import Foundation
import OmiCore
import BackgroundTasks

struct UploadFeedback {
    let sentBytes: Int64
    let totalBytes: Int64
    let remainingBytes: Int64
    let title: String
    let estimatedSeconds: TimeInterval?
    var fraction: Double? { totalBytes > 0 ? min(1, Double(sentBytes) / Double(totalBytes)) : nil }
}

@MainActor
final class BackgroundUploader: NSObject, @preconcurrency URLSessionDataDelegate, @preconcurrency URLSessionTaskDelegate {
    static var shared: BackgroundUploader?
    static var backgroundCompletion: (() -> Void)?
    private let store: DurableStore
    private var session: URLSession!
    private var active: [Int: URLSessionTask] = [:]
    private var policyCancellations = Set<Int>()
    private var responses: [Int: Data] = [:]
    private var rates: [Int: TransferRate] = [:]
    private var waitingForConnectivity = Set<Int>()
    private var fileSizes: [UUID: Int64] = [:]
    private var taskEndpoints: [Int: UUID] = [:]
    private var attemptedEndpoints: [UUID: Set<UUID>] = [:]
    var client: BackendClient? {
        didSet { enforceDestination(); pump() }
    }
    var onChange: (() -> Void)?
    var onProgress: (() -> Void)?
    var onError: ((String) -> Void)?
    private var uploadHolds = UserInitiatedUploadHolds()
    private var immediateTasks: [UUID: Task<Void, Never>] = [:]
    private var immediateSessions: [UUID: URLSession] = [:]
    private var immediateItems: [UUID: Set<UUID>] = [:]
    private var immediateRetryAt: [UUID: TimeInterval] = [:]
    private var restoring = true
    private var uploadWiFiOnly: Bool

    init(store: DurableStore, uploadWiFiOnly: Bool) {
        self.store = store
        self.uploadWiFiOnly = uploadWiFiOnly
        super.init()
        let configuration = URLSessionConfiguration.background(withIdentifier: "com.daythread.capture-uploads")
        configuration.sessionSendsLaunchEvents = true; configuration.isDiscretionary = false
        configuration.waitsForConnectivity = true; configuration.allowsCellularAccess = true
        configuration.timeoutIntervalForResource = 7 * 24 * 3600
        session = URLSession(configuration: configuration, delegate: self, delegateQueue: .main)
        Self.shared = self
        session.getAllTasks { [weak self] tasks in
            Task { @MainActor in
                guard let self else { return }
                for task in tasks where task.state != .completed {
                    if task.taskDescription.flatMap(UUID.init(uuidString:)) != nil {
                        self.active[task.taskIdentifier] = task
                    }
                }
                self.enforceNetworkPolicy()
                self.enforceDestination()
                self.restoring = false; self.pump()
            }
        }
    }
    func setUploadWiFiOnly(_ value: Bool) {
        guard uploadWiFiOnly != value else { return }
        uploadWiFiOnly = value
        for session in immediateSessions.values { session.invalidateAndCancel() }
        enforceNetworkPolicy()
        pump()
    }
    private func enforceNetworkPolicy() {
        // Existing and restored tasks retain their original request policy. Requeue them
        // under the current preference rather than mutating the session's copied config.
        for task in active.values where task.state != .completed {
            if task.originalRequest?.allowsCellularAccess != !uploadWiFiOnly {
                policyCancellations.insert(task.taskIdentifier)
                task.cancel()
            }
        }
    }
    private func enforceDestination() {
        for (id, session) in immediateSessions where store.items.first(where: { $0.id == id })?.destinationID != client?.destinationIdentity {
            session.invalidateAndCancel()
        }
        for task in active.values where task.state != .completed {
            guard let client, let id = task.taskDescription.flatMap(UUID.init(uuidString:)),
                  let item = store.items.first(where: { $0.id == id }),
                  item.destinationID == client.destinationIdentity, !item.acknowledged,
                  task.originalRequest?.value(forHTTPHeaderField: "X-Daythread-Destination") == client.destinationIdentity,
                  task.originalRequest?.value(forHTTPHeaderField: "X-Daythread-Session") == client.authorization?.sessionID.uuidString,
                  task.originalRequest?.url == uploadURL(for: item, client: client),
                  task.originalRequest?.value(forHTTPHeaderField: "X-Omi-Profile") == item.resolvedProfileID else {
                policyCancellations.insert(task.taskIdentifier); task.cancel(); continue
            }
        }
    }
    private func uploadURL(for item: OutboxItem, client: BackendClient) -> URL {
        switch item.kind {
        case .audio:
            var query = ["device_id": item.deviceID, "started_at": DurableStore.stamp(item.capturedAt)]
            if let location = item.location { query.merge(location.uploadQuery) { _, new in new } }
            return client.url("v1/audio/\(item.id.uuidString)", query: query)
        case .event: return client.url("v1/events", query: ["receipt_only": "true"])
        case .gap: return client.url("v1/gaps")
        case .generation: return client.url("v1/jobs")
        case .captureState: return client.url("v1/capture-state/\(item.id.uuidString)")
        }
    }
    var observedState: String {
        if !active.isEmpty { return "active" }
        if store.pending.contains(where: { $0.error != nil }) { return "retrying" }
        return store.pending.isEmpty ? "idle" : "waiting"
    }
    func feedback(profileID: String) -> UploadFeedback? {
        let pending = store.pending.filter { $0.destinationID == client?.destinationIdentity && $0.resolvedProfileID == profileID }
        guard !pending.isEmpty else { return nil }
        var remaining: Int64 = 0
        for item in pending {
            if fileSizes[item.id] == nil {
                let file = store.file(item.id, extension: item.kind == .audio ? "wav" : "json")
                if let size = try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize {
                    fileSizes[item.id] = Int64(size)
                }
            }
            remaining += fileSizes[item.id] ?? 0
        }
        let ids = Set(pending.map(\.id))
        guard let task = active.values.first(where: { $0.taskDescription.flatMap(UUID.init(uuidString:)).map(ids.contains) == true }),
              let id = task.taskDescription.flatMap(UUID.init(uuidString:)) else {
            let retry = pending.map(\.retryAt).filter { $0 > Date() }.min()
            let title = retry.map { L10n.source("Retry scheduled at \($0.formatted(.dateTime.locale(L10n.locale).hour().minute()))") } ?? L10n.source("Waiting to upload")
            return UploadFeedback(sentBytes: 0, totalBytes: 0, remainingBytes: remaining, title: title, estimatedSeconds: nil)
        }
        let total = max(task.countOfBytesExpectedToSend, fileSizes[id] ?? 0)
        let sent = max(0, min(task.countOfBytesSent, max(0, total)))
        remaining = max(0, remaining - sent)
        let confirming = total > 0 && sent >= total
        let waiting = waitingForConnectivity.contains(task.taskIdentifier)
        let title = waiting ? (uploadWiFiOnly ? L10n.source("Waiting for Wi-Fi") : L10n.source("Waiting for connection")) : confirming ? L10n.source("Waiting for server confirmation") : L10n.source("Uploading")
        let estimate = waiting || confirming ? nil : rates[task.taskIdentifier]?.remainingSeconds(for: remaining, at: ProcessInfo.processInfo.systemUptime)
        return UploadFeedback(sentBytes: sent, totalBytes: total, remainingBytes: remaining, title: title, estimatedSeconds: estimate)
    }
    func holdGeneration(_ id: UUID) {
        uploadHolds.hold(id, at: ProcessInfo.processInfo.systemUptime)
        Task { [weak self] in
            do { try await Task.sleep(for: .seconds(8)) } catch { return }
            self?.releaseGeneration(id)
        }
    }
    func releaseGeneration(_ id: UUID) { uploadHolds.release(id); pump() }
    func pump() {
        guard let client else { return }
        let now = ProcessInfo.processInfo.systemUptime
        let held = uploadHolds.active(at: now)
        // A regular session uses this BLE wake window; the background session may be discretionary.
        for item in store.pending(destinationID: client.destinationIdentity) where item.prioritizesUpload &&
            !held.contains(item.id) && immediateTasks[item.id] == nil &&
            (immediateRetryAt[item.id] ?? 0) <= now &&
            store.nextSubmissionUpload(item.id, destinationID: client.destinationIdentity) != nil {
            if immediateTasks.count >= 2 { break }
            startImmediateSubmission(item.id, client: client)
        }
        let reserved = immediateItems.values.reduce(into: held) { $0.formUnion($1) }
        guard !restoring, active.isEmpty,
              let item = store.nextUpload(destinationID: client.destinationIdentity, excluding: reserved) else { return }
        let file: URL
        do { file = try store.uploadFile(for: item) }
        catch {
            do { try store.failed(item.id, message: L10n.error(error)) }
            catch { onError?(L10n.source("Could not persist upload retry: \(L10n.error(error))")) }
            onError?(L10n.error(error)); Self.scheduleRetry(); return
        }
        var request = URLRequest(url: uploadURL(for: item, client: client))
        request.setValue(item.resolvedProfileID, forHTTPHeaderField: "X-Omi-Profile")
        request.allowsCellularAccess = !uploadWiFiOnly
        request.httpMethod = item.kind == .audio || item.kind == .captureState ? "PUT" : "POST"
        request.setValue(item.kind == .audio ? "audio/wav" : "application/json", forHTTPHeaderField: "Content-Type")
        do { try client.authorize(&request) }
        catch { onError?(L10n.error(error)); return }
        let task = session.uploadTask(with: request, fromFile: file)
        var rate = TransferRate(); rate.observe(sentBytes: 0, at: ProcessInfo.processInfo.systemUptime)
        rates[task.taskIdentifier] = rate
        task.taskDescription = item.id.uuidString
        if let endpointID = client.activeEndpoint?.id {
            taskEndpoints[task.taskIdentifier] = endpointID
            attemptedEndpoints[item.id, default: []].insert(endpointID)
        }
        active[task.taskIdentifier] = task; task.resume()
        onProgress?()
    }
    private func startImmediateSubmission(_ id: UUID, client: BackendClient) {
        guard let snapshot = try? client.snapshot(), let request = store.items.first(where: { $0.id == id }) else { return }
        immediateRetryAt[id] = ProcessInfo.processInfo.systemUptime + 30
        immediateItems[id] = Set([id] + store.audioDependencies(for: request).map(\.id))
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        configuration.allowsCellularAccess = !uploadWiFiOnly
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 20
        configuration.urlCache = nil; configuration.httpCookieStorage = nil
        let session = URLSession(configuration: configuration, delegate: ManagedRedirectPolicy.shared, delegateQueue: nil)
        immediateSessions[id] = session
        let execution = ClickBackgroundExecution(pending: [.submission], maximumSeconds: 25)
        execution.onExpiration = { [weak self] in
            self?.immediateTasks[id]?.cancel()
            session.invalidateAndCancel()
        }
        immediateTasks[id] = Task(priority: .userInitiated) { [weak self] in
            guard let self else { execution.complete(.submission); return }
            let deadline = Task {
                do { try await Task.sleep(for: .seconds(20)) } catch { return }
                session.invalidateAndCancel()
            }
            defer {
                deadline.cancel(); session.invalidateAndCancel()
                immediateTasks.removeValue(forKey: id); immediateSessions.removeValue(forKey: id)
                immediateItems.removeValue(forKey: id)
                execution.complete(.submission)
                onChange?(); pump(); Self.scheduleRetry()
            }
            do {
                let accepted = try await UserInitiatedUpload.drain(store: store, submissionID: id, destinationID: snapshot.destinationIdentity) { item, file in
                    guard self.client?.destinationIdentity == snapshot.destinationIdentity,
                          self.client?.authorization?.sessionID == snapshot.authorization?.sessionID else { throw CancellationError() }
                    var lastError: Error?
                    let endpoints = snapshot.router.orderedCandidates()
                    for (index, endpoint) in endpoints.enumerated() {
                        do {
                            try Task.checkCancellation()
                            snapshot.router.setActive(endpoint.id)
                            var request = URLRequest(url: self.uploadURL(for: item, client: snapshot))
                            request.httpMethod = item.kind == .audio || item.kind == .captureState ? "PUT" : "POST"
                            request.timeoutInterval = 5; request.allowsCellularAccess = !self.uploadWiFiOnly
                            request.setValue(item.resolvedProfileID, forHTTPHeaderField: "X-Omi-Profile")
                            request.setValue(item.kind == .audio ? "audio/wav" : "application/json", forHTTPHeaderField: "Content-Type")
                            try snapshot.authorize(&request)
                            let (data, response) = try await session.upload(for: request, fromFile: file)
                            try snapshot.validateResponse(response, data: data)
                            snapshot.router.markSuccess(endpoint.id)
                            return data
                        } catch {
                            lastError = error
                            guard index < endpoints.count - 1, BackendClient.shouldTryNextEndpoint(error) else { throw error }
                        }
                    }
                    throw lastError ?? CaptureError.invalidReceipt
                } acknowledged: { item in
                    self.fileSizes.removeValue(forKey: item.id)
                    // The OS may still own an older discretionary transfer for this exact ID.
                    for task in self.active.values where task.taskDescription.flatMap(UUID.init(uuidString:)) == item.id {
                        self.policyCancellations.insert(task.taskIdentifier); task.cancel()
                    }
                }
                if accepted { immediateRetryAt.removeValue(forKey: id) }
            } catch {
                // Original files and IDs remain queued for the durable background path.
                // The per-request cooldown prevents a failed quick handoff from spinning.
            }
        }
    }
    func retry() {
        do { try store.retryNow(); pump() } catch { onError?(L10n.error(error)) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        // Audio acknowledgements are small; never accumulate an unbounded error response.
        let remaining = 1_000_000 - (responses[dataTask.taskIdentifier]?.count ?? 0)
        responses[dataTask.taskIdentifier, default: Data()].append(data.prefix(max(0, remaining)))
        if data.count > remaining { dataTask.cancel() }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        waitingForConnectivity.remove(task.taskIdentifier)
        rates[task.taskIdentifier, default: TransferRate()].observe(sentBytes: totalBytesSent, at: ProcessInfo.processInfo.systemUptime)
        onProgress?()
    }
    func urlSession(_ session: URLSession, taskIsWaitingForConnectivity task: URLSessionTask) {
        waitingForConnectivity.insert(task.taskIdentifier)
        rates.removeValue(forKey: task.taskIdentifier)
        onProgress?()
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let id = task.taskDescription.flatMap(UUID.init(uuidString:)) else { return }
        let data = responses.removeValue(forKey: task.taskIdentifier) ?? Data()
        defer {
            active.removeValue(forKey: task.taskIdentifier); rates.removeValue(forKey: task.taskIdentifier)
            waitingForConnectivity.remove(task.taskIdentifier)
            taskEndpoints.removeValue(forKey: task.taskIdentifier)
            onChange?(); pump(); Self.scheduleRetry()
        }
        // A preference change preserves the original outbox item and does not count as
        // a failed upload. Retry uses the same ID, even if the server already received it.
        if policyCancellations.remove(task.taskIdentifier) != nil { return }
        if store.items.first(where: { $0.id == id })?.acknowledged == true { return }
        do {
            if let error { throw error }
            guard let response = task.response else { throw BackendFailure(message: L10n.source("Upload ended without an acknowledgement")) }
            client?.authorization?.expireIfNeeded(response)
            try BackendClient.validate(response, data: data)
            guard let item = store.items.first(where: { $0.id == id }) else { throw CaptureError.invalidReceipt }
            try UploadReceipt.validate(data, for: item)
            try store.acknowledge(id)
            fileSizes.removeValue(forKey: id)
            attemptedEndpoints.removeValue(forKey: id)
        } catch {
            // A background URLSession task has one destination. On a transport/TLS
            // failure, spend the remaining endpoints in this ordered cycle before
            // applying the durable exponential backoff. This bounds retries and
            // keeps the upload's original ID/body intact.
            if BackendClient.shouldTryNextEndpoint(error), let client, let failedEndpoint = taskEndpoints[task.taskIdentifier],
               let next = client.router.orderedCandidates().first(where: { $0.id != failedEndpoint && !(attemptedEndpoints[id] ?? []).contains($0.id) }) {
                client.router.setActive(next.id)
                attemptedEndpoints[id, default: []].insert(next.id)
                do { try store.retryNow(id) }
                catch { onError?(L10n.source("Could not persist fallback upload retry: \(L10n.error(error))")) }
            } else {
                attemptedEndpoints.removeValue(forKey: id)
                do { try store.failed(id, message: L10n.error(error)) }
                catch { onError?(L10n.source("Could not persist upload retry: \(L10n.error(error))")) }
                onError?(L10n.error(error))
            }
        }
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        Self.backgroundCompletion?(); Self.backgroundCompletion = nil
    }
    static func scheduleRetry() {
        guard let uploader = shared, let client = uploader.client,
              uploader.active.isEmpty,
              !uploader.store.pending(destinationID: client.destinationIdentity).isEmpty else {
            BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: "com.daythread.sync")
            return
        }
        let request = BGProcessingTaskRequest(identifier: "com.daythread.sync")
        request.requiresNetworkConnectivity = true; request.requiresExternalPower = false
        request.earliestBeginDate = Date().addingTimeInterval(60)
        do { try BGTaskScheduler.shared.submit(request) }
        catch { /* Foreground retries and background URLSession remain active if iOS declines scheduling. */ }
    }
}

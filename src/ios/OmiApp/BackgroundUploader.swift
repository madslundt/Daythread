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
    var client: BackendClient? {
        didSet { enforceDestination(); pump() }
    }
    var onChange: (() -> Void)?
    var onProgress: (() -> Void)?
    var onError: ((String) -> Void)?
    private var restoring = true
    private var uploadWiFiOnly: Bool

    init(store: DurableStore, uploadWiFiOnly: Bool) {
        self.store = store
        self.uploadWiFiOnly = uploadWiFiOnly
        super.init()
        let configuration = URLSessionConfiguration.background(withIdentifier: "local.omi.capture-uploads")
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
        guard let client else { return }
        for task in active.values where task.state != .completed {
            guard let id = task.taskDescription.flatMap(UUID.init(uuidString:)),
                  let item = store.items.first(where: { $0.id == id }) else { continue }
            let expected = uploadURL(for: item, client: client)
            let originalProfile = task.originalRequest?.value(forHTTPHeaderField: "X-Omi-Profile") ?? "default"
            if task.originalRequest?.url != expected || originalProfile != item.resolvedProfileID {
                policyCancellations.insert(task.taskIdentifier)
                task.cancel()
            }
        }
    }
    private func uploadURL(for item: OutboxItem, client: BackendClient) -> URL {
        switch item.kind {
        case .audio:
            return client.url("v1/audio/\(item.id.uuidString)", query: ["device_id": item.deviceID, "started_at": DurableStore.stamp(item.capturedAt)])
        case .event: return client.url("v1/events", query: ["receipt_only": "true"])
        case .gap: return client.url("v1/gaps")
        case .captureState: return client.url("v1/capture-state/\(item.id.uuidString)")
        }
    }
    var observedState: String {
        if !active.isEmpty { return "active" }
        if store.pending.contains(where: { $0.error != nil }) { return "retrying" }
        return store.pending.isEmpty ? "idle" : "waiting"
    }
    func feedback(profileID: String) -> UploadFeedback? {
        let pending = store.pending.filter { $0.resolvedProfileID == profileID }
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
            let title = retry.map { "Retry scheduled at \($0.formatted(date: .omitted, time: .shortened))" } ?? "Waiting to upload"
            return UploadFeedback(sentBytes: 0, totalBytes: 0, remainingBytes: remaining, title: title, estimatedSeconds: nil)
        }
        let total = max(task.countOfBytesExpectedToSend, fileSizes[id] ?? 0)
        let sent = max(0, min(task.countOfBytesSent, max(0, total)))
        remaining = max(0, remaining - sent)
        let confirming = total > 0 && sent >= total
        let waiting = waitingForConnectivity.contains(task.taskIdentifier)
        let title = waiting ? (uploadWiFiOnly ? "Waiting for Wi-Fi" : "Waiting for connection") : confirming ? "Waiting for server confirmation" : "Uploading"
        let estimate = waiting || confirming ? nil : rates[task.taskIdentifier]?.remainingSeconds(for: remaining, at: ProcessInfo.processInfo.systemUptime)
        return UploadFeedback(sentBytes: sent, totalBytes: total, remainingBytes: remaining, title: title, estimatedSeconds: estimate)
    }
    func pump() {
        guard !restoring, active.isEmpty, let client,
              let item = store.pending.first(where: { $0.retryAt <= Date() }) else { return }
        let file: URL
        do { file = try store.uploadFile(for: item) }
        catch {
            do { try store.failed(item.id, message: error.localizedDescription) }
            catch { onError?("Could not persist upload retry: \(error.localizedDescription)") }
            onError?(error.localizedDescription); Self.scheduleRetry(); return
        }
        var request = URLRequest(url: uploadURL(for: item, client: client))
        request.setValue(item.resolvedProfileID, forHTTPHeaderField: "X-Omi-Profile")
        request.allowsCellularAccess = !uploadWiFiOnly
        request.httpMethod = item.kind == .audio || item.kind == .captureState ? "PUT" : "POST"
        request.setValue(item.kind == .audio ? "audio/wav" : "application/json", forHTTPHeaderField: "Content-Type")
        let task = session.uploadTask(with: request, fromFile: file)
        var rate = TransferRate(); rate.observe(sentBytes: 0, at: ProcessInfo.processInfo.systemUptime)
        rates[task.taskIdentifier] = rate
        task.taskDescription = item.id.uuidString; active[task.taskIdentifier] = task; task.resume()
        onProgress?()
    }
    func retry() {
        do { try store.retryNow(); pump() } catch { onError?(error.localizedDescription) }
    }
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
            onChange?(); pump(); Self.scheduleRetry()
        }
        // A preference change preserves the original outbox item and does not count as
        // a failed upload. Retry uses the same ID, even if the server already received it.
        if policyCancellations.remove(task.taskIdentifier) != nil { return }
        do {
            if let error { throw error }
            guard let response = task.response else { throw BackendFailure(message: "Upload ended without an acknowledgement") }
            try BackendClient.validate(response, data: data)
            guard let item = store.items.first(where: { $0.id == id }) else { throw CaptureError.invalidReceipt }
            try UploadReceipt.validate(data, for: item)
            try store.acknowledge(id)
            fileSizes.removeValue(forKey: id)
        } catch {
            do { try store.failed(id, message: error.localizedDescription) }
            catch { onError?("Could not persist upload retry: \(error.localizedDescription)") }
            onError?(error.localizedDescription)
        }
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        Self.backgroundCompletion?(); Self.backgroundCompletion = nil
    }
    static func scheduleRetry() {
        let request = BGProcessingTaskRequest(identifier: "local.omi.sync")
        request.requiresNetworkConnectivity = true; request.requiresExternalPower = false
        request.earliestBeginDate = Date().addingTimeInterval(60)
        do { try BGTaskScheduler.shared.submit(request) }
        catch { /* Foreground retries and background URLSession remain active if iOS declines scheduling. */ }
    }
}

import Foundation
import Darwin

public extension Notification.Name {
    static let backgroundRecordingImportChanged = Notification.Name("BackgroundRecordingImport.changed")
}

/// A file-backed handoff from the share extension to iOS. Each recording has its
/// own session so a later share cannot take ownership of an earlier transfer.
@MainActor
public final class BackgroundRecordingImport: NSObject, @preconcurrency URLSessionDataDelegate {
    public static let sessionPrefix = "com.daythread.share-import."
    public static let groupID = "group.com.daythread.app"
    private static var owners: [UUID: BackgroundRecordingImport] = [:]

    public struct JobSummary: Identifiable, Sendable {
        public let id: UUID
        public let sourceName: String
        public let error: String?
        public let uploadedParts: Int
        public let totalParts: Int
    }

    private var job: BackgroundImportJob
    private let directory: URL
    private var session: URLSession!
    private var responses: [Int: Data] = [:]
    private var active = Set<Int>()
    private var metadataTask: Task<Void, Never>?
    private var completion: (() -> Void)?
    private var eventsFinished = false
    private var finalized = false
    private var restoring = false
    private var lease: BackgroundImportLease?

    @discardableResult
    public static func enqueue(converted: ConvertedRecording, plan: RecordingUploadPlan, sourceName: String,
                               configuration: BackendEndpointConfiguration, profileID: String,
                               tagIDs: [String] = [], tagNames: [String] = [], locationData: Data? = nil,
                               wiFiOnly: Bool = false) throws -> UUID {
        let root = try sharedRoot()
        let job = try BackgroundImportJob.stage(converted: converted, plan: plan, sourceName: sourceName,
            configuration: configuration, profileID: profileID, tagIDs: tagIDs, tagNames: tagNames,
            locationData: locationData, wiFiOnly: wiFiOnly, root: root)
        let directory = job.directory(in: root)
        guard let lease = try BackgroundImportLease.claim(in: directory) else { return job.id }
        let owner = BackgroundRecordingImport(job: job, directory: directory, lease: lease)
        owners[job.id] = owner
        // Persistence is the acceptance boundary. A scheduling failure remains a
        // queued job for retry; reporting it as a rejected handoff would let the
        // extension enqueue a second copy of the same accepted upload plan.
        do { try owner.schedule(existing: []) }
        catch { owner.recordFailure(error) }
        return job.id
    }

    public static func pendingJobs(archiveIdentity: String, profileID: String) -> [JobSummary] {
        guard let root = try? sharedRoot() else { return [] }
        return jobs(in: root).filter { $0.configuration.archiveIdentity == archiveIdentity && $0.profileID == profileID }
            .map { JobSummary(id: $0.id, sourceName: $0.sourceName, error: $0.error,
                              uploadedParts: $0.acknowledged.count, totalParts: $0.samples.count) }
    }

    /// Scheduled sessions belong to the extension until iOS delivers their
    /// background events. Only interrupted preparation and recorded failures are
    /// reclaimed here; simply opening the app must not steal a live session.
    public static func retryPending(archiveIdentity: String, profileID: String) throws {
        let root = try sharedRoot()
        for job in jobs(in: root) where job.configuration.archiveIdentity == archiveIdentity && job.profileID == profileID {
            guard job.state != .scheduled else { continue }
            if let owner = owners[job.id] { owner.restoreTasks() }
            else {
                let directory = job.directory(in: root)
                guard let lease = try BackgroundImportLease.claim(in: directory) else { continue }
                let current = try BackgroundImportJob.load(from: directory)
                guard current.state != .scheduled else { continue }
                let owner = BackgroundRecordingImport(job: current, directory: directory, lease: lease)
                owners[job.id] = owner
                owner.restoreTasks()
            }
        }
    }

    /// Returns false for sessions owned by another uploader.
    @discardableResult
    public static func handleEvents(identifier: String, completion: @escaping () -> Void) -> Bool {
        guard identifier.hasPrefix(sessionPrefix) else { return false }
        guard let id = UUID(uuidString: String(identifier.dropFirst(sessionPrefix.count))),
              let root = try? sharedRoot() else { completion(); return true }
        if let owner = owners[id] { owner.completion = completion; owner.finishEventsIfReady(); return true }
        do {
            let directory = root.appendingPathComponent(id.uuidString, isDirectory: true)
            guard let lease = try BackgroundImportLease.claim(in: directory) else {
                Task { try? await Task.sleep(for: .milliseconds(250)); handleEvents(identifier: identifier, completion: completion) }
                return true
            }
            let owner = BackgroundRecordingImport(job: try BackgroundImportJob.load(from: directory), directory: directory, lease: lease)
            owner.completion = completion
            owners[id] = owner
            owner.restoreTasks(reschedule: false)
        } catch { completion() }
        return true
    }

    private init(job: BackgroundImportJob, directory: URL, lease: BackgroundImportLease) {
        self.job = job
        self.directory = directory
        self.lease = lease
        super.init()
        let configuration = URLSessionConfiguration.background(withIdentifier: Self.sessionPrefix + job.id.uuidString)
        configuration.sharedContainerIdentifier = Self.groupID
        configuration.sessionSendsLaunchEvents = true
        configuration.isDiscretionary = false
        configuration.waitsForConnectivity = true
        configuration.allowsCellularAccess = !job.wiFiOnly
        configuration.timeoutIntervalForResource = 7 * 24 * 3600
        configuration.httpMaximumConnectionsPerHost = 3
        session = URLSession(configuration: configuration, delegate: self, delegateQueue: .main)
    }

    private func restoreTasks(reschedule: Bool = true) {
        guard !restoring, metadataTask == nil else { return }
        restoring = true
        session.getAllTasks { [weak self] tasks in
            Task { @MainActor in
                guard let self else { return }
                self.active.formUnion(tasks.filter { $0.state != .completed }.map(\.taskIdentifier))
                do { if reschedule { try self.schedule(existing: tasks) } }
                catch { self.recordFailure(error) }
                self.restoring = false
                self.finishMetadataIfReady()
                self.releaseLease()
                self.finishEventsIfReady()
            }
        }
    }

    private func schedule(existing: [URLSessionTask]) throws {
        guard metadataTask == nil, try claimLease() else { return }
        defer { releaseLease() }
        var scheduled = Set<Int>()
        for task in existing where task.state != .completed {
            guard let index = task.taskDescription.flatMap(Int.init), job.samples.indices.contains(index) else { continue }
            scheduled.insert(index)
            active.insert(task.taskIdentifier)
            if task.state == .suspended { task.resume() }
        }
        // Validate every request/file before starting any new transfer.
        let pending = try job.samples.indices.filter { !job.acknowledged.contains($0) && !scheduled.contains($0) }.map { index in
            let file = directory.appendingPathComponent("part-\(index).wav")
            guard FileManager.default.fileExists(atPath: file.path) else { throw CocoaError(.fileNoSuchFile) }
            return (index, file, try job.uploadRequest(for: index))
        }
        if !pending.isEmpty || !scheduled.isEmpty { eventsFinished = false }
        job.error = nil
        job.state = .prepared
        try job.save(in: directory)
        for (index, file, request) in pending {
            let task = session.uploadTask(with: request, fromFile: file)
            task.taskDescription = String(index)
            active.insert(task.taskIdentifier)
            task.resume()
        }
        job.state = .scheduled
        try job.save(in: directory)
        notifyChange()
        finishMetadataIfReady()
    }

    public func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard session === self.session else { return }
        guard (responses[dataTask.taskIdentifier]?.count ?? 0) + data.count <= 1_048_576 else {
            dataTask.cancel(); return
        }
        responses[dataTask.taskIdentifier, default: Data()].append(data)
    }

    public func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                           newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        // This blocks redirects for optional-details requests in ephemeral
        // sessions. iOS owns redirect handling for background file transfers;
        // their final response URL is checked before accepting a receipt.
        completionHandler(nil)
    }

    public func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard session === self.session, !finalized else { return }
        active.insert(task.taskIdentifier) // Includes callbacks deferred while another owner holds the lease.
        if restoring {
            Task { try? await Task.sleep(for: .milliseconds(250)); urlSession(session, task: task, didCompleteWithError: error) }
            return
        }
        do {
            guard try claimLease() else {
                Task { try? await Task.sleep(for: .milliseconds(250)); urlSession(session, task: task, didCompleteWithError: error) }
                return
            }
        } catch { active.remove(task.taskIdentifier); recordFailure(error); return }
        defer { releaseLease() }
        active.remove(task.taskIdentifier)
        let data = responses.removeValue(forKey: task.taskIdentifier) ?? Data()
        do {
            if let error { throw error }
            guard let index = task.taskDescription.flatMap(Int.init) else { throw CaptureError.invalidReceipt }
            guard task.response?.url == task.originalRequest?.url else { throw CaptureError.invalidReceipt }
            try job.accept(data, response: task.response, part: index)
            if active.isEmpty, job.acknowledged.count < job.samples.count, job.error != nil { job.state = .failed }
            try job.save(in: directory)
            notifyChange()
        } catch { recordFailure(error) }
        finishMetadataIfReady()
        finishEventsIfReady()
    }

    public func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        eventsFinished = true
        if !restoring, active.isEmpty, job.acknowledged.count < job.samples.count {
            recordFailure(RecordingImportError(job.error ?? AppLocalization.source("The recording could not be uploaded. Open Daythread to retry.")))
        }
        finishMetadataIfReady()
        finishEventsIfReady()
    }

    public func urlSession(_ session: URLSession, didBecomeInvalidWithError error: Error?) {
        guard session === self.session, let error, !finalized else { return }
        active.removeAll()
        eventsFinished = true
        recordFailure(error)
        finishEventsIfReady()
    }

    private func finishMetadataIfReady() {
        guard !restoring, !finalized, active.isEmpty, job.acknowledged.count == job.samples.count,
              metadataTask == nil, (try? claimLease()) == true else { return }
        // Ordinary metadata requests cannot survive extension termination. Mark
        // this phase durably so the next foreground can finish it even if iOS
        // has already delivered every background audio event to the extension.
        job.state = .details
        do { try job.save(in: directory) }
        catch { recordFailure(error); return }
        metadataTask = Task {
            do {
                for index in job.samples.indices where !job.detailsSaved.contains(index) {
                    try await applyDetails(to: job.plan.parts[index].id)
                    job.detailsSaved.insert(index)
                    try job.save(in: directory)
                }
                guard job.canReleaseAudio else { throw CaptureError.invalidReceipt }
                try FileManager.default.removeItem(at: directory)
                finalized = true
                notifyChange()
            } catch { recordFailure(error) }
            metadataTask = nil
            releaseLease()
            finishEventsIfReady()
        }
    }

    private func applyDetails(to id: UUID) async throws {
        let path = "v1/sources/\(id.uuidString.lowercased())"
        if !job.tagIDs.isEmpty || !job.tagNames.isEmpty {
            var selection = try await send(path + "/tags")
            let changes = job.tagIDs.map { ["tag_id": $0] } + job.tagNames.map { ["name": $0] }
            for change in changes {
                let body: [String: Any] = ["expected_revision": selection["revision"] as? String ?? "",
                    "expected_membership": selection["membership"] as? String ?? "", "action": "add"]
                    .merging(change) { _, value in value }
                selection = try await send(path + "/tags", body: body)
            }
        }
        if let data = job.locationData {
            let location = try JSONSerialization.jsonObject(with: data)
            let current = try await send(path + "/location")
            _ = try await send(path + "/location", body: ["expected_version": current["version"] as? Int ?? 0, "location": location])
        }
    }

    private func send(_ path: String, body: [String: Any]? = nil) async throws -> [String: Any] {
        var request = try job.request(path)
        request.httpMethod = body == nil ? "GET" : "PUT"
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.allowsCellularAccess = !job.wiFiOnly
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: .main)
        defer { session.finishTasksAndInvalidate() }
        let (data, response) = try await session.data(for: request)
        try BackgroundImportJob.check(response, data: data)
        guard let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw CaptureError.invalidReceipt }
        return result
    }

    private func recordFailure(_ error: Error) {
        guard !finalized else { return }
        do {
            guard try claimLease() else {
                Task { try? await Task.sleep(for: .milliseconds(250)); recordFailure(error) }
                return
            }
        } catch {
            job.error = error.localizedDescription
            active.removeAll(); eventsFinished = true
            notifyChange(); finishEventsIfReady()
            return // Permanent I/O/manifest errors must not create a retry loop.
        }
        defer { releaseLease() }
        job.error = error.localizedDescription
        job.state = active.isEmpty ? .failed : .scheduled
        do { try job.save(in: directory) }
        catch { job.error = error.localizedDescription }
        notifyChange()
    }

    private func finishEventsIfReady() {
        guard !restoring, active.isEmpty, metadataTask == nil,
              eventsFinished || finalized || (job.state == .failed && completion == nil) else { return }
        let callback = completion
        completion = nil
        releaseLease()
        callback?()
        session.finishTasksAndInvalidate()
        Self.owners.removeValue(forKey: job.id)
    }

    private func notifyChange() { NotificationCenter.default.post(name: .backgroundRecordingImportChanged, object: nil) }

    private func claimLease() throws -> Bool {
        if lease != nil { return true }
        guard let claimed = try BackgroundImportLease.claim(in: directory) else { return false }
        let latest = try BackgroundImportJob.load(from: directory)
        lease = claimed
        job = latest
        return true
    }
    private func releaseLease() { if metadataTask == nil && !restoring { lease = nil } }

    private static func sharedRoot() throws -> URL {
        guard let group = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: groupID) else {
            throw RecordingImportError(AppLocalization.source("Daythread’s shared audio storage is unavailable."))
        }
        return group.appendingPathComponent("BackgroundRecordingImports", isDirectory: true)
    }

    private static func jobs(in root: URL) -> [BackgroundImportJob] {
        let files = (try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
        return files.filter { UUID(uuidString: $0.lastPathComponent) != nil }.compactMap { try? BackgroundImportJob.load(from: $0) }
    }
}

/// Nonblocking process coordination. Closing the descriptor releases ownership,
/// including when iOS terminates an extension during a metadata request.
final class BackgroundImportLease {
    private let descriptor: Int32
    private init(_ descriptor: Int32) { self.descriptor = descriptor }
    deinit { close(descriptor) }
    static func claim(in directory: URL) throws -> BackgroundImportLease? {
        let descriptor = open(directory.appendingPathComponent(".owner-lock").path, O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            let code = errno
            close(descriptor)
            if code == EWOULDBLOCK { return nil }
            throw POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO)
        }
        return BackgroundImportLease(descriptor)
    }
}

/// The durable seam shared by extension handoff, delegate recovery and retries.
/// Audio is removed only after every matching receipt and optional detail write.
struct BackgroundImportJob: Codable {
    enum State: String, Codable { case prepared, scheduled, details, failed }
    let id: UUID
    let sourceName: String
    let configuration: BackendEndpointConfiguration
    let profileID: String
    let plan: RecordingUploadPlan
    let samples: [Int]
    let tagIDs: [String]
    let tagNames: [String]
    let locationData: Data?
    let wiFiOnly: Bool
    var acknowledged: Set<Int>
    var detailsSaved: Set<Int> = []
    var state = State.prepared
    var error: String?

    var canReleaseAudio: Bool {
        acknowledged == Set(samples.indices) && detailsSaved == Set(samples.indices)
    }

    static func stage(converted: ConvertedRecording, plan: RecordingUploadPlan, sourceName: String,
                      configuration: BackendEndpointConfiguration, profileID: String, tagIDs: [String],
                      tagNames: [String], locationData: Data?, wiFiOnly: Bool, root: URL) throws -> Self {
        try plan.validate(digests: converted.parts.map(\.digest), sampleCount: converted.sampleCount)
        guard !profileID.isEmpty else { throw RecordingImportError(AppLocalization.source("Choose a profile in Settings first.")) }
        let job = Self(id: UUID(), sourceName: sourceName, configuration: configuration, profileID: profileID,
            plan: plan, samples: converted.parts.map(\.samples), tagIDs: tagIDs, tagNames: tagNames,
            locationData: locationData, wiFiOnly: wiFiOnly,
            acknowledged: Set(plan.parts.indices.filter { plan.parts[$0].uploaded }))
        _ = try job.request("v1/recording-uploads/check")
        if let locationData {
            guard try JSONSerialization.jsonObject(with: locationData) is [String: Any] else { throw CaptureError.invalidReceipt }
        }
        let folder = job.directory(in: root)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        do {
            try protect(folder)
            for (index, part) in converted.parts.enumerated() {
                let target = folder.appendingPathComponent("part-\(index).wav")
                try FileManager.default.moveItem(at: part.url, to: target)
                try protect(target)
            }
            try job.save(in: folder)
            return job
        } catch {
            try? FileManager.default.removeItem(at: folder)
            throw error
        }
    }

    func directory(in root: URL) -> URL { root.appendingPathComponent(id.uuidString, isDirectory: true) }

    func save(in directory: URL) throws {
        var options: Data.WritingOptions = .atomic
        #if os(iOS)
        options.insert(.completeFileProtectionUntilFirstUserAuthentication)
        #endif
        try JSONEncoder().encode(self).write(to: directory.appendingPathComponent("job.json"), options: options)
    }

    static func load(from directory: URL) throws -> Self {
        let job = try JSONDecoder().decode(Self.self, from: Data(contentsOf: directory.appendingPathComponent("job.json")))
        guard UUID(uuidString: directory.lastPathComponent) == job.id, job.samples.count == job.plan.parts.count,
              job.acknowledged.isSubset(of: Set(job.samples.indices)), job.detailsSaved.isSubset(of: job.acknowledged),
              job.samples.allSatisfy({ $0 > 0 && $0 <= RecordingImport.samplesPerPart }) else { throw CaptureError.invalidReceipt }
        try job.plan.validate(digests: job.plan.parts.map(\.digest), sampleCount: job.samples.reduce(0, +))
        return job
    }

    func uploadRequest(for index: Int) throws -> URLRequest {
        guard samples.indices.contains(index), let base = configuration.activeEndpoint.flatMap({ URL(string: $0.address) }) else { throw CaptureError.invalidReceipt }
        var request = try request("v1/audio/\(plan.parts[index].id.uuidString.lowercased())")
        request.url = RecordingImport.uploadURL(base: base, id: plan.parts[index].id, query: try plan.query(for: index))
        request.httpMethod = "PUT"
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        return request
    }

    func request(_ path: String) throws -> URLRequest {
        guard let endpoint = configuration.activeEndpoint, let base = URL(string: endpoint.address),
              ["http", "https"].contains(base.scheme?.lowercased() ?? ""), base.host != nil,
              base.user == nil, base.password == nil, !profileID.isEmpty else { throw CaptureError.invalidReceipt }
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.timeoutInterval = 180
        request.allowsCellularAccess = !wiFiOnly
        request.setValue(profileID, forHTTPHeaderField: "X-Omi-Profile")
        request.setValue(ManagedServiceOrigin.ownDestination(archiveIdentity: configuration.archiveIdentity), forHTTPHeaderField: "X-Daythread-Destination")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    mutating func accept(_ data: Data, response: URLResponse?, part: Int) throws {
        try Self.check(response, data: data)
        guard samples.indices.contains(part) else { throw CaptureError.invalidReceipt }
        try plan.validateReceipt(data, part: part, sampleCount: samples[part])
        acknowledged.insert(part)
    }

    static func check(_ response: URLResponse?, data: Data) throws {
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw RecordingImportError(message ?? AppLocalization.source("The recording could not be uploaded. Open Daythread to retry."))
        }
    }

    private static func protect(_ url: URL) throws {
        #if os(iOS)
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: url.path)
        #endif
    }
}

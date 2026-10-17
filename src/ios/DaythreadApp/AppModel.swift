import Foundation
import Observation
import OmiCore
import AVFoundation

@MainActor @Observable
final class AppModel {
    let bluetooth = OmiBluetooth()
    private(set) var backendConfiguration: BackendEndpointConfiguration
    var backendAddress: String { backendConfiguration.activeEndpoint?.address ?? "" }
    var backendEndpoints: [BackendEndpoint] { backendConfiguration.endpoints }
    struct Profile: Codable, Identifiable, Hashable {
        let id: String
        let name: String
    }
    struct ProfileDirectory: Decodable { let profiles: [Profile] }
    private(set) var profiles: [Profile] = [.init(id: "default", name: "Default")]
    private(set) var selectedProfileID: String
    var hasSelectedProfile: Bool { !selectedProfileID.isEmpty }
    var selectedProfileName: String { profiles.first { $0.id == selectedProfileID }?.name ?? selectedProfileID }
    // Preserve legacy default caches while isolating every additional profile.
    var archiveIdentity: String { selectedProfileID == "default" ? backendConfiguration.archiveIdentity : backendConfiguration.archiveIdentity + ".profile." + selectedProfileID }

    private func resetArchive() {
        backendGeneration = UUID()
        player?.stop(); player = nil; playbackStatus = ""
        serverTimeZoneIdentifier = UserDefaults.standard.string(forKey: backendAddress + ".timezone")
        serverTimeline = LocalCache.load(archiveIdentity + ".timeline") ?? .null
        importanceMarks = []; importanceLoaded = false; importanceError = nil; importanceRequest = nil; importanceSaving = []
        importanceRevision = UUID()
        labelRevision = ""; archiveRevision = ""
        serverStatus = "Not connected"; completenessStatus = .null
        error = nil; lastStatusCheck = .distantPast
        refreshLocal()
    }
    func selectProfile(_ profile: Profile?) {
        let id = profile?.id ?? ""
        guard id != selectedProfileID else { return }
        // Finish under the old owner before accepting any frames for the next profile.
        flush()
        guard store?.isRecording != true else { return }
        selectedProfileID = id
        UserDefaults.standard.set(id, forKey: "selectedProfileID")
        store?.profileID = id
        bluetooth.captureEnabled = hasSelectedProfile && !paused && store != nil
        nextAudioDate = nil; disconnectedAt = nil
        lastDecodedAt = nil; lastPersistedAt = nil
        resetArchive()
        recordCaptureState(paused ? "paused" : "unknown", cause: paused ? "user_pause" : "unknown")
        notice = profile.map { "Profile: " + $0.name } ?? "Choose a profile in Settings"
        reconnectBackend()
        Task { await refreshStatus() }
    }
    func loadProfiles() async throws {
        let generation = backendGeneration
        let directory = try await client().decoded("v1/profiles", as: ProfileDirectory.self)
        guard backendGeneration == generation, !Task.isCancelled else { return }
        profiles = directory.profiles
        if hasSelectedProfile && !profiles.contains(where: { $0.id == selectedProfileID }) {
            selectProfile(nil)
        }
    }
    func createProfile(name: String) async throws {
        let generation = backendGeneration
        let profile = try await client().decoded("v1/profiles", method: "POST", body: ["name": name.trimmingCharacters(in: .whitespacesAndNewlines)], as: Profile.self)
        guard backendGeneration == generation, !Task.isCancelled else { return }
        profiles.append(profile)
    }
    func renameProfile(_ profile: Profile, name: String) async throws {
        let generation = backendGeneration
        let updated = try await client().decoded("v1/profiles/" + profile.id, method: "PATCH", body: ["name": name.trimmingCharacters(in: .whitespacesAndNewlines)], as: Profile.self)
        guard backendGeneration == generation, !Task.isCancelled else { return }
        if let index = profiles.firstIndex(where: { $0.id == profile.id }) { profiles[index] = updated }
    }
    var importanceMarks: Set<ImportanceTarget> = []
    var importanceLoaded = false
    var importanceError: String?
    var importanceSaving: Set<ImportanceTarget> = []
    @ObservationIgnored private var importanceRequest: UUID?
    @ObservationIgnored private var importanceRevision = UUID()
    var labelRevision = ""
    var archiveRevision = ""
    var serverTimeZoneIdentifier: String?
    var gesture: MeetingGesture { didSet { UserDefaults.standard.set(gesture.rawValue, forKey: "meetingGesture") } }
    var meetingMinutes: Int { didSet { UserDefaults.standard.set(meetingMinutes, forKey: "meetingMinutes") } }
    var paused: Bool {
        didSet {
            UserDefaults.standard.set(paused, forKey: "capturePaused")
            if paused != oldValue { pauseRevision = UUID() }
        }
    }
    var uploadWiFiOnly: Bool {
        didSet {
            UserDefaults.standard.set(uploadWiFiOnly, forKey: "uploadWiFiOnly")
            uploader?.setUploadWiFiOnly(uploadWiFiOnly)
        }
    }
    var storageBudgetMB: Int {
        didSet {
            UserDefaults.standard.set(storageBudgetMB, forKey: "storageBudgetMB")
            store?.maximumBytes = Int64(storageBudgetMB) * 1_000_000
        }
    }
    var error: String?
    var notice = "Pair your Omi to begin"
    var queueCount = 0
    var uploadFeedback: UploadFeedback?
    var unsyncedMinutes = 0.0
    var storageMB = 0.0
    var recentGaps: [OutboxItem] = []
    var queueErrors: [String] = []
    var meetingStart: Date?
    var meetingDeadline: Date?
    var meetingQualifiesAt: Date?
    var markerCooldownSeconds: Int { didSet { UserDefaults.standard.set(markerCooldownSeconds, forKey: "markerCooldownSeconds") } }
    var meetingMinSeconds: Int { didSet { UserDefaults.standard.set(meetingMinSeconds, forKey: "meetingMinSeconds") } }
    var nextMarkAt: Date?
    var serverStatus = "Not connected"
    var serverTimeline: JSONValue = .null
    var completenessStatus: JSONValue = .null
    var lastDecodedAt: Date?
    var lastPersistedAt: Date?
    var captureFailure: String? = UserDefaults.standard.string(forKey: "captureFailure")
    private(set) var observedCaptureState = "unknown"
    @ObservationIgnored private var phoneID: UUID = {
        if let value = UserDefaults.standard.string(forKey: "phoneInstallationID"), let id = UUID(uuidString: value) { return id }
        let id = UUID(); UserDefaults.standard.set(id.uuidString, forKey: "phoneInstallationID"); return id
    }()
    var now = Date()
    var player: AVAudioPlayer?
    var playbackStatus = ""
    @ObservationIgnored var store: DurableStore?
    @ObservationIgnored private var uploader: BackgroundUploader?
    @ObservationIgnored private var reassembler = OmiReassembler()
    @ObservationIgnored private var decoder: FrameDecoder?
    @ObservationIgnored private var pendingClock: CaptureClock?
    @ObservationIgnored private var lastNotification: Date?
    @ObservationIgnored private var nextAudioDate: Date?
    @ObservationIgnored private var disconnectedAt: Date?
    @ObservationIgnored private var disconnectedCause = "unknown"
    @ObservationIgnored private let clock = CaptureClock()
    @ObservationIgnored private var lastStatusCheck = Date.distantPast
    @ObservationIgnored private var backendGeneration = UUID()
    @ObservationIgnored private var pauseRevision = UUID()
    @ObservationIgnored private var refreshingStatus = false
    @ObservationIgnored private var refreshStatusPending = false
    @ObservationIgnored private let endpointRouter: BackendEndpointRouter

    init() {
        let defaults = UserDefaults.standard
        let selectedID = defaults.string(forKey: "selectedProfileID") ?? ""
        let defaultAddress = defaults.string(forKey: "backendURL") ?? "https://daythread.home.arpa"
        let storedConfiguration: BackendEndpointConfiguration
        if let data = defaults.data(forKey: "backendEndpoints.v1"),
           let stored = try? JSONDecoder().decode(BackendEndpointConfiguration.self, from: data),
           !stored.endpoints.isEmpty {
            storedConfiguration = stored
        } else {
            let endpoint = BackendEndpoint(address: defaultAddress)
            storedConfiguration = BackendEndpointConfiguration(endpoints: [endpoint], activeID: endpoint.id, archiveIdentity: defaultAddress)
        }
        let activeAddress = storedConfiguration.activeEndpoint?.address ?? defaultAddress
        let archiveKey = selectedID == "default" ? storedConfiguration.archiveIdentity : storedConfiguration.archiveIdentity + ".profile." + selectedID
        selectedProfileID = selectedID
        backendConfiguration = storedConfiguration
        endpointRouter = BackendEndpointRouter(configuration: storedConfiguration)
        gesture = MeetingGesture(rawValue: defaults.string(forKey: "meetingGesture") ?? "") ?? .doubleClick
        let timeout = defaults.integer(forKey: "meetingMinutes")
        meetingMinutes = timeout == 0 ? 120 : timeout
        markerCooldownSeconds = defaults.object(forKey: "markerCooldownSeconds") as? Int ?? 60
        meetingMinSeconds = defaults.object(forKey: "meetingMinSeconds") as? Int ?? 60
        paused = defaults.bool(forKey: "capturePaused")
        uploadWiFiOnly = defaults.bool(forKey: "uploadWiFiOnly")
        storageBudgetMB = defaults.object(forKey: "storageBudgetMB") as? Int ?? 2000
        serverTimeZoneIdentifier = defaults.string(forKey: activeAddress + ".timezone")
        serverTimeline = LocalCache.load(archiveKey + ".timeline") ?? .null
        do {
            var root = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            var backupPolicy = URLResourceValues(); backupPolicy.isExcludedFromBackup = true
            try root.setResourceValues(backupPolicy)
            let capture = root.appendingPathComponent("Capture", isDirectory: true)
            store = try DurableStore(directory: capture)
            store?.profileID = selectedProfileID
            store?.maximumBytes = Int64(storageBudgetMB) * 1_000_000
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: capture.path)
            if let store { uploader = BackgroundUploader(store: store, uploadWiFiOnly: uploadWiFiOnly) }
            disconnectedAt = store?.items.filter { $0.resolvedProfileID == selectedProfileID && $0.kind == .audio }.compactMap(\.endedAt).max()
            uploader?.onChange = { [weak self] in
                self?.refreshLocal()
                Task { @MainActor [weak self] in
                    guard let self, let backend = try? self.client() else { return }
                    try? await self.publishObservation(using: backend)
                }
            }
            uploader?.onError = { [weak self] in self?.serverStatus = $0 }
            uploader?.onProgress = { [weak self] in self?.refreshLocal() }
            refreshLocal()
        } catch { self.error = error.localizedDescription }
        bluetooth.captureEnabled = hasSelectedProfile && !paused && store != nil
        bluetooth.onCodec = { [weak self] codec in
            do { self?.decoder = try FrameDecoder(codec: codec) }
            catch { self?.failCapture(error) }
        }
        bluetooth.onAudio = { [weak self] data, date, uptime in self?.audio(data, date: date, uptime: uptime) }
        bluetooth.onButton = { [weak self] data in self?.button(data) }
        bluetooth.onDisconnect = { [weak self] in self?.disconnectCapture() }
        bluetooth.onError = { [weak self] message in self?.error = message }
        recordCaptureState(paused && captureFailure == nil ? "paused" : captureFailure != nil ? "blocked" : "unknown", cause: paused && captureFailure == nil ? "user_pause" : captureFailure ?? "unknown")
        endpointRouter.onChange = { [weak self] configuration in
            guard let self else { return }
            Task { @MainActor in
                self.backendConfiguration = configuration
                self.persistBackendConfiguration()
            }
        }
        persistBackendConfiguration()
        reconnectBackend()
    }
    func client() throws -> BackendClient { try BackendClient(router: endpointRouter, profileID: selectedProfileID) }
    func reconnectBackend() {
        guard hasSelectedProfile else { uploader?.client = nil; return }
        do { uploader?.client = try client(); uploader?.retry() }
        catch { self.error = error.localizedDescription }
    }

    func saveEndpoint(address: String, replacing id: UUID? = nil) throws {
        let normalized = try BackendClient.validatedURL(address).absoluteString
        var configuration = backendConfiguration
        if let id, let index = configuration.endpoints.firstIndex(where: { $0.id == id }) {
            configuration.endpoints[index].address = normalized
        } else {
            let endpoint = BackendEndpoint(address: normalized)
            configuration.endpoints.append(endpoint)
            if configuration.activeID == nil { configuration.activeID = endpoint.id }
        }
        applyEndpointConfiguration(configuration)
    }

    func removeEndpoint(_ id: UUID) throws {
        guard backendConfiguration.endpoints.count > 1 else {
            throw BackendFailure(message: "Keep at least one backend endpoint configured.")
        }
        var configuration = backendConfiguration
        configuration.remove(id: id)
        applyEndpointConfiguration(configuration)
    }

    func moveEndpoints(from offsets: IndexSet, to destination: Int) {
        var configuration = backendConfiguration
        configuration.move(fromOffsets: offsets, toOffset: destination)
        applyEndpointConfiguration(configuration)
    }

    func activateEndpoint(_ id: UUID) {
        let oldAddress = backendAddress
        endpointRouter.setActive(id)
        backendConfiguration = endpointRouter.configuration
        persistBackendConfiguration()
        if oldAddress != backendAddress { backendAddressChanged() }
    }

    func testEndpoint(_ endpoint: BackendEndpoint) async throws {
        let client = try BackendClient(address: endpoint.address, profileID: selectedProfileID)
        _ = try await client.request("v1/profiles")
    }

    private func applyEndpointConfiguration(_ configuration: BackendEndpointConfiguration) {
        let oldAddress = backendAddress
        var configuration = configuration
        if configuration.archiveIdentity.isEmpty { configuration.archiveIdentity = backendConfiguration.archiveIdentity }
        if configuration.activeEndpoint?.address != oldAddress, let active = configuration.activeEndpoint {
            configuration.archiveIdentity = active.address
        }
        endpointRouter.replace(configuration)
        backendConfiguration = configuration
        persistBackendConfiguration()
        if oldAddress != backendAddress { backendAddressChanged() }
    }

    private func backendAddressChanged() {
        UserDefaults.standard.set(backendAddress, forKey: "backendURL")
        selectProfile(nil)
        resetArchive()
        profiles = []
        reconnectBackend()
    }

    private func persistBackendConfiguration() {
        guard let data = try? JSONEncoder().encode(backendConfiguration) else { return }
        UserDefaults.standard.set(data, forKey: "backendEndpoints.v1")
        UserDefaults.standard.set(backendAddress, forKey: "backendURL")
    }
    func tick() async {
        now = Date(); refreshLocal(); uploader?.pump()
        let processing = completenessStatus["processing"]
        let busy = queueCount > 0 || processing["queued"].integer > 0 || processing["transcribing"].integer > 0
        if now.timeIntervalSince(lastStatusCheck) >= (busy ? 2 : 15) { await refreshStatus() }
    }
    func refreshStatus() async {
        guard !refreshingStatus else { refreshStatusPending = true; return }
        refreshingStatus = true
        defer {
            refreshingStatus = false
            if refreshStatusPending {
                refreshStatusPending = false
                Task { await refreshStatus() }
            }
        }
        lastStatusCheck = Date()
        let generation = backendGeneration
        let address = backendAddress
        let identity = archiveIdentity
        do {
            let backend = try client()
            try await loadProfiles()
            guard backendGeneration == generation else { return }
            guard hasSelectedProfile else { serverStatus = "Choose a profile in Settings"; return }
            let status = try await backend.request("v1/status")
            guard backendGeneration == generation else { return }
            LocalCache.reconcile(address: identity, revision: status["archive_revision"].text)
            archiveRevision = status["archive_revision"].text
            labelRevision = status["label_revision"].text
            if TimeZone(identifier: status["timezone"].text) != nil {
                serverTimeZoneIdentifier = status["timezone"].text
                UserDefaults.standard.set(serverTimeZoneIdentifier, forKey: address + ".timezone")
            }
            completenessStatus = status
            serverStatus = status["worker_running"] == .bool(true) ? "Home-core connected · worker running" : "Home-core connected"
            if UserDefaults.standard.bool(forKey: "pauseSettingDirty") {
                let revision = pauseRevision
                _ = try await backend.request("v1/settings", method: "PATCH", body: ["capture_paused": paused && captureFailure == nil])
                guard backendGeneration == generation else { return }
                guard pauseRevision == revision else { refreshStatusPending = true; return }
                UserDefaults.standard.set(false, forKey: "pauseSettingDirty")
            }
            let revision = pauseRevision
            let settings = try await backend.request("v1/settings")
            guard backendGeneration == generation else { return }
            if settings["meeting_max_minutes"].integer > 0 { meetingMinutes = settings["meeting_max_minutes"].integer }
            if case .number(let cooldown) = settings["marker_cooldown_seconds"] { markerCooldownSeconds = max(0, min(3600, Int(cooldown))) }
            if settings["meeting_min_seconds"].integer >= 60 { meetingMinSeconds = settings["meeting_min_seconds"].integer }
            if pauseRevision == revision, !UserDefaults.standard.bool(forKey: "pauseSettingDirty"),
               case .bool(let remotePause) = settings["capture_paused"], remotePause != paused, captureFailure == nil {
                setPaused(remotePause, synchronize: false)
            }
            let timeline = try await backend.request("v1/timeline")
            guard backendGeneration == generation else { return }
            serverTimeline = timeline
            LocalCache.save(timeline, key: identity + ".timeline")
            refreshLocal()
            try await publishObservation(using: backend)
            await loadImportance()
        } catch {
            if backendGeneration == generation { serverStatus = error.localizedDescription }
        }
    }
    func loadImportance() async {
        guard importanceRequest == nil else { return }
        let token = UUID(), generation = backendGeneration, revision = importanceRevision
        importanceRequest = token
        defer { if importanceRequest == token { importanceRequest = nil } }
        do {
            let result: ImportanceDirectory = try await client().decoded("v1/importance", as: ImportanceDirectory.self)
            guard !Task.isCancelled, backendGeneration == generation, importanceRevision == revision else { return }
            importanceMarks = Set(result.items); importanceLoaded = true; importanceError = nil
        } catch {
            guard !Task.isCancelled, backendGeneration == generation else { return }
            importanceError = error.localizedDescription
        }
    }
    func setImportant(_ target: ImportanceTarget, to value: Bool) async throws {
        guard !importanceSaving.contains(target) else { return }
        let generation = backendGeneration
        importanceSaving.insert(target); importanceRevision = UUID()
        defer { if backendGeneration == generation { importanceSaving.remove(target) } }
        _ = try await client().request("v1/importance", method: "PUT", body: [
            "kind": target.kind, "id": target.id, "section": target.section, "important": value
        ])
        guard backendGeneration == generation else { return }
        importanceRevision = UUID()
        if value { importanceMarks.insert(target) } else { importanceMarks.remove(target) }
    }
    func setPaused(_ value: Bool, synchronize: Bool = true) {
        if value { flush() }
        else {
            // A failed finalization keeps the previous segment recoverable. Close it before accepting new frames.
            do { try store?.finish() } catch { failCapture(error); return }
        }
        captureFailure = nil; UserDefaults.standard.removeObject(forKey: "captureFailure")
        recordCaptureState(value ? "paused" : "unknown", cause: value ? "user_pause" : "resumed")
        paused = value; bluetooth.captureEnabled = hasSelectedProfile && !value && store != nil
        notice = value ? "Capture paused" : "Capture resumed"
        if value {
            disconnectedAt = nil
            nextAudioDate = nil
        } else if let codec = bluetooth.codec {
            do { decoder = try FrameDecoder(codec: codec) } catch { failCapture(error) }
        }
        if synchronize {
            UserDefaults.standard.set(true, forKey: "pauseSettingDirty")
            Task { await refreshStatus() }
        }
    }
    private func audio(_ data: Data, date suppliedDate: Date, uptime: TimeInterval) {
        guard hasSelectedProfile, !paused, decoder != nil, store != nil else { return }
        let date = clock.date(at: uptime)
        if let lastNotification, date.timeIntervalSince(lastNotification) > 1 {
            flush(); addGap(start: nextAudioDate ?? lastNotification, end: date, reason: "BLE audio delivery interrupted", cause: "delivery_interrupted")
            nextAudioDate = nil
            do { decoder = try FrameDecoder(codec: bluetooth.codec ?? -1) } catch { failCapture(error); return }
        }
        self.lastNotification = date
        let result = reassembler.consume(data)
        if result.lost {
            do { try store?.finish() } catch { failCapture(error); return }
            addGap(start: nextAudioDate ?? date.addingTimeInterval(-0.02), end: date, reason: "BLE sequence or fragment loss", cause: "packet_loss")
            pendingClock = nil; nextAudioDate = nil
            do { decoder = try FrameDecoder(codec: bluetooth.codec ?? -1) } catch { failCapture(error); return }
        }
        if let frame = result.frame, let clock = pendingClock { record(frame, clock: clock) }
        if result.startedFrame {
            if let disconnectedAt {
                addGap(start: disconnectedAt, end: date, reason: disconnectedCause == "connection" ? "Capture disconnected" : "Capture disconnected or paused", cause: disconnectedCause)
                self.disconnectedAt = nil
            }
            pendingClock = CaptureClock(wall: nextAudioDate ?? date, uptime: uptime)
        }
    }
    private func record(_ frame: Data, clock: CaptureClock) {
        guard let store, let decoder else { return }
        do {
            let pcm = try decoder.decode(frame)
            lastDecodedAt = Date()
            if observedCaptureState != "capturing" { recordCaptureState("capturing", cause: "resumed") }
            append(frame, pcm: pcm, clock: clock, store: store, codec: decoder.codec)
        } catch { failCapture(error) }
    }
    private func append(_ frame: Data, pcm: Data, clock: CaptureClock, store: DurableStore, codec: Int) {
        do {
            if !store.isRecording { try store.begin(deviceID: bluetooth.identifier, codec: codec, clock: clock) }
            try store.append(frame: frame, decoded: pcm)
            lastPersistedAt = Date()
            nextAudioDate = (nextAudioDate ?? clock.wall).addingTimeInterval(Double(pcm.count) / 32000)
            if store.segmentSeconds >= 30 { try store.finish(); refreshLocal(); uploader?.pump() }
        } catch { failCapture(error) }
    }
    func flush() {
        if let clock = pendingClock, let decoder, let store {
            if let final = reassembler.flush(using: decoder) {
                append(final.frame, pcm: final.pcm, clock: clock, store: store, codec: decoder.codec)
            } else {
                addGap(start: nextAudioDate ?? clock.wall,
                       end: self.clock.date(at: ProcessInfo.processInfo.systemUptime),
                       reason: "Incomplete final BLE frame discarded", cause: "incomplete_frame")
            }
        } else { reassembler.reset() }
        pendingClock = nil; lastNotification = nil
        do { try store?.finish(); refreshLocal(); uploader?.pump() } catch { failCapture(error) }
    }
    func checkpoint() {
        // Preserve the pending fragmented frame while ending only the fully decoded segment.
        do { try store?.finish(); refreshLocal(); uploader?.pump() } catch { failCapture(error) }
    }
    private func disconnectCapture() {
        flush()
        if !paused { recordCaptureState("disconnected", cause: "connection"); disconnectedCause = "connection"; disconnectedAt = nextAudioDate ?? Date() }
        nextAudioDate = nil; decoder = nil
    }
    private func addGap(start: Date, end: Date, reason: String, cause: String = "unknown") {
        guard hasSelectedProfile else { return }
        do { try store?.gap(deviceID: bluetooth.identifier, start: start, end: end, reason: reason, cause: cause); refreshLocal() }
        catch { failCapture(error) }
    }
    private func failCapture(_ error: Error) {
        if case CaptureError.storageFull = error { captureFailure = "storage" }
        else { captureFailure = error is CaptureError ? "decoder" : "storage" }
        UserDefaults.standard.set(captureFailure, forKey: "captureFailure")
        disconnectedCause = captureFailure ?? "unknown"
        recordCaptureState("blocked", cause: captureFailure ?? "unknown")
        self.error = error.localizedDescription; paused = true; bluetooth.captureEnabled = false
        disconnectedAt = nextAudioDate ?? clock.date(at: ProcessInfo.processInfo.systemUptime); nextAudioDate = nil
        reassembler.reset(); pendingClock = nil
        do { try store?.finish() } catch { self.error = "\(self.error ?? "") · Could not close segment: \(error.localizedDescription)" }
        refreshLocal()
    }
    private func button(_ data: Data) {
        var policy = GesturePolicy(meetingGesture: gesture, lastAcceptedMark: nextMarkAt?.addingTimeInterval(-Double(markerCooldownSeconds)), cooldown: Double(markerCooldownSeconds))
        if let action = policy.resolve(data, at: Date()) { event(action) }
    }
    func event(_ action: SemanticAction) {
        guard hasSelectedProfile else { error = "Choose a profile in Settings first."; return }
        let date = clock.date(at: ProcessInfo.processInfo.systemUptime)
        if action == .mark, let nextMarkAt, date < nextMarkAt {
            notice = "Already marked nearby. Another flag is available in \(Int(ceil(nextMarkAt.timeIntervalSince(date)))) seconds."
            return
        }
        guard let store else { error = "Capture storage unavailable"; return }
        do {
            let oldStart = meetingStart
            let oldQualification = meetingQualifiesAt
            try store.event(deviceID: bluetooth.identifier, at: date, action: action, meetingMaxMinutes: meetingMinutes, meetingMinSeconds: meetingMinSeconds, markerCooldownSeconds: markerCooldownSeconds)
            if action == .mark { notice = "Marked nearby speech as important" }
            else if let oldStart {
                let qualifies = oldQualification ?? oldStart.addingTimeInterval(Double(meetingMinSeconds))
                notice = date < qualifies ? "Short meeting ignored. Audio is kept." : "Meeting hint ended"
            }
            else { notice = "Meeting hint started · provisional for \(meetingMinSeconds) seconds" }
            refreshLocal(); uploader?.pump()
        } catch { self.error = error.localizedDescription }
    }
    private func refreshLocal() {
        guard let store else { return }
        let items = store.items.filter { $0.resolvedProfileID == selectedProfileID }
        let pending = items.filter { !$0.acknowledged && $0.ready }
        queueCount = pending.count
        uploadFeedback = uploader?.feedback(profileID: selectedProfileID)
        unsyncedMinutes = items.filter { $0.kind == .audio && !$0.acknowledged }.reduce(0) { $0 + max(0, ($1.endedAt ?? $1.capturedAt).timeIntervalSince($1.capturedAt)) } / 60
        storageMB = Double(store.storageBytes) / 1_000_000
        recentGaps = Array(items.filter { $0.kind == .gap }.suffix(5).reversed())
        queueErrors = Array(items.compactMap(\.error).suffix(3))
        nextMarkAt = items.filter { $0.action == .mark }.map { $0.capturedAt.addingTimeInterval(Double($0.markerCooldownSeconds ?? 60)) }.max()
        if let remoteMark = serverTimeline["markers"].array.compactMap({ Self.date($0["occurred_at"].text) }).max() {
            let remoteNext = remoteMark.addingTimeInterval(Double(markerCooldownSeconds))
            nextMarkAt = max(nextMarkAt ?? .distantPast, remoteNext)
        }
        if serverTimeline != .null, !pending.contains(where: { $0.kind == .event && $0.action == .meetingToggle }) {
            meetingStart = Self.date(serverTimeline["active_meeting"]["started_at"].text)
            meetingDeadline = Self.date(serverTimeline["active_meeting"]["auto_stop_at"].text)
            meetingQualifiesAt = Self.date(serverTimeline["active_meeting"]["qualifies_at"].text)
            if let stop = meetingDeadline, stop <= Date() { meetingStart = nil; meetingDeadline = nil; meetingQualifiesAt = nil }
            return
        }
        var start: Date?; var deadline: Date?; var qualifies: Date?
        for item in items.filter({ $0.kind == .event && $0.action == .meetingToggle }).sorted(by: { $0.capturedAt < $1.capturedAt }) {
            if let stop = deadline, item.capturedAt >= stop { start = nil; deadline = nil }
            if start != nil { start = nil; deadline = nil }
            else {
                start = item.capturedAt; deadline = item.capturedAt.addingTimeInterval(Double(item.meetingMaxMinutes ?? 120) * 60)
                qualifies = item.capturedAt.addingTimeInterval(Double(item.meetingMinSeconds ?? 60))
            }
        }
        if let stop = deadline, stop <= Date() { start = nil; deadline = nil }
        meetingStart = start; meetingDeadline = deadline; meetingQualifiesAt = start == nil ? nil : qualifies
    }
    private func recordCaptureState(_ state: String, cause: String) {
        guard hasSelectedProfile else { return }
        observedCaptureState = state
        do { try store?.captureState(phoneID: phoneID, at: Date(), state: state, cause: cause) }
        catch { self.error = "Could not persist capture observation: \(error.localizedDescription)" }
    }
    private func publishObservation(using backend: BackendClient) async throws {
        guard hasSelectedProfile, let store else { return }
        let sequence = (UserDefaults.standard.object(forKey: "phoneObservationSequence") as? Int ?? 0) + 1
        UserDefaults.standard.set(sequence, forKey: "phoneObservationSequence")
        let pending = store.items.filter { $0.resolvedProfileID == backend.profileID && $0.kind == .audio && !$0.acknowledged }
        let state = captureFailure != nil ? "blocked" : paused ? "paused" : !bluetooth.connected ? "disconnected" : lastDecodedAt.map { Date().timeIntervalSince($0) < 2 } == true ? "capturing" : "unknown"
        var body: [String: Any] = [
            "sequence": sequence, "observed_at": DurableStore.stamp(Date()),
            "device_id": bluetooth.identifier, "capture_state": state,
            "cause": captureFailure ?? (paused ? "user_pause" : !bluetooth.connected ? "connection" : "unknown"),
            "requested_pause": paused && captureFailure == nil,
            "pending_recordings": pending.filter(\.ready).count,
            "recovery_items": pending.filter { !$0.ready }.count,
            "pending_seconds": pending.reduce(0.0) { $0 + max(0, ($1.endedAt ?? $1.capturedAt).timeIntervalSince($1.capturedAt)) },
            "upload_policy": uploadWiFiOnly ? "wifi_only" : "any_network",
            "upload_state": uploader?.observedState ?? "unknown",
            "storage_bytes": store.storageBytes, "storage_budget_bytes": Int64(storageBudgetMB) * 1_000_000,
        ]
        if let lastDecodedAt { body["last_decoded_at"] = DurableStore.stamp(lastDecodedAt) }
        if let lastPersistedAt { body["last_persisted_at"] = DurableStore.stamp(lastPersistedAt) }
        if let oldest = pending.map(\.capturedAt).min() { body["oldest_capture_at"] = DurableStore.stamp(oldest) }
        _ = try await backend.request("v1/phones/\(phoneID.uuidString)/status", method: "PUT", body: body)
    }
    func retryUploads() { uploader?.retry() }
    func play(_ url: URL) {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
            try AVAudioSession.sharedInstance().setActive(true)
            player?.stop(); player = try AVAudioPlayer(contentsOf: url)
            player?.prepareToPlay(); player?.play(); playbackStatus = "Playing recorded audio"
        } catch { self.error = error.localizedDescription }
    }
    func localRewind(minutes: Int, end: Date) throws -> Int {
        checkpoint(); guard let store else { throw BackendFailure(message: "Capture storage unavailable") }
        let result = try store.rewind(minutes: minutes, end: end); play(result.url); return result.gaps
    }
    static func date(_ string: String) -> Date? {
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: string) ?? ISO8601DateFormatter().date(from: string)
    }
}

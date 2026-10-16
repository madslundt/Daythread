import Foundation
import Observation
import OmiCore
import AVFoundation

@MainActor @Observable
final class AppModel {
    let bluetooth = OmiBluetooth()
    var backendAddress: String {
        didSet {
            UserDefaults.standard.set(backendAddress, forKey: "backendURL")
            guard backendAddress != oldValue else { return }
            backendGeneration = UUID()
            serverTimeZoneIdentifier = UserDefaults.standard.string(forKey: backendAddress + ".timezone")
            serverTimeline = LocalCache.load(backendAddress + ".timeline") ?? .null
            serverStatus = "Not connected"
            lastStatusCheck = .distantPast
            refreshLocal()
        }
    }
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
    @ObservationIgnored private let clock = CaptureClock()
    @ObservationIgnored private var lastStatusCheck = Date.distantPast
    @ObservationIgnored private var backendGeneration = UUID()
    @ObservationIgnored private var pauseRevision = UUID()
    @ObservationIgnored private var refreshingStatus = false
    @ObservationIgnored private var refreshStatusPending = false

    init() {
        let defaults = UserDefaults.standard
        backendAddress = defaults.string(forKey: "backendURL") ?? "http://home-core.local:8787"
        gesture = MeetingGesture(rawValue: defaults.string(forKey: "meetingGesture") ?? "") ?? .doubleClick
        let timeout = defaults.integer(forKey: "meetingMinutes")
        meetingMinutes = timeout == 0 ? 120 : timeout
        markerCooldownSeconds = defaults.object(forKey: "markerCooldownSeconds") as? Int ?? 60
        meetingMinSeconds = defaults.object(forKey: "meetingMinSeconds") as? Int ?? 60
        paused = defaults.bool(forKey: "capturePaused")
        uploadWiFiOnly = defaults.bool(forKey: "uploadWiFiOnly")
        storageBudgetMB = defaults.object(forKey: "storageBudgetMB") as? Int ?? 2000
        serverTimeZoneIdentifier = defaults.string(forKey: backendAddress + ".timezone")
        serverTimeline = LocalCache.load(backendAddress + ".timeline") ?? .null
        do {
            var root = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            var backupPolicy = URLResourceValues(); backupPolicy.isExcludedFromBackup = true
            try root.setResourceValues(backupPolicy)
            let capture = root.appendingPathComponent("Capture", isDirectory: true)
            store = try DurableStore(directory: capture)
            store?.maximumBytes = Int64(storageBudgetMB) * 1_000_000
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: capture.path)
            if let store { uploader = BackgroundUploader(store: store, uploadWiFiOnly: uploadWiFiOnly) }
            disconnectedAt = store?.items.filter { $0.kind == .audio }.compactMap(\.endedAt).max()
            uploader?.onChange = { [weak self] in self?.refreshLocal() }
            uploader?.onError = { [weak self] in self?.serverStatus = $0 }
            refreshLocal()
        } catch { self.error = error.localizedDescription }
        bluetooth.captureEnabled = !paused && store != nil
        bluetooth.onCodec = { [weak self] codec in
            do { self?.decoder = try FrameDecoder(codec: codec) }
            catch { self?.failCapture(error) }
        }
        bluetooth.onAudio = { [weak self] data, date, uptime in self?.audio(data, date: date, uptime: uptime) }
        bluetooth.onButton = { [weak self] data in self?.button(data) }
        bluetooth.onDisconnect = { [weak self] in self?.disconnectCapture() }
        bluetooth.onError = { [weak self] message in self?.error = message }
        reconnectBackend()
    }
    func client() throws -> BackendClient { try BackendClient(address: backendAddress) }
    func reconnectBackend() {
        do { uploader?.client = try client(); uploader?.retry() }
        catch { self.error = error.localizedDescription }
    }
    func tick() async {
        now = Date(); refreshLocal(); uploader?.pump()
        if now.timeIntervalSince(lastStatusCheck) >= 15 { await refreshStatus() }
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
        do {
            let backend = try BackendClient(address: address)
            let status = try await backend.request("v1/status")
            guard backendGeneration == generation else { return }
            if TimeZone(identifier: status["timezone"].text) != nil {
                serverTimeZoneIdentifier = status["timezone"].text
                UserDefaults.standard.set(serverTimeZoneIdentifier, forKey: address + ".timezone")
            }
            serverStatus = status["worker_running"] == .bool(true) ? "Home-core connected · worker running" : "Home-core connected"
            if UserDefaults.standard.bool(forKey: "pauseSettingDirty") {
                let revision = pauseRevision
                _ = try await backend.request("v1/settings", method: "PATCH", body: ["capture_paused": paused])
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
               case .bool(let remotePause) = settings["capture_paused"], remotePause != paused {
                setPaused(remotePause, synchronize: false)
            }
            let timeline = try await backend.request("v1/timeline")
            guard backendGeneration == generation else { return }
            serverTimeline = timeline
            LocalCache.save(timeline, key: address + ".timeline")
            refreshLocal()
        } catch {
            if backendGeneration == generation { serverStatus = error.localizedDescription }
        }
    }
    func setPaused(_ value: Bool, synchronize: Bool = true) {
        if value { flush() }
        else {
            // A failed finalization keeps the previous segment recoverable. Close it before accepting new frames.
            do { try store?.finish() } catch { failCapture(error); return }
        }
        paused = value; bluetooth.captureEnabled = !value && store != nil
        notice = value ? "Capture paused" : "Capture resumed"
        if value {
            disconnectedAt = nextAudioDate ?? clock.date(at: ProcessInfo.processInfo.systemUptime)
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
        guard !paused, decoder != nil, store != nil else { return }
        let date = clock.date(at: uptime)
        if let lastNotification, date.timeIntervalSince(lastNotification) > 1 {
            flush(); addGap(start: nextAudioDate ?? lastNotification, end: date, reason: "BLE audio delivery interrupted")
            nextAudioDate = nil
            do { decoder = try FrameDecoder(codec: bluetooth.codec ?? -1) } catch { failCapture(error); return }
        }
        self.lastNotification = date
        let result = reassembler.consume(data)
        if result.lost {
            do { try store?.finish() } catch { failCapture(error); return }
            addGap(start: nextAudioDate ?? date.addingTimeInterval(-0.02), end: date, reason: "BLE sequence or fragment loss")
            pendingClock = nil; nextAudioDate = nil
            do { decoder = try FrameDecoder(codec: bluetooth.codec ?? -1) } catch { failCapture(error); return }
        }
        if let frame = result.frame, let clock = pendingClock { record(frame, clock: clock) }
        if result.startedFrame {
            if let disconnectedAt {
                addGap(start: disconnectedAt, end: date, reason: "Capture disconnected or paused")
                self.disconnectedAt = nil
            }
            pendingClock = CaptureClock(wall: nextAudioDate ?? date, uptime: uptime)
        }
    }
    private func record(_ frame: Data, clock: CaptureClock) {
        guard let store, let decoder else { return }
        do {
            let pcm = try decoder.decode(frame)
            append(frame, pcm: pcm, clock: clock, store: store, codec: decoder.codec)
        } catch { failCapture(error) }
    }
    private func append(_ frame: Data, pcm: Data, clock: CaptureClock, store: DurableStore, codec: Int) {
        do {
            if !store.isRecording { try store.begin(deviceID: bluetooth.identifier, codec: codec, clock: clock) }
            try store.append(frame: frame, decoded: pcm)
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
                       reason: "Incomplete final BLE frame discarded")
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
        flush(); disconnectedAt = nextAudioDate ?? Date(); nextAudioDate = nil; decoder = nil
    }
    private func addGap(start: Date, end: Date, reason: String) {
        do { try store?.gap(deviceID: bluetooth.identifier, start: start, end: end, reason: reason); refreshLocal() }
        catch { failCapture(error) }
    }
    private func failCapture(_ error: Error) {
        self.error = error.localizedDescription; paused = true; bluetooth.captureEnabled = false
        UserDefaults.standard.set(true, forKey: "pauseSettingDirty")
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
        queueCount = store.pending.count; unsyncedMinutes = store.unsyncedSeconds / 60
        storageMB = Double(store.storageBytes) / 1_000_000
        recentGaps = Array(store.items.filter { $0.kind == .gap }.suffix(5).reversed())
        queueErrors = Array(store.items.compactMap(\.error).suffix(3))
        nextMarkAt = store.items.filter { $0.action == .mark }.map { $0.capturedAt.addingTimeInterval(Double($0.markerCooldownSeconds ?? 60)) }.max()
        if let remoteMark = serverTimeline["markers"].array.compactMap({ Self.date($0["occurred_at"].text) }).max() {
            let remoteNext = remoteMark.addingTimeInterval(Double(markerCooldownSeconds))
            nextMarkAt = max(nextMarkAt ?? .distantPast, remoteNext)
        }
        if serverTimeline != .null, !store.pending.contains(where: { $0.kind == .event && $0.action == .meetingToggle }) {
            meetingStart = Self.date(serverTimeline["active_meeting"]["started_at"].text)
            meetingDeadline = Self.date(serverTimeline["active_meeting"]["auto_stop_at"].text)
            meetingQualifiesAt = Self.date(serverTimeline["active_meeting"]["qualifies_at"].text)
            if let stop = meetingDeadline, stop <= Date() { meetingStart = nil; meetingDeadline = nil; meetingQualifiesAt = nil }
            return
        }
        var start: Date?; var deadline: Date?; var qualifies: Date?
        for item in store.items.filter({ $0.kind == .event && $0.action == .meetingToggle }).sorted(by: { $0.capturedAt < $1.capturedAt }) {
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

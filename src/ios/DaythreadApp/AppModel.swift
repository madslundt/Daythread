import Foundation
import Observation
import OmiCore
import AVFoundation
import UIKit

@MainActor @Observable
final class AppModel {
    let microphone = PhoneRecorder()
    private(set) var recordingSchedule: RecordingSchedule = {
        guard let data = UserDefaults.standard.data(forKey: "recordingSchedule.v1") else { return RecordingSchedule() }
        // A corrupt policy must not start capture.
        if let policy = try? JSONDecoder().decode(RecordingSchedule.self, from: data) { return policy }
        var disabled = RecordingSchedule(); disabled.enabled = false; return disabled
    }()
    @ObservationIgnored private var scheduleAttempted = false
    @ObservationIgnored private var scheduleTimer: Timer?
    @ObservationIgnored private var scheduledTransition: Date?
    @ObservationIgnored private var clockObserver: NSObjectProtocol?
    private var liveCaptureAllowed: Bool {
        recordingSchedule.allows(Date()) && !paused && captureFailure == nil
            && (!usesManagedHosting || managedAccount.account?.hosting_active == true)
    }
    private var pendantCaptureAllowed: Bool {
        liveCaptureAllowed && recordingSchedule.activeSource == "omi"
    }
    func setRecordingSchedule(_ policy: RecordingSchedule) {
        let changed = recordingSchedule != policy
        if changed { microphone.captureAllowed = false }
        recordingSchedule = policy
        if let data = try? JSONEncoder().encode(policy) { UserDefaults.standard.set(data, forKey: "recordingSchedule.v1") }
        scheduleAttempted = false
        scheduleTimer?.invalidate(); scheduledTransition = nil
        applyRecordingSchedule()
    }
    func disableMicrophone() {
        var policy = recordingSchedule; policy.enabled = false; policy.manualOverride = false
        setRecordingSchedule(policy)
    }
    func followRecordingSchedule() {
        var policy = recordingSchedule; policy.enabled = true; policy.manualOverride = false
        setRecordingSchedule(policy)
    }
    func startManualRecording() {
        var policy = recordingSchedule; policy.enabled = true; policy.manualOverride = true; policy.manualSource = "phone"
        // Explicit recording clears a previous failure on this iPhone.
        if paused || captureFailure != nil { setPaused(false) }
        setRecordingSchedule(policy)
        stopPlayback()
        Task { await microphone.start(archiveIdentity: archiveIdentity) }
    }
    func startManualPendant() {
        var policy = recordingSchedule; policy.enabled = true; policy.manualOverride = true; policy.manualSource = "omi"
        if paused || captureFailure != nil { setPaused(false) }
        setRecordingSchedule(policy)
    }
    func applyRecordingSchedule() {
        armScheduleTimer()
        let allowed = liveCaptureAllowed && hasSelectedProfile
        let pendant = pendantCaptureAllowed && hasSelectedProfile && store != nil
        if bluetooth.captureEnabled && !pendant {
            // Only fully received pre-disable frames may finish. Drop the pending fragment.
            reassembler.reset(); pendingClock = nil
            flush(); nextAudioDate = nil; disconnectedAt = nil
            recordCaptureState("paused", cause: "user_pause")
        }
        if bluetooth.captureEnabled != pendant { bluetooth.captureEnabled = pendant }
        updateLocationCapture()
        microphone.captureDeadline = allowed && recordingSchedule.scheduled && !recordingSchedule.manualOverride ? scheduledTransition : nil
        microphone.captureAllowed = allowed && recordingSchedule.activeSource == "phone"
        if !allowed { scheduleAttempted = false; return }
        guard recordingSchedule.scheduled, !recordingSchedule.manualOverride, recordingSchedule.source == "phone",
              microphone.active == nil, !microphone.requestingPermission, !scheduleAttempted,
              UIApplication.shared.applicationState == .active else { return }
        scheduleAttempted = true
        stopPlayback()
        Task { await microphone.start(archiveIdentity: archiveIdentity) }
    }
    private func armScheduleTimer() {
        // Keep the existing timer across foreground UI ticks; recompute only at a boundary or policy change.
        if let scheduledTransition, scheduledTransition > Date(), scheduleTimer?.isValid == true { return }
        if let scheduledTransition, scheduledTransition <= Date() { scheduleAttempted = false }
        scheduleTimer?.invalidate(); scheduleTimer = nil; scheduledTransition = nil
        guard let transition = recordingSchedule.nextTransition(after: Date()) else { return }
        scheduledTransition = transition
        let timer = Timer(fire: transition, interval: 0, repeats: false) { [weak self] _ in
            Task { @MainActor in
                self?.scheduledTransition = nil
                self?.scheduleAttempted = false
                self?.applyRecordingSchedule()
            }
        }
        timer.tolerance = 0.1
        scheduleTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }
    let managedAccount = ManagedAccountSession()
    private(set) var usesManagedHosting = false
    @ObservationIgnored private var appliedManagedSessionID: UUID?
    @ObservationIgnored private var managedRouter: BackendEndpointRouter?
    @ObservationIgnored private var managedExpiryObserver: NSObjectProtocol?
    var destinationIdentity: String {
        usesManagedHosting ? (managedAccount.origin?.destination(accountID: managedAccount.accountID ?? "signed-out") ?? "managed:unconfigured")
            : ManagedServiceOrigin.ownDestination(archiveIdentity: backendConfiguration.archiveIdentity)
    }
    private var profilePreferenceKey: String { usesManagedHosting ? destinationIdentity + ".selectedProfile" : "selectedProfileID" }
    let bluetooth = OmiBluetooth()
    let locations = PhoneLocation()
    var recordingLocationsEnabled: Bool {
        didSet {
            UserDefaults.standard.set(recordingLocationsEnabled, forKey: "recordingLocationsEnabled")
            if recordingLocationsEnabled { locations.requestPermission() }
            updateLocationCapture()
        }
    }
    private func updateLocationCapture() {
        locations.setCaptureActive(recordingLocationsEnabled && hasSelectedProfile && pendantCaptureAllowed && bluetooth.connected && store != nil)
    }
    private(set) var backendConfiguration: BackendEndpointConfiguration
    var backendAddress: String { usesManagedHosting ? managedAccount.origin?.url.absoluteString ?? "" : backendConfiguration.activeEndpoint?.address ?? "" }
    var backendEndpoints: [BackendEndpoint] { backendConfiguration.endpoints }
    struct Profile: Codable, Identifiable, Hashable {
        let id: String
        let name: String
    }
    struct ProfileDirectory: Decodable { let profiles: [Profile] }
    private(set) var profiles: [Profile] = [.init(id: "default", name: L10n.source("Default"))]
    private(set) var selectedProfileID: String
    private var managedConnectionMatches: Bool {
        usesManagedHosting && managedAccount.signedIn && appliedManagedSessionID == managedAccount.sessionID
            && store?.destinationID == destinationIdentity
    }
    var hasSelectedProfile: Bool {
        !selectedProfileID.isEmpty && ((!usesManagedHosting && !managedAccount.signedIn)
            || (managedConnectionMatches && managedAccount.account?.canAccessArchive == true))
    }
    var selectedProfileName: String { profiles.first { $0.id == selectedProfileID }?.name ?? selectedProfileID }
    // Preserve legacy default caches while isolating every additional profile.
    var archiveIdentity: String {
        let root = usesManagedHosting ? destinationIdentity : backendConfiguration.archiveIdentity
        return selectedProfileID == "default" ? root : root + ".profile." + selectedProfileID
    }

    private func resetArchive() {
        microphone.selectArchive(archiveIdentity)
        scheduleAttempted = false
        backendGeneration = UUID()
        stopPlayback()
        serverTimeZoneIdentifier = UserDefaults.standard.string(forKey: (usesManagedHosting ? archiveIdentity : backendAddress) + ".timezone")
        serverTimeline = LocalCache.load(archiveIdentity + ".timeline") ?? .null
        importanceMarks = []; importanceLoaded = false; importanceError = nil; importanceRequest = nil; importanceSaving = []
        importanceRevision = UUID()
        labelRevision = ""; archiveRevision = ""
        serverStatus = L10n.source("Not connected"); completenessStatus = .null; statusRefreshFailed = false
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
        locations.setCaptureActive(false)
        UserDefaults.standard.set(id, forKey: profilePreferenceKey)
        syncShareImportConfiguration()
        store?.profileID = id
        bluetooth.captureEnabled = hasSelectedProfile && pendantCaptureAllowed && store != nil
        nextAudioDate = nil; disconnectedAt = nil
        lastDecodedAt = nil; lastPersistedAt = nil
        resetArchive()
        recordCaptureState(paused ? "paused" : "unknown", cause: paused ? "user_pause" : "unknown")
        notice = profile.map { L10n.source("Profile: \($0.name)") } ?? L10n.source("Choose a profile in Settings")
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
    var singleClickAction: WearableAction { didSet { UserDefaults.standard.set(singleClickAction.rawValue, forKey: "singleClickAction") } }
    var doubleClickAction: WearableAction { didSet { UserDefaults.standard.set(doubleClickAction.rawValue, forKey: "doubleClickAction") } }
    var clickNotificationsEnabled: Bool { didSet { UserDefaults.standard.set(clickNotificationsEnabled, forKey: ClickNotification.preferenceKey) } }
    var recentAudioSeconds: Int { didSet { UserDefaults.standard.set(recentAudioSeconds, forKey: "recentAudioSeconds") } }
    var paused: Bool {
        didSet {
            UserDefaults.standard.set(paused, forKey: "capturePaused")
            if paused != oldValue { updateLocationCapture() }
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
    var notice = L10n.source("Pair your Omi to begin")
    var queueCount = 0
    var uploadFeedback: UploadFeedback?
    var unsyncedMinutes = 0.0
    var storageMB = 0.0
    var recentGaps: [OutboxItem] = []
    var queueErrors: [String] = []
    var meetingStart: Date?
    var meetingID: UUID?
    var meetingQualifiesAt: Date?
    let markerCooldownSeconds = 60
    let meetingMinSeconds = 60
    var nextMarkAt: Date?
    var serverStatus = L10n.source("Not connected")
    private(set) var statusRefreshFailed = false
    var serverTimeline: JSONValue = .null
    var completenessStatus: JSONValue = .null
    var queuedRecordings = 0
    var recordingRequested: Bool {
        microphone.active != nil || (bluetooth.connected && bluetooth.captureEnabled)
    }
    var canRewind: Bool {
        guard hasSelectedProfile else { return false }
        if omiRecording || store?.canRewind(minutes: 10, end: now) == true { return true }
        guard !statusRefreshFailed, let at = Self.date(completenessStatus["as_of"].text),
              now.timeIntervalSince(at) >= 0, now.timeIntervalSince(at) <= 120,
              let captured = Self.date(completenessStatus["ingestion"]["last_captured_at"].text) else { return false }
        return now.timeIntervalSince(captured) >= 0 && now.timeIntervalSince(captured) < 600
    }
    var omiRecording: Bool {
        pendantCaptureAllowed && bluetooth.connected && bluetooth.captureEnabled &&
        lastDecodedAt.map { abs(now.timeIntervalSince($0)) < 2 } == true
    }
    // Diagnostics refresh with the foreground clock rather than on every BLE frame.
    @ObservationIgnored var lastDecodedAt: Date?
    @ObservationIgnored var lastPersistedAt: Date?
    var captureFailure: String? = UserDefaults.standard.string(forKey: "captureFailure")
    private(set) var observedCaptureState = "unknown"
    @ObservationIgnored private var phoneID: UUID = {
        if let value = UserDefaults.standard.string(forKey: "phoneInstallationID"), let id = UUID(uuidString: value) { return id }
        let id = UUID(); UserDefaults.standard.set(id.uuidString, forKey: "phoneInstallationID"); return id
    }()
    var now = Date()
    var player: AVAudioPlayer?
    @ObservationIgnored private let playbackDelegate = AudioPlaybackDelegate()
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
    @ObservationIgnored private var lastLocalCheck = Date.distantPast
    @ObservationIgnored private var backendGeneration = UUID()
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
        let controls = ClickControls(singleClick: defaults.string(forKey: "singleClickAction"),
                                     doubleClick: defaults.string(forKey: "doubleClickAction"),
                                     durationSeconds: defaults.integer(forKey: "recentAudioSeconds"))
        singleClickAction = controls.singleClick; doubleClickAction = controls.doubleClick
        recentAudioSeconds = controls.durationSeconds
        clickNotificationsEnabled = ClickNotification.enabled(in: defaults)
        recordingLocationsEnabled = defaults.bool(forKey: "recordingLocationsEnabled")
        paused = defaults.bool(forKey: "capturePaused")
        uploadWiFiOnly = defaults.bool(forKey: "uploadWiFiOnly")
        storageBudgetMB = defaults.object(forKey: "storageBudgetMB") as? Int ?? 2000
        serverTimeZoneIdentifier = defaults.string(forKey: activeAddress + ".timezone")
        serverTimeline = LocalCache.load(archiveKey + ".timeline") ?? .null
        do {
            var root = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            var backupPolicy = URLResourceValues(); backupPolicy.isExcludedFromBackup = true
            try root.setResourceValues(backupPolicy)
            let capture = root.appendingPathComponent(L10n.source("Capture"), isDirectory: true)
            store = try DurableStore(directory: capture)
            try store?.bindLegacyDestination(destinationIdentity)
            store?.destinationID = destinationIdentity
            store?.profileID = selectedProfileID
            store?.maximumBytes = Int64(storageBudgetMB) * 1_000_000
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: capture.path)
            if let store { uploader = BackgroundUploader(store: store, uploadWiFiOnly: uploadWiFiOnly) }
            disconnectedAt = store?.items.filter { $0.destinationID == destinationIdentity && $0.resolvedProfileID == selectedProfileID && $0.kind == .audio }.compactMap(\.endedAt).max()
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
        } catch { self.error = L10n.error(error) }
        bluetooth.captureEnabled = hasSelectedProfile && pendantCaptureAllowed && store != nil
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
        managedAccount.onChange = { [weak self] in self?.managedAccountChanged() }
        managedExpiryObserver = NotificationCenter.default.addObserver(forName: .managedSessionExpired, object: nil, queue: .main) { [weak self] notification in
            let expected = notification.userInfo?["sessionID"] as? UUID
            Task { @MainActor in self?.managedAccount.expire(expected: expected) }
        }
        if defaults.bool(forKey: "managedArchiveSelected") || managedAccount.signedIn {
            do { try setManagedMode(true) } catch { self.error = L10n.error(error) }
        }
        applyRecordingSchedule()
        playbackDelegate.onFinished = { [weak self] player in
            guard let self, self.player === player else { return }
            self.stopPlayback()
        }
        microphone.onRecordingLimit = { [weak self] in
            self?.scheduleAttempted = false
            self?.applyRecordingSchedule()
        }
        clockObserver = NotificationCenter.default.addObserver(forName: UIApplication.significantTimeChangeNotification,
                                                               object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in
                self?.scheduleTimer?.invalidate(); self?.scheduledTransition = nil
                self?.applyRecordingSchedule()
            }
        }
        reconnectBackend()
        if managedAccount.signedIn { Task { await managedAccount.refresh() } }
    }
    func client() throws -> BackendClient {
        if usesManagedHosting || managedAccount.signedIn {
            guard managedConnectionMatches, let managedRouter, let authorization = managedAccount.authorization else {
                throw BackendFailure(message: L10n.source("Sign in to your Daythread account to continue."), status: 401)
            }
            guard managedAccount.hostingVerified else {
                throw BackendFailure(message: L10n.source("Refresh your account to connect to managed hosting."))
            }
            guard managedAccount.account?.canAccessArchive == true else {
                throw BackendFailure(message: L10n.source("Managed hosting is not active for this account."), status: 402)
            }
            return try BackendClient(router: managedRouter, profileID: selectedProfileID, authorization: authorization)
        }
        return try BackendClient(router: endpointRouter, profileID: selectedProfileID)
    }
    private func managedAccountChanged() {
        if managedAccount.signedIn {
            if !usesManagedHosting || appliedManagedSessionID != managedAccount.sessionID {
                do { try setManagedMode(true) } catch { self.error = L10n.error(error); bluetooth.captureEnabled = false; locations.setCaptureActive(false); uploader?.client = nil; profiles = []; resetArchive(); return }
            }
            bluetooth.captureEnabled = hasSelectedProfile && pendantCaptureAllowed && store != nil
            if !hasSelectedProfile { flush() }
            updateLocationCapture(); reconnectBackend()
        } else if usesManagedHosting {
            flush(); bluetooth.captureEnabled = false; locations.setCaptureActive(false)
            uploader?.client = nil; selectedProfileID = ""; profiles = []; resetArchive()
        }
    }
    func setManagedMode(_ managed: Bool) throws {
        guard managed || !managedAccount.signedIn else { throw BackendFailure(message: L10n.source("The managed service address is fixed by this app.")) }
        guard !managed || managedAccount.origin != nil else {
            throw BackendFailure(message: L10n.source("Managed sign-in is not configured in this app build."))
        }
        flush()
        guard store?.isRecording != true else {
            throw BackendFailure(message: L10n.source("Finish saving the current recording before changing connections."))
        }
        bluetooth.captureEnabled = false; locations.setCaptureActive(false); uploader?.client = nil
        let previousKey = usesManagedHosting ? (store?.destinationID ?? destinationIdentity) + ".selectedProfile" : "selectedProfileID"
        if !selectedProfileID.isEmpty { UserDefaults.standard.set(selectedProfileID, forKey: previousKey) }
        usesManagedHosting = managed
        appliedManagedSessionID = managed ? managedAccount.sessionID : nil
        UserDefaults.standard.set(managed, forKey: "managedArchiveSelected")
        if managed, let origin = managedAccount.origin {
            let endpoint = BackendEndpoint(address: origin.url.absoluteString)
            managedRouter = BackendEndpointRouter(configuration: .init(endpoints: [endpoint], activeID: endpoint.id, archiveIdentity: destinationIdentity))
        } else { managedRouter = nil }
        selectedProfileID = UserDefaults.standard.string(forKey: profilePreferenceKey) ?? (managed && managedAccount.signedIn ? "default" : "")
        store?.destinationID = destinationIdentity; store?.profileID = selectedProfileID
        profiles = []; nextAudioDate = nil; disconnectedAt = nil; lastDecodedAt = nil; lastPersistedAt = nil
        resetArchive(); bluetooth.captureEnabled = hasSelectedProfile && pendantCaptureAllowed && store != nil
        updateLocationCapture(); reconnectBackend()
        syncShareImportConfiguration()
    }
    func useOwnBackend() async throws {
        flush()
        guard store?.isRecording != true else { throw BackendFailure(message: L10n.source("Finish saving the current recording before changing connections.")) }
        try await managedAccount.revokeAndSignOut()
        try setManagedMode(false)
    }
    func reconnectBackend() {
        guard hasSelectedProfile, !usesManagedHosting || (managedAccount.hostingVerified && managedAccount.account?.hosting_active == true) else { uploader?.client = nil; return }
        do { uploader?.client = try client(); uploader?.retry() }
        catch { self.error = L10n.error(error) }
    }

    func saveEndpoint(address: String, replacing id: UUID? = nil) throws {
        guard !usesManagedHosting && !managedAccount.signedIn else { throw BackendFailure(message: L10n.source("The managed service address is fixed by this app.")) }
        let normalized = try BackendClient.validatedURL(address).absoluteString
        flush()
        guard store?.isRecording != true else { throw BackendFailure(message: L10n.source("Finish saving the current recording before changing connections.")) }
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
        guard !usesManagedHosting && !managedAccount.signedIn else { throw BackendFailure(message: L10n.source("The managed service address is fixed by this app.")) }
        guard backendConfiguration.endpoints.count > 1 else {
            throw BackendFailure(message: L10n.source("Keep at least one backend endpoint configured."))
        }
        var configuration = backendConfiguration
        configuration.remove(id: id)
        applyEndpointConfiguration(configuration)
    }

    func moveEndpoints(from offsets: IndexSet, to destination: Int) {
        guard !usesManagedHosting && !managedAccount.signedIn else { return }
        var configuration = backendConfiguration
        configuration.move(fromOffsets: offsets, toOffset: destination)
        applyEndpointConfiguration(configuration)
    }

    func activateEndpoint(_ id: UUID) {
        guard !usesManagedHosting && !managedAccount.signedIn else { return }
        flush(); guard store?.isRecording != true else { return }
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
        store?.destinationID = destinationIdentity
        resetArchive()
        profiles = []
        reconnectBackend()
    }

    private func persistBackendConfiguration() {
        guard let data = try? JSONEncoder().encode(backendConfiguration) else { return }
        UserDefaults.standard.set(data, forKey: "backendEndpoints.v1")
        UserDefaults.standard.set(backendAddress, forKey: "backendURL")
        syncShareImportConfiguration()
    }

    private func syncShareImportConfiguration() {
        guard let shared = UserDefaults(suiteName: "group.com.daythread.app") else { return }
        shared.set(try? JSONEncoder().encode(backendConfiguration), forKey: "backendEndpoints.v1")
        shared.set(backendAddress, forKey: "backendURL")
        shared.set(selectedProfileID, forKey: "selectedProfileID")
        shared.set(usesManagedHosting || managedAccount.signedIn, forKey: "managedArchiveSelected")
    }
    func tick() async {
        now = Date(); applyRecordingSchedule(); uploader?.pump()
        if now.timeIntervalSince(lastLocalCheck) >= 5 { lastLocalCheck = now; refreshLocal() }
        let processing = completenessStatus["processing"]
        let generation = completenessStatus["generation"]
        let busy = queueCount > 0 || omiRecording || microphone.active != nil || processing["queued"].integer > 0 || processing["transcribing"].integer > 0 || generation["queued"].integer > 0 || generation["running"].integer > 0
        let interval: TimeInterval = ProcessInfo.processInfo.isLowPowerModeEnabled ? (busy ? 15 : 120) : (busy ? 5 : 60)
        if now.timeIntervalSince(lastStatusCheck) >= interval { await refreshStatus() }
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
        if managedAccount.signedIn && !managedAccount.hostingVerified { await managedAccount.refresh() }
        let generation = backendGeneration
        let address = backendAddress
        let identity = archiveIdentity
        do {
            let backend = try client()
            try await loadProfiles()
            guard backendGeneration == generation else { return }
            guard hasSelectedProfile else { serverStatus = L10n.source("Choose a profile in Settings"); return }
            let status = try await backend.request("v1/status")
            guard backendGeneration == generation else { return }
            LocalCache.reconcile(address: identity, revision: status["archive_revision"].text)
            archiveRevision = status["archive_revision"].text
            labelRevision = status["label_revision"].text
            if TimeZone(identifier: status["timezone"].text) != nil {
                serverTimeZoneIdentifier = status["timezone"].text
                UserDefaults.standard.set(serverTimeZoneIdentifier, forKey: (usesManagedHosting ? identity : address) + ".timezone")
            }
            completenessStatus = status; statusRefreshFailed = false
            serverStatus = status["worker_running"] == .bool(true) ? L10n.source("Home-core connected · worker running") : L10n.source("Home-core connected")
            let acknowledgedMeetingEvents = store?.items.filter { $0.destinationID == destinationIdentity && $0.resolvedProfileID == selectedProfileID && $0.acknowledged && $0.kind == .event && $0.action == .meetingToggle }.map { JSONValue.string($0.id.uuidString.lowercased()) } ?? []
            var timeline = try await backend.request("v1/timeline")
            if case .object(var object) = timeline {
                object["_local_observed_meeting_events"] = .array(acknowledgedMeetingEvents)
                timeline = .object(object)
            }
            guard backendGeneration == generation else { return }
            serverTimeline = timeline
            LocalCache.save(timeline, key: identity + ".timeline")
            refreshLocal()
            try await publishObservation(using: backend)
            await loadImportance()
        } catch {
            if backendGeneration == generation { serverStatus = L10n.error(error); statusRefreshFailed = true }
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
            importanceError = L10n.error(error)
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
    func setPaused(_ value: Bool) {
        if value { microphone.captureAllowed = false; flush() }
        else {
            // A failed finalization keeps the previous segment recoverable. Close it before accepting new frames.
            do { try store?.finish() } catch { failCapture(error); return }
        }
        captureFailure = nil; UserDefaults.standard.removeObject(forKey: "captureFailure")
        recordCaptureState(value ? "paused" : "unknown", cause: value ? "user_pause" : "resumed")
        paused = value; bluetooth.captureEnabled = hasSelectedProfile && pendantCaptureAllowed && store != nil
        applyRecordingSchedule()
        notice = value ? L10n.source("Recording stopped on this iPhone") : L10n.source("Recording ready on this iPhone")
        if value {
            disconnectedAt = nil
            nextAudioDate = nil
        } else if let codec = bluetooth.codec {
            do { decoder = try FrameDecoder(codec: codec) } catch { failCapture(error) }
        }

    }
    private func audio(_ data: Data, date suppliedDate: Date, uptime: TimeInterval) {
        // A BLE callback can wake the app after a suspended timer's deadline.
        if let scheduledTransition, suppliedDate >= scheduledTransition { applyRecordingSchedule() }
        guard hasSelectedProfile, bluetooth.captureEnabled, decoder != nil, store != nil else { return }
        let date = clock.date(at: uptime)
        if let lastNotification, date.timeIntervalSince(lastNotification) > 1 {
            flush(); addGap(start: nextAudioDate ?? lastNotification, end: date, reason: L10n.source("BLE audio delivery interrupted"), cause: "delivery_interrupted")
            nextAudioDate = nil
            do { decoder = try FrameDecoder(codec: bluetooth.codec ?? -1) } catch { failCapture(error); return }
        }
        self.lastNotification = date
        let result = reassembler.consume(data)
        if result.lost {
            do { try store?.finish() } catch { failCapture(error); return }
            addGap(start: nextAudioDate ?? date.addingTimeInterval(-0.02), end: date, reason: L10n.source("BLE sequence or fragment loss"), cause: "packet_loss")
            pendingClock = nil; nextAudioDate = nil
            do { decoder = try FrameDecoder(codec: bluetooth.codec ?? -1) } catch { failCapture(error); return }
        }
        if let frame = result.frame, let clock = pendingClock { record(frame, clock: clock) }
        if result.startedFrame {
            if let disconnectedAt {
                addGap(start: disconnectedAt, end: date, reason: disconnectedCause == "connection" ? L10n.source("Capture disconnected") : L10n.source("Capture disconnected or paused"), cause: disconnectedCause)
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
            if !store.isRecording {
                updateLocationCapture()
                try store.begin(deviceID: bluetooth.identifier, codec: codec, clock: clock,
                                location: recordingLocationsEnabled ? locations.latest : nil)
            }
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
                       reason: L10n.source("Incomplete final BLE frame discarded"), cause: "incomplete_frame")
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
        locations.setCaptureActive(false)
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
        self.error = L10n.error(error); paused = true; bluetooth.captureEnabled = false
        disconnectedAt = nextAudioDate ?? clock.date(at: ProcessInfo.processInfo.systemUptime); nextAudioDate = nil
        reassembler.reset(); pendingClock = nil
        do { try store?.finish() } catch { self.error = L10n.source("\(self.error ?? "") · Could not close segment: \(L10n.error(error))") }
        refreshLocal()
    }
    private func button(_ data: Data) {
        guard let click = WearableClick(data) else { return }
        let execution = ClickBackgroundExecution()
        bluetooth.playHaptic(click.hapticDuration) { execution.complete(.haptic) }
        let end = clock.date(at: ProcessInfo.processInfo.systemUptime)
        let action = click.action(single: singleClickAction, double: doubleClickAction)
        let (feedback, jobID) = performClickAction(action, at: end)
        // Submit the local acknowledgement before allowing this job onto the network.
        let uploader = uploader
        if let jobID { uploader?.holdGeneration(jobID) }
        let confirmation = CompletionNotifications.shared.acknowledgeClick(click, feedback: feedback, jobID: jobID,
                                                        enabled: clickNotificationsEnabled) { [weak uploader] in
            if let jobID { uploader?.releaseGeneration(jobID) }
            execution.complete(.confirmation)
        }
        execution.onExpiration = { [weak uploader] in
            confirmation?.cancel()
            if let jobID { uploader?.releaseGeneration(jobID) }
        }
    }
    private func performClickAction(_ action: WearableAction, at end: Date) -> (ClickActionFeedback, UUID?) {
        if action == .none { return (.noAction, nil) }
        guard hasSelectedProfile else { return (.failed(L10n.text("Choose a profile in Settings first.")), nil) }
        switch action {
        case .mark: return (event(.mark), nil)
        case .meetingToggle: return (event(.meetingToggle), nil)
        case .captureToggle:
            error = nil
            let pause = liveCaptureAllowed && (bluetooth.captureEnabled || microphone.active != nil)
            if pause { setPaused(true) } else { startManualPendant() }
            if let error { return (.failed(L10n.key(error)), nil) }
            guard pause ? paused : bluetooth.captureEnabled else {
                return (.failed(L10n.text("Capture storage unavailable")), nil)
            }
            return (pause ? .capturePaused : .captureResumed, nil)
        case .summarize, .answer, .translate:
            error = nil; checkpoint()
            if let error { return (.failed(L10n.key(error)), nil) }
            guard let store else {
                error = L10n.source("Capture storage unavailable")
                return (.failed(L10n.text("Capture storage unavailable")), nil)
            }
            do {
                let jobID = try store.wearableAction(deviceID: bluetooth.identifier, at: end, action: action,
                                                    durationSeconds: recentAudioSeconds)
                notice = L10n.source("Recent audio request queued. The result will appear in Notifications.")
                refreshLocal()
                return (.queued(action, seconds: recentAudioSeconds), jobID)
            } catch { self.error = L10n.error(error); return (.failed(L10n.error(error)), nil) }
        case .none: return (.noAction, nil)
        }
    }
    @discardableResult
    func event(_ action: SemanticAction) -> ClickActionFeedback {
        guard hasSelectedProfile else {
            error = L10n.source("Choose a profile in Settings first.")
            return .failed(L10n.text("Choose a profile in Settings first."))
        }
        let date = clock.date(at: ProcessInfo.processInfo.systemUptime)
        if action == .mark, let nextMarkAt, date < nextMarkAt {
            let feedback = ClickActionFeedback.alreadyMarked(seconds: Int(ceil(nextMarkAt.timeIntervalSince(date))))
            notice = feedback.message
            return feedback
        }
        guard let store else {
            error = L10n.source("Capture storage unavailable")
            return .failed(L10n.text("Capture storage unavailable"))
        }
        do {
            let oldStart = meetingStart
            let oldQualification = meetingQualifiesAt
            try store.event(deviceID: recordingSchedule.hintDeviceID(phoneID: phoneID, pendantID: bluetooth.identifier), at: date, action: action, meetingIntent: action == .meetingToggle ? (oldStart == nil ? "start" : "end") : nil, meetingID: action == .meetingToggle && oldStart != nil ? meetingID : nil)
            let feedback: ClickActionFeedback
            if action == .mark { feedback = .marked }
            else if let oldStart {
                let qualifies = oldQualification ?? oldStart.addingTimeInterval(Double(meetingMinSeconds))
                feedback = date < qualifies ? .shortMeetingIgnored : .meetingEnded
            } else { feedback = .meetingStarted(provisionalSeconds: meetingMinSeconds) }
            notice = feedback.message
            refreshLocal(); uploader?.pump()
            return feedback
        } catch { self.error = L10n.error(error); return .failed(L10n.error(error)) }
    }
    private func refreshLocal() {
        guard let store else { return }
        let items = store.items.filter { $0.destinationID == destinationIdentity && $0.resolvedProfileID == selectedProfileID }
        let pending = items.filter { !$0.acknowledged && $0.ready }
        queueCount = pending.count
        queuedRecordings = pending.filter { $0.kind == .audio }.count
        uploadFeedback = uploader?.feedback(profileID: selectedProfileID)
        unsyncedMinutes = items.filter { $0.kind == .audio && !$0.acknowledged }.reduce(0) { $0 + max(0, ($1.endedAt ?? $1.capturedAt).timeIntervalSince($1.capturedAt)) } / 60
        storageMB = Double(store.storageBytes) / 1_000_000
        recentGaps = Array(items.filter { $0.kind == .gap }.suffix(5).reversed())
        queueErrors = Array(items.compactMap(\.error).suffix(3))
        nextMarkAt = items.compactMap(\.flagAvailableAgainAt).max()
        if let remoteMark = serverTimeline["markers"].array.compactMap({ Self.date($0["occurred_at"].text) }).max() {
            let remoteNext = remoteMark.addingTimeInterval(Double(markerCooldownSeconds))
            nextMarkAt = max(nextMarkAt ?? .distantPast, remoteNext)
        }
        // Start with the server's authoritative meeting state, then apply local events
        // it has not observed. Explicit ends cannot reopen an automatically ended meeting.
        var start = Self.date(serverTimeline["active_meeting"]["started_at"].text)
        var id = UUID(uuidString: serverTimeline["active_meeting"]["id"].text)
        var qualifies = Self.date(serverTimeline["active_meeting"]["qualifies_at"].text)
        let knownEvents = Set(serverTimeline["_local_observed_meeting_events"].array.map(\.text))
        let hasObservedEvents = serverTimeline["_local_observed_meeting_events"] != .null
        let localEvents = items.filter {
            $0.kind == .event && $0.action == .meetingToggle &&
            (serverTimeline == .null || !$0.acknowledged || (hasObservedEvents && !knownEvents.contains($0.id.uuidString.lowercased())))
        }.sorted {
            $0.capturedAt == $1.capturedAt ? $0.id.uuidString < $1.id.uuidString : $0.capturedAt < $1.capturedAt
        }
        for item in localEvents {
            if item.meetingIntent == "end" {
                if id == item.meetingID { start = nil; id = nil; qualifies = nil }
            } else if item.meetingIntent == "start" {
                if start == nil { start = item.capturedAt; id = item.id; qualifies = item.capturedAt.addingTimeInterval(60) }
            } else if start != nil {
                start = nil; id = nil; qualifies = nil
            } else {
                start = item.capturedAt; id = item.id; qualifies = item.capturedAt.addingTimeInterval(60)
            }
        }
        meetingStart = start; meetingID = id; meetingQualifiesAt = start == nil ? nil : qualifies

    }
    private func recordCaptureState(_ state: String, cause: String) {
        guard hasSelectedProfile else { return }
        observedCaptureState = state
        do { try store?.captureState(phoneID: phoneID, at: Date(), state: state, cause: cause) }
        catch { self.error = L10n.source("Could not persist capture observation: \(L10n.error(error))") }
    }
    private func publishObservation(using backend: BackendClient) async throws {
        guard hasSelectedProfile, let store else { return }
        let sequence = (UserDefaults.standard.object(forKey: "phoneObservationSequence") as? Int ?? 0) + 1
        UserDefaults.standard.set(sequence, forKey: "phoneObservationSequence")
        let pending = store.items.filter { $0.destinationID == backend.destinationIdentity && $0.resolvedProfileID == backend.profileID && $0.kind == .audio && !$0.acknowledged }
        let drafts = microphone.recordings.filter { $0.id != microphone.active?.id }
        let observedAt = Date()
        let microphoneActive = microphone.active != nil
        let pendantActive = pendantCaptureAllowed && bluetooth.captureEnabled && bluetooth.connected && lastDecodedAt.map { abs(observedAt.timeIntervalSince($0)) < 2 } == true
        let state = microphoneActive || pendantActive ? "capturing" : captureFailure != nil ? "blocked" : !liveCaptureAllowed ? "paused" : !bluetooth.connected ? "disconnected" : "unknown"
        var body: [String: Any] = [
            "sequence": sequence, "observed_at": DurableStore.stamp(observedAt),
            "device_id": bluetooth.identifier, "capture_state": state,
            "capture_source": microphoneActive ? "phone" : "omi",
            "cause": captureFailure ?? (paused ? "user_pause" : !bluetooth.connected ? "connection" : "unknown"),
            "requested_pause": paused && captureFailure == nil,
            "pending_recordings": pending.filter(\.ready).count + drafts.count,
            "recovery_items": pending.filter { !$0.ready }.count,
            "pending_seconds": pending.reduce(0.0) { $0 + max(0, ($1.endedAt ?? $1.capturedAt).timeIntervalSince($1.capturedAt)) } + drafts.reduce(0.0) { $0 + max(0, ($1.endedAt ?? $1.startedAt).timeIntervalSince($1.startedAt)) },
            "upload_policy": uploadWiFiOnly ? "wifi_only" : "any_network",
            "upload_state": uploader?.observedState ?? "unknown",
            "storage_bytes": store.storageBytes, "storage_budget_bytes": Int64(storageBudgetMB) * 1_000_000,
        ]
        if microphoneActive { body["last_decoded_at"] = DurableStore.stamp(observedAt) }
        else if let lastDecodedAt { body["last_decoded_at"] = DurableStore.stamp(lastDecodedAt) }
        if let lastPersistedAt { body["last_persisted_at"] = DurableStore.stamp(lastPersistedAt) }
        if let oldest = (pending.map(\.capturedAt) + drafts.map(\.startedAt)).min() { body["oldest_capture_at"] = DurableStore.stamp(oldest) }
        _ = try await backend.request("v1/phones/\(phoneID.uuidString)/status", method: "PUT", body: body)
    }
    func retryUploads() { uploader?.retry() }
    func stopPlayback() {
        let hadPlayer = player != nil
        player?.stop(); player = nil; playbackStatus = ""
        if hadPlayer, microphone.active == nil {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        }
    }
    func play(_ url: URL, startAt: TimeInterval = 0, rate: Float = 1) {
        guard microphone.active == nil else {
            error = L10n.source("Stop recording before playing audio."); return
        }
        stopPlayback()
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
            try AVAudioSession.sharedInstance().setActive(true)
            let audio = try AVAudioPlayer(contentsOf: url)
            audio.enableRate = true
            audio.rate = min(2, max(0.5, rate.isFinite ? rate : 1))
            audio.currentTime = min(audio.duration, max(0, startAt.isFinite ? startAt : 0))
            audio.delegate = playbackDelegate; player = audio
            guard audio.prepareToPlay(), audio.play() else {
                stopPlayback(); return
            }
            playbackStatus = L10n.source("Playing recorded audio")
        } catch {
            stopPlayback()
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            self.error = L10n.error(error)
        }
    }
    func localRewind(minutes: Int, end: Date) throws -> Int {
        checkpoint(); guard let store else { throw BackendFailure(message: L10n.source("Capture storage unavailable")) }
        let result = try store.rewind(minutes: minutes, end: end); play(result.url); return result.gaps
    }
    nonisolated static func date(_ string: String) -> Date? {
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: string) ?? ISO8601DateFormatter().date(from: string)
    }
}

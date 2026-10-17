import AVFoundation
import Observation
import OmiCore

/// Owns microphone capture independently of the Capture tab's lifetime.
@MainActor @Observable
final class PhoneRecorder: NSObject, AVAudioRecorderDelegate {
    private(set) var recordings: [LocalRecordingDraft] = []
    private(set) var active: LocalRecordingDraft?
    private(set) var requestingPermission = false
    private(set) var recoveryWarning: String?
    private(set) var uploadingID: UUID?
    var decisionID: UUID?
    var error: String?
    var importer: RecordingImporter?
    @ObservationIgnored private var store: LocalRecordingStore?
    @ObservationIgnored private var recorder: AVAudioRecorder?
    @ObservationIgnored private var uploadTask: Task<Void, Never>?
    @ObservationIgnored private var interruption: NSObjectProtocol?
    @ObservationIgnored private var archive = ""
    @ObservationIgnored private var archiveGeneration = UUID()

    override init() {
        super.init()
        do {
            let root = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                   appropriateFor: nil, create: true)
            store = try LocalRecordingStore(directory: root.appendingPathComponent("MicrophoneRecordings", isDirectory: true))
        } catch { self.error = L10n.error(error) }
        interruption = NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification,
                                                               object: nil, queue: .main) { [weak self] notification in
            guard let raw = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  raw == AVAudioSession.InterruptionType.began.rawValue else { return }
            Task { @MainActor in
                guard let self, self.active != nil else { return }
                self.stop()
                self.error = L10n.source("Recording was interrupted. The audio captured so far is saved on this iPhone.")
            }
        }
    }

    func selectArchive(_ identity: String) {
        guard archive != identity else { reload(); return }
        stop()
        uploadTask?.cancel()
        importer = nil; uploadingID = nil; decisionID = nil
        archive = identity
        archiveGeneration = UUID()
        reload()
    }

    func start(archiveIdentity: String) async {
        guard active == nil, !requestingPermission else { return }
        selectArchive(archiveIdentity)
        let generation = archiveGeneration
        error = nil
        requestingPermission = true
        defer { requestingPermission = false }
        let granted = await AVAudioApplication.requestRecordPermission()
        guard archiveGeneration == generation, !Task.isCancelled else { return }
        guard granted else {
            error = L10n.source("Allow microphone access in iPhone Settings to record audio."); return
        }
        guard let store else { error = L10n.source("Recording storage is unavailable."); return }
        var draft: LocalRecordingDraft?
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
            try session.setActive(true)
            if let microphone = session.availableInputs?.first(where: { $0.portType == .builtInMic }) {
                try session.setPreferredInput(microphone)
            }
            let item = try store.create(archiveIdentity: archiveIdentity, startedAt: Date())
            draft = item
            let audio = try AVAudioRecorder(url: store.fileURL(for: item), settings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 16_000,
                AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 32_000,
                AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
            ])
            audio.delegate = self
            guard audio.record(forDuration: 7200) else {
                throw RecordingImportError(L10n.source("The microphone could not start recording. Try again."))
            }
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
                                                  ofItemAtPath: store.fileURL(for: item).path)
            recorder = audio; active = item
            reload()
        } catch {
            recorder?.stop(); recorder = nil
            if let draft { try? store.remove(id: draft.id) }
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            self.error = L10n.error(error)
        }
    }

    func stop() {
        guard let item = active, let store else { return }
        // Use captured audio duration, including when a call interrupts the session.
        var duration = recorder?.currentTime ?? 0
        recorder?.stop(); recorder = nil; active = nil
        if let audio = try? AVAudioFile(forReading: store.fileURL(for: item)), audio.processingFormat.sampleRate > 0 {
            duration = Double(audio.length) / audio.processingFormat.sampleRate
        }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        do {
            _ = try store.finish(id: item.id, endedAt: item.startedAt.addingTimeInterval(max(0, duration)))
            decisionID = item.id
        } catch { self.error = L10n.error(error) }
        reload()
    }

    func remove(_ item: LocalRecordingDraft) {
        guard item.id != active?.id, item.id != uploadingID else { return }
        do {
            try store?.remove(id: item.id)
            if decisionID == item.id { decisionID = nil }
            reload()
        } catch { self.error = L10n.error(error) }
    }

    func play(_ item: LocalRecordingDraft, model: AppModel) {
        guard let store else { return }
        model.play(store.fileURL(for: item))
    }

    func upload(_ item: LocalRecordingDraft, model: AppModel) {
        guard uploadingID == nil, item.id != active?.id, item.archiveIdentity == model.archiveIdentity,
              let store else { return }
        error = nil
        let identity = model.archiveIdentity
        let importer = RecordingImporter()
        self.importer = importer; uploadingID = item.id
        uploadTask = Task { [weak self] in
            defer {
                if self?.importer === importer { self?.uploadingID = nil; self?.reload() }
            }
            do {
                let client = try model.client()
                await importer.add([store.fileURL(for: item)], client: client)
                try Task.checkCancellation()
                guard model.archiveIdentity == identity else { return }
                if !importer.recordings.isEmpty {
                    importer.recordings[0].recordedAt = item.startedAt
                    importer.recordings[0].reference = .start
                    importer.recordings[0].timeConfirmed = true
                }
                await importer.upload(client: client, wiFiOnly: model.uploadWiFiOnly) {
                    model.archiveRevision = UUID().uuidString
                }
                try Task.checkCancellation()
                guard model.archiveIdentity == identity else { return }
                if importer.allHandled {
                    try store.remove(id: item.id)
                    if self?.decisionID == item.id { self?.decisionID = nil }
                } else {
                    self?.error = importer.recordings.first?.error ?? importer.error
                }
            } catch {
                if !Task.isCancelled, self?.importer === importer { self?.error = L10n.error(error) }
            }
        }
    }

    private func reload() {
        do {
            recordings = try store?.recordings(for: archive) ?? []
            recoveryWarning = store?.loadingIssues.isEmpty == false
                ? L10n.source("Some saved recording details could not be read. Their audio remains on this iPhone.") : nil
        }
        catch { self.error = L10n.error(error) }
    }

    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor [weak self] in
            guard let self, self.recorder === recorder else { return }
            self.stop()
            if !flag { self.error = L10n.source("Recording ended unexpectedly. Review the saved audio before uploading.") }
        }
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor [weak self] in
            guard let self, self.recorder === recorder else { return }
            self.stop()
            self.error = L10n.source("Recording ended unexpectedly. Review the saved audio before uploading.")
        }
    }
}

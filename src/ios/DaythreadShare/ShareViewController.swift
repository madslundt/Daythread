import UIKit
import UniformTypeIdentifiers
import CoreLocation
import OmiCore

final class ShareViewController: UIViewController {
    var audioProviders: [NSItemProvider] = []
    let status = UILabel()
    let progressBar = UIProgressView(progressViewStyle: .default)
    let saveButton = UIButton(type: .system)
    let cancelButton = UIButton(type: .system)
    let openAppButton = UIButton(type: .system)
    let tagsButton = UIButton(type: .system)
    let locationButton = UIButton(type: .system)
    var stagedURLs: [URL] = []
    var timeEditors: [URL: ShareRecordingTimeView] = [:]
    let recordingOptions = UIStackView()
    var plans: [URL: RecordingUploadPlan] = [:]
    var pendingTagNames = Set<String>()
    var staging = false
    var busy = false
    var uploadOperation: Task<Void, Never>?
    var activeUploadTask: URLSessionUploadTask?
    var didCancelUpload = false
    var endpoints: BackendEndpointConfiguration?
    var profileID = ""
    var availableTags: [ShareTag] = []
    var locationZones: [ShareZone] = []
    var selectedTagIDs = Set<String>()
    var selectedPlace: (zoneID: String, locationID: String)?
    var currentLocation: CaptureLocation?
    let locationReader = ShareLocationReader()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.09, green: 0.085, blue: 0.08, alpha: 1)
                : UIColor(red: 0.97, green: 0.955, blue: 0.93, alpha: 1)
        }
        title = "Daythread"
        audioProviders = (extensionContext?.inputItems as? [NSExtensionItem] ?? [])
            .flatMap { $0.attachments ?? [] }
            .filter { provider in
                provider.registeredTypeIdentifiers.contains { UTType($0)?.conforms(to: .audio) == true }
            }

        if let shared = UserDefaults(suiteName: "group.com.daythread.app") {
            endpoints = shared.data(forKey: "backendEndpoints.v1").flatMap { try? JSONDecoder().decode(BackendEndpointConfiguration.self, from: $0) }
            profileID = shared.string(forKey: "selectedProfileID") ?? ""
        }

        let icon = UIImageView(image: UIImage(systemName: "waveform.badge.plus"))
        icon.tintColor = UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.90, green: 0.62, blue: 0.50, alpha: 1)
                : UIColor(red: 0.60, green: 0.32, blue: 0.24, alpha: 1)
        }
        icon.contentMode = .scaleAspectFit
        icon.heightAnchor.constraint(equalToConstant: 48).isActive = true

        let heading = UILabel()
        heading.text = "Import audio to Daythread"
        heading.font = UIFontMetrics(forTextStyle: .title2).scaledFont(
            for: UIFont(name: "Georgia", size: 27) ?? .systemFont(ofSize: 27)
        )
        heading.textColor = .label
        heading.textAlignment = .center

        status.text = audioProviders.isEmpty
            ? "No compatible audio files were shared."
            : "\(audioProviders.count) audio file\(audioProviders.count == 1 ? "" : "s") ready to import."
        status.font = .preferredFont(forTextStyle: .body)
        status.textColor = .secondaryLabel
        status.textAlignment = .center
        status.numberOfLines = 0
        status.accessibilityIdentifier = "share.status"

        progressBar.progressTintColor = UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.90, green: 0.62, blue: 0.50, alpha: 1)
                : UIColor(red: 0.60, green: 0.32, blue: 0.24, alpha: 1)
        }
        progressBar.trackTintColor = .tertiarySystemFill
        progressBar.isHidden = true
        progressBar.accessibilityLabel = "Upload progress"
        progressBar.accessibilityIdentifier = "share.progress"

        configureOptionButton(tagsButton, title: "Tags (optional)")
        configureOptionButton(locationButton, title: "Location (optional)")
        tagsButton.showsMenuAsPrimaryAction = true
        locationButton.showsMenuAsPrimaryAction = true

        saveButton.setTitle("Import to Daythread", for: .normal)
        saveButton.titleLabel?.font = .preferredFont(forTextStyle: .headline)
        var buttonStyle = UIButton.Configuration.filled()
        buttonStyle.cornerStyle = .large
        buttonStyle.contentInsets = NSDirectionalEdgeInsets(top: 15, leading: 22, bottom: 15, trailing: 22)
        buttonStyle.baseBackgroundColor = UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.90, green: 0.62, blue: 0.50, alpha: 1)
                : UIColor(red: 0.60, green: 0.32, blue: 0.24, alpha: 1)
        }
        buttonStyle.baseForegroundColor = UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.17, green: 0.10, blue: 0.08, alpha: 1)
                : .white
        }
        saveButton.configuration = buttonStyle
        saveButton.addTarget(self, action: #selector(saveAudio), for: .touchUpInside)
        saveButton.isEnabled = !audioProviders.isEmpty && endpoints != nil && !profileID.isEmpty
        if endpoints == nil || profileID.isEmpty {
            status.text = "Open Daythread and select an archive before sharing a recording."
        }
        saveButton.accessibilityIdentifier = "share.save"

        cancelButton.setTitle("Cancel", for: .normal)
        cancelButton.titleLabel?.font = .preferredFont(forTextStyle: .body)
        cancelButton.addTarget(self, action: #selector(cancelUpload), for: .touchUpInside)
        cancelButton.isHidden = true
        cancelButton.accessibilityIdentifier = "share.cancel"

        openAppButton.setTitle("Open Daythread", for: .normal)
        openAppButton.titleLabel?.font = .preferredFont(forTextStyle: .headline)
        var openButtonStyle = UIButton.Configuration.filled()
        openButtonStyle.cornerStyle = .large
        openButtonStyle.baseBackgroundColor = UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.90, green: 0.62, blue: 0.50, alpha: 1)
                : UIColor(red: 0.60, green: 0.32, blue: 0.24, alpha: 1)
        }
        openButtonStyle.baseForegroundColor = UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.17, green: 0.10, blue: 0.08, alpha: 1)
                : .white
        }
        openButtonStyle.contentInsets = NSDirectionalEdgeInsets(top: 15, leading: 22, bottom: 15, trailing: 22)
        openAppButton.configuration = openButtonStyle
        openAppButton.addTarget(self, action: #selector(openDaythread), for: .touchUpInside)
        openAppButton.isHidden = true
        openAppButton.accessibilityIdentifier = "share.openApp"

        let options = UIStackView(arrangedSubviews: [tagsButton, locationButton])
        options.axis = .horizontal; options.spacing = 10; options.distribution = .fillEqually
        recordingOptions.axis = .vertical
        recordingOptions.spacing = 16
        let stack = UIStackView(arrangedSubviews: [icon, heading, options, recordingOptions, status, progressBar, saveButton, cancelButton, openAppButton])
        stack.axis = .vertical
        stack.spacing = 16
        stack.alignment = .fill
        stack.translatesAutoresizingMaskIntoConstraints = false
        let card = UIView()
        card.backgroundColor = UIColor.secondarySystemGroupedBackground
        card.layer.cornerRadius = 24
        card.layer.cornerCurve = .continuous
        card.addSubview(stack)
        stack.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 24).isActive = true
        stack.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -24).isActive = true
        stack.topAnchor.constraint(equalTo: card.topAnchor, constant: 28).isActive = true
        stack.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -28).isActive = true
        card.translatesAutoresizingMaskIntoConstraints = false
        let scroll = UIScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(scroll)
        scroll.addSubview(card)
        NSLayoutConstraint.activate([
            scroll.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
            scroll.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            scroll.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor),
            card.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor, constant: 20),
            card.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor, constant: -20),
            card.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor, constant: 20),
            card.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor, constant: -20),
            card.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor, constant: -40),
        ])
        loadOptions()
        stageAudio()
    }

    @objc private func saveAudio() {
        guard !busy, !staging, !stagedURLs.isEmpty,
              stagedURLs.allSatisfy({ timeEditors[$0]?.isConfirmed == true }) else { return }
        busy = true
        didCancelUpload = false
        saveButton.isEnabled = false
        setOptionsEnabled(false)
        cancelButton.isHidden = false
        openAppButton.isHidden = true
        status.text = "Preparing audio…"
        uploadOperation = Task { await upload(stagedURLs) }
    }

    private func stageAudio() {
        guard !staging, !audioProviders.isEmpty else { return }
        staging = true
        saveButton.isEnabled = false
        status.text = AppLocalization.text("Reading recording dates…")
        guard let root = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: "group.com.daythread.app") else {
            fail(NSError(domain: "DaythreadShare", code: 1))
            return
        }
        let directory = root.appendingPathComponent("SharedAudio", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        } catch {
            fail(error)
            return
        }

        let group = DispatchGroup()
        let lock = NSLock()
        var copied: [URL] = []
        for provider in audioProviders {
            guard let type = provider.registeredTypeIdentifiers.first(where: { UTType($0)?.conforms(to: .audio) == true }) else { continue }
            group.enter()
            provider.loadFileRepresentation(forTypeIdentifier: type) { source, _ in
                defer { group.leave() }
                guard let source else { return }
                let name = URL(fileURLWithPath: provider.suggestedName ?? source.lastPathComponent).lastPathComponent
                let destination = directory.appendingPathComponent(UUID().uuidString + "__" + name)
                do {
                    try FileManager.default.copyItem(at: source, to: destination)
                    lock.lock(); copied.append(destination); lock.unlock()
                } catch { }
            }
        }
        group.notify(queue: .main) { [weak self] in
            guard let self else { return }
            lock.lock(); let urls = copied; lock.unlock()
            if self.didCancelUpload {
                urls.forEach { try? FileManager.default.removeItem(at: $0) }
                self.busy = false
                self.cancelButton.isHidden = true
                self.saveButton.isEnabled = true
                self.status.text = "Import canceled. You can retry when ready."
                return
            }
            guard !urls.isEmpty else {
                self.fail(NSError(domain: "DaythreadShare", code: 2))
                return
            }
            self.stagedURLs = urls.sorted { $0.lastPathComponent < $1.lastPathComponent }
            Task {
                for url in self.stagedURLs {
                    let modified = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
                    let suggestion = await RecordingImport.dateSuggestion(in: url, modifiedAt: modified)
                    let editor = ShareRecordingTimeView(name: Self.originalName(url.lastPathComponent), suggestion: suggestion)
                    editor.onChanged = { [weak self] in self?.updateImportAvailability() }
                    self.timeEditors[url] = editor
                    self.recordingOptions.addArrangedSubview(editor)
                }
                self.staging = false
                self.status.text = AppLocalization.text("Review each recording’s date and time before importing.")
                self.updateImportAvailability()
            }
        }
    }

    @objc private func cancelUpload() {
        guard busy else { return }
        didCancelUpload = true
        cancelButton.isHidden = true
        activeUploadTask?.cancel()
        uploadOperation?.cancel()
        status.text = "Canceling import…"
    }

    private func upload(_ urls: [URL]) async {
        defer {
            busy = false
            setOptionsEnabled(true)
            uploadOperation = nil
            activeUploadTask = nil
        }
        do {
            guard let endpoints, !profileID.isEmpty else { throw ShareImportError("Open Daythread and select an archive before importing audio.") }
            if UserDefaults(suiteName: "group.com.daythread.app")?.bool(forKey: "managedArchiveSelected") == true {
                throw ShareImportError("Open Daythread to import audio to your signed-in archive.")
            }
            for (index, url) in urls.enumerated() {
                setStatus("Importing recording \(index + 1) of \(urls.count)…")
                try await upload(url, configuration: endpoints)
                try? FileManager.default.removeItem(at: url)
                stagedURLs.removeAll { $0 == url }
                timeEditors[url]?.removeFromSuperview()
                timeEditors.removeValue(forKey: url)
                plans.removeValue(forKey: url)
            }
            setStatus("Import complete. Your recording is ready in Daythread.")
            await MainActor.run {
                cancelButton.isHidden = true
                saveButton.setTitle("Close", for: .normal)
                saveButton.isEnabled = true
                saveButton.removeTarget(self, action: #selector(saveAudio), for: .touchUpInside)
                saveButton.addTarget(self, action: #selector(finish), for: .touchUpInside)
                openAppButton.isHidden = false
            }
        } catch {
            await MainActor.run {
                cancelButton.isHidden = true
                saveButton.setTitle("Retry import", for: .normal)
                saveButton.isEnabled = true
                status.text = didCancelUpload ? "Import canceled. You can retry when ready." : error.localizedDescription
            }
        }
    }

    private func upload(_ source: URL, configuration: BackendEndpointConfiguration) async throws {
        let attributes = try FileManager.default.attributesOfItem(atPath: source.path)
        let byteCount = (attributes[.size] as? NSNumber)?.intValue ?? 0
        try RecordingImport.validateFile(name: Self.originalName(source.lastPathComponent), bytes: byteCount)
        let digest = try RecordingAudioConverter.fileDigest(source)
        let duplicateData = try await send("v1/recording-uploads/check", method: "POST", configuration: configuration,
                                           body: try JSONSerialization.data(withJSONObject: ["digest": digest]))
        if plans[source] == nil,
           (try? JSONSerialization.jsonObject(with: duplicateData) as? [String: Any])?["duplicate"] as? Bool == true {
            return
        }
        let directory = source.deletingLastPathComponent().appendingPathComponent("Converted-" + UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let converted = try await RecordingAudioConverter.convert(source, into: directory)
        guard let editor = timeEditors[source], editor.isConfirmed else {
            throw ShareImportError("Review the recording date and time before importing.")
        }
        let bounds = try RecordingBounds(recordedAt: editor.recordedAt, sampleCount: converted.sampleCount, reference: editor.reference)
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let body: [String: Any] = [
            "file_digest": digest, "digests": converted.parts.map(\.digest), "sample_count": converted.sampleCount,
            "source_id": Self.originalName(source.lastPathComponent), "started_at": formatter.string(from: bounds.startedAt),
            "ended_at": formatter.string(from: bounds.endedAt), "conversation_source_id": NSNull(),
        ]
        let plan: RecordingUploadPlan
        if let saved = plans[source] {
            plan = saved
        } else {
            let data = try await send("v1/recording-uploads/prepare", method: "POST", configuration: configuration,
                                      body: try JSONSerialization.data(withJSONObject: body))
            let response = try JSONDecoder().decode(RecordingPreparation.self, from: data)
            guard case .upload(let prepared) = response else { return }
            try prepared.validate(digests: converted.parts.map(\.digest), sampleCount: converted.sampleCount)
            plan = prepared
            plans[source] = plan
            editor.locked = true
        }
        try plan.validate(digests: converted.parts.map(\.digest), sampleCount: converted.sampleCount)
        let totalBytes = converted.parts.reduce(Int64(0)) { $0 + Int64($1.bytes) }
        var completedBytes = zip(plan.parts, converted.parts).reduce(Int64(0)) { total, pair in
            total + (pair.0.uploaded ? Int64(pair.1.bytes) : 0)
        }
        setUploadProgress(sent: completedBytes, total: totalBytes)
        for index in plan.parts.indices where !plan.parts[index].uploaded {
            try Task.checkCancellation()
            let partBytes = Int64(converted.parts[index].bytes)
            let partBaseline = completedBytes
            let query = try plan.query(for: index)
            let receipt = try await uploadFile(converted.parts[index].url, id: plan.parts[index].id,
                                               query: query, configuration: configuration) { sent in
                self.setUploadProgress(sent: partBaseline + min(sent, partBytes), total: totalBytes)
            }
            try plan.validateReceipt(receipt, part: index, sampleCount: converted.parts[index].samples)
            completedBytes += partBytes
        }
        try Task.checkCancellation()
        try await applyOptionalDetails(plan, configuration: configuration)
    }

    private func applyOptionalDetails(_ plan: RecordingUploadPlan, configuration: BackendEndpointConfiguration) async throws {
        guard !selectedTagIDs.isEmpty || !pendingTagNames.isEmpty || selectedPlace != nil || currentLocation != nil else { return }
        for part in plan.parts {
            let sourceID = part.id.uuidString.lowercased()
            if !selectedTagIDs.isEmpty || !pendingTagNames.isEmpty {
                let path = "v1/sources/\(sourceID)/tags"
                var selection = try JSONSerialization.jsonObject(with: await send(path, method: "GET", configuration: configuration)) as? [String: Any] ?? [:]
                let existing = (selection["tags"] as? [[String: Any]] ?? []).compactMap { $0["id"] as? String }
                let changes = selectedTagIDs.subtracting(existing).sorted().map { ["tag_id": $0] }
                    + pendingTagNames.sorted().map { ["name": $0] }
                for change in changes {
                    let body = [
                        "expected_revision": selection["revision"] as? String ?? "",
                        "expected_membership": selection["membership"] as? String ?? "",
                        "action": "add",
                    ].merging(change) { _, value in value }
                    let data = try await send(path, method: "PUT", configuration: configuration,
                                              body: try JSONSerialization.data(withJSONObject: body))
                    selection = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
                }
            }
            var place: [String: Any]?
            if let selectedPlace { place = ["zone_id": selectedPlace.zoneID, "location_id": selectedPlace.locationID] }
            else if let currentLocation {
                place = ["kind": "current", "latitude": currentLocation.latitude, "longitude": currentLocation.longitude,
                         "horizontal_accuracy": currentLocation.horizontalAccuracy]
            }
            if let place {
                let path = "v1/sources/\(sourceID)/location"
                let data = try await send(path, method: "GET", configuration: configuration)
                let version = (try JSONSerialization.jsonObject(with: data) as? [String: Any])?["version"] as? Int ?? 0
                let body: [String: Any] = ["expected_version": version, "location": place]
                _ = try await send(path, method: "PUT", configuration: configuration,
                                   body: try JSONSerialization.data(withJSONObject: body))
            }
        }
    }

    private static func originalName(_ stagedName: String) -> String {
        guard let separator = stagedName.range(of: "__") else { return stagedName }
        return String(stagedName[separator.upperBound...])
    }

    @MainActor private func setStatus(_ text: String) { status.text = text }

    private func setUploadProgress(sent: Int64, total: Int64) {
        let fraction = total > 0 ? min(1, max(0, Double(sent) / Double(total))) : 1
        let percent = Int((fraction * 100).rounded())
        DispatchQueue.main.async {
            self.progressBar.isHidden = false
            self.progressBar.setProgress(Float(fraction), animated: true)
            self.status.text = "Uploading · \(percent)%"
        }
    }

    @objc private func finish() { extensionContext?.completeRequest(returningItems: nil) }

    @objc private func openDaythread() {
        guard let url = URL(string: "daythread://shared-audio") else { return }
        extensionContext?.open(url) { [weak self] opened in
            guard let self else { return }
            if opened {
                self.finish()
                return
            }
            DispatchQueue.main.async {
                self.status.text = "iOS couldn’t open Daythread from this share sheet. You can open it from your Home Screen."
            }
        }
    }

    private func fail(_ error: Error) {
        status.text = "Audio couldn’t be saved. Try sharing it again."
        busy = false
        staging = false
        setOptionsEnabled(true)
        cancelButton.isHidden = true
        updateImportAvailability()
    }
}

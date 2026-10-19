import UIKit
import UniformTypeIdentifiers
import CoreLocation
import OmiCore

final class ShareViewController: UIViewController {
    private var audioProviders: [NSItemProvider] = []
    private let status = UILabel()
    private let progressBar = UIProgressView(progressViewStyle: .default)
    private let saveButton = UIButton(type: .system)
    private let cancelButton = UIButton(type: .system)
    private let openAppButton = UIButton(type: .system)
    private let tagsButton = UIButton(type: .system)
    private let locationButton = UIButton(type: .system)
    private var stagedURLs: [URL] = []
    private var busy = false
    private var uploadOperation: Task<Void, Never>?
    private var activeUploadTask: URLSessionUploadTask?
    private var didCancelUpload = false
    private var endpoints: BackendEndpointConfiguration?
    private var profileID = ""
    private var availableTags: [ShareTag] = []
    private var locationZones: [ShareZone] = []
    private var selectedTagIDs = Set<String>()
    private var selectedPlace: (zoneID: String, locationID: String)?
    private var currentLocation: CaptureLocation?
    private let locationReader = ShareLocationReader()

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
        let stack = UIStackView(arrangedSubviews: [icon, heading, options, status, progressBar, saveButton, cancelButton, openAppButton])
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
        view.addSubview(card)
        NSLayoutConstraint.activate([
            card.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 20),
            card.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -20),
            card.centerYAnchor.constraint(equalTo: view.safeAreaLayoutGuide.centerYAnchor),
        ])
        loadOptions()
    }

    private func configureOptionButton(_ button: UIButton, title: String) {
        var style = UIButton.Configuration.gray()
        style.cornerStyle = .large
        style.contentInsets = NSDirectionalEdgeInsets(top: 11, leading: 12, bottom: 11, trailing: 12)
        button.configuration = style
        button.setTitle(title, for: .normal)
    }

    private func refreshOptionMenus() {
        let tagTitle = selectedTagIDs.isEmpty ? "Tags (optional)" : "Tags · \(selectedTagIDs.count)"
        tagsButton.setTitle(tagTitle, for: .normal)
        let tagActions = availableTags.map { tag in
            UIAction(title: tag.name, state: selectedTagIDs.contains(tag.id) ? .on : .off) { [weak self] _ in
                guard let self else { return }
                if self.selectedTagIDs.contains(tag.id) { self.selectedTagIDs.remove(tag.id) }
                else { self.selectedTagIDs.insert(tag.id) }
                self.refreshOptionMenus()
            }
        }
        tagsButton.menu = UIMenu(title: availableTags.isEmpty ? "No tags in this archive" : "Add tags", options: .displayInline, children: tagActions)

        if let currentLocation { locationButton.setTitle(String(format: "Location · %.3f, %.3f", currentLocation.latitude, currentLocation.longitude), for: .normal) }
        else if let selectedPlace,
                let zone = locationZones.first(where: { $0.id == selectedPlace.zoneID }),
                let place = zone.locations.first(where: { $0.id == selectedPlace.locationID }) {
            locationButton.setTitle("\(zone.name) · \(place.label)", for: .normal)
        } else { locationButton.setTitle("Location (optional)", for: .normal) }
        var locationActions = [UIAction(title: "No location", state: selectedPlace == nil && currentLocation == nil ? .on : .off) { [weak self] _ in
            self?.selectedPlace = nil; self?.currentLocation = nil; self?.refreshOptionMenus()
        }]
        locationActions.append(UIAction(title: "Use current location", image: UIImage(systemName: "location")) { [weak self] _ in
            guard let self else { return }
            Task {
                do {
                    let fix = try await self.locationReader.currentLocation()
                    self.currentLocation = CaptureLocation(latitude: fix.coordinate.latitude, longitude: fix.coordinate.longitude,
                                                           horizontalAccuracy: max(0, fix.horizontalAccuracy), capturedAt: fix.timestamp)
                    self.selectedPlace = nil
                    self.refreshOptionMenus()
                } catch { self.status.text = error.localizedDescription }
            }
        })
        for zone in locationZones {
            for place in zone.locations {
                let selected = selectedPlace?.zoneID == zone.id && selectedPlace?.locationID == place.id
                locationActions.append(UIAction(title: "\(zone.name) · \(place.label)", state: selected ? .on : .off) { [weak self] _ in
                    self?.selectedPlace = (zone.id, place.id); self?.currentLocation = nil; self?.refreshOptionMenus()
                })
            }
        }
        locationButton.menu = UIMenu(title: "Add a location", children: locationActions)
    }

    private func loadOptions() {
        guard let endpoints else { return }
        Task {
            do {
                let tagData = try await send("v1/tags", method: "GET", configuration: endpoints)
                availableTags = try JSONDecoder().decode(ShareTagDirectory.self, from: tagData).items
            } catch { }
            do {
                let placeData = try await send("v1/location-zones", method: "GET", configuration: endpoints)
                locationZones = try JSONDecoder().decode(ShareZoneDirectory.self, from: placeData).items
            } catch { }
            refreshOptionMenus()
        }
    }

    @objc private func saveAudio() {
        guard !busy else { return }
        busy = true
        didCancelUpload = false
        saveButton.isEnabled = false
        cancelButton.isHidden = false
        openAppButton.isHidden = true
        status.text = "Preparing audio…"
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

        if !stagedURLs.isEmpty {
            uploadOperation = Task { await upload(stagedURLs) }
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
                let name = provider.suggestedName ?? source.lastPathComponent
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
            self.stagedURLs = urls
            self.uploadOperation = Task { await self.upload(urls) }
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
        try RecordingImport.validateFile(name: source.lastPathComponent, bytes: byteCount)
        let digest = try RecordingAudioConverter.fileDigest(source)
        let duplicateData = try await send("v1/recording-uploads/check", method: "POST", configuration: configuration,
                                           body: try JSONSerialization.data(withJSONObject: ["digest": digest]))
        if (try? JSONSerialization.jsonObject(with: duplicateData) as? [String: Any])?["duplicate"] as? Bool == true {
            return
        }
        let directory = source.deletingLastPathComponent().appendingPathComponent("Converted-" + UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let converted = try await RecordingAudioConverter.convert(source, into: directory)
        let embeddedDate = await RecordingImport.embeddedRecordingDate(in: source)
        let fileDate = (attributes[.modificationDate] as? Date).flatMap { RecordingImport.suggestedEnd(modifiedAt: $0) }
        let recordedAt = embeddedDate ?? fileDate ?? Date()
        let bounds = try RecordingBounds(recordedAt: recordedAt, sampleCount: converted.sampleCount, reference: .end)
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let body: [String: Any] = [
            "file_digest": digest, "digests": converted.parts.map(\.digest), "sample_count": converted.sampleCount,
            "source_id": Self.originalName(source.lastPathComponent), "started_at": formatter.string(from: bounds.startedAt),
            "ended_at": formatter.string(from: bounds.endedAt), "conversation_source_id": NSNull(),
        ]
        let data = try await send("v1/recording-uploads/prepare", method: "POST", configuration: configuration,
                                  body: try JSONSerialization.data(withJSONObject: body))
        let response = try JSONDecoder().decode(RecordingPreparation.self, from: data)
        guard case .upload(let plan) = response else { return }
        try plan.validate(digests: converted.parts.map(\.digest), sampleCount: converted.sampleCount)
        let totalBytes = converted.parts.reduce(Int64(0)) { $0 + Int64($1.bytes) }
        var completedBytes = zip(plan.parts, converted.parts).reduce(Int64(0)) { total, pair in
            total + (pair.0.uploaded ? Int64(pair.1.bytes) : 0)
        }
        setUploadProgress(sent: completedBytes, total: totalBytes)
        for index in plan.parts.indices where !plan.parts[index].uploaded {
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
        try await applyOptionalDetails(plan, configuration: configuration)
    }

    private func applyOptionalDetails(_ plan: RecordingUploadPlan, configuration: BackendEndpointConfiguration) async throws {
        guard !selectedTagIDs.isEmpty || selectedPlace != nil || currentLocation != nil else { return }
        for part in plan.parts {
            let sourceID = part.id.uuidString.lowercased()
            if !selectedTagIDs.isEmpty {
                let path = "v1/sources/\(sourceID)/tags"
                var selection = try JSONSerialization.jsonObject(with: await send(path, method: "GET", configuration: configuration)) as? [String: Any] ?? [:]
                let existing = (selection["tags"] as? [[String: Any]] ?? []).compactMap { $0["id"] as? String }
                for tagID in selectedTagIDs.subtracting(existing).sorted() {
                    let body = [
                        "expected_revision": selection["revision"] as? String ?? "",
                        "expected_membership": selection["membership"] as? String ?? "",
                        "action": "add", "tag_id": tagID,
                    ]
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

    private func send(_ path: String, method: String, configuration: BackendEndpointConfiguration, body: Data? = nil) async throws -> Data {
        guard let endpoint = configuration.activeEndpoint, let address = URL(string: endpoint.address) else {
            throw ShareImportError("Daythread’s archive address is unavailable. Open the app and try again.")
        }
        var request = URLRequest(url: address.appendingPathComponent(path))
        request.httpMethod = method; request.httpBody = body; request.timeoutInterval = 180
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(profileID, forHTTPHeaderField: "X-Omi-Profile")
        request.setValue(ManagedServiceOrigin.ownDestination(archiveIdentity: configuration.archiveIdentity), forHTTPHeaderField: "X-Daythread-Destination")
        return try await checkedData(for: request)
    }

    private func uploadFile(_ file: URL, id: UUID, query: [String: String], configuration: BackendEndpointConfiguration,
                            onProgress: @escaping (Int64) -> Void) async throws -> Data {
        guard let endpoint = configuration.activeEndpoint, let base = URL(string: endpoint.address) else { throw ShareImportError("Daythread’s archive address is unavailable.") }
        var components = URLComponents(url: base.appendingPathComponent("v1/audio/\(id.uuidString.lowercased())"), resolvingAgainstBaseURL: false)!
        components.queryItems = query.keys.sorted().map { URLQueryItem(name: $0, value: query[$0]) }
        components.percentEncodedQuery = components.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        var request = URLRequest(url: components.url!); request.httpMethod = "PUT"; request.timeoutInterval = 180
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        request.setValue(profileID, forHTTPHeaderField: "X-Omi-Profile")
        request.setValue(ManagedServiceOrigin.ownDestination(archiveIdentity: configuration.archiveIdentity), forHTTPHeaderField: "X-Daythread-Destination")
        return try await withCheckedThrowingContinuation { continuation in
            let delegate = ShareUploadProgressDelegate(onProgress: onProgress)
            let session = URLSession(configuration: .ephemeral, delegate: delegate, delegateQueue: .main)
            let task = session.uploadTask(with: request, fromFile: file) { data, response, error in
                session.finishTasksAndInvalidate()
                self.activeUploadTask = nil
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                let responseData = data ?? Data()
                guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                    continuation.resume(throwing: ShareImportError(Self.message(from: responseData)))
                    return
                }
                continuation.resume(returning: responseData)
            }
            activeUploadTask = task
            task.resume()
        }
    }

    private func checkedData(for request: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw ShareImportError(Self.message(from: data)) }
        return data
    }

    private static func message(from data: Data) -> String {
        (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String ?? "Daythread couldn’t import this recording. Open the app to retry."
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
        cancelButton.isHidden = true
        saveButton.isEnabled = true
    }
}

private final class ShareUploadProgressDelegate: NSObject, URLSessionTaskDelegate {
    private let onProgress: (Int64) -> Void

    init(onProgress: @escaping (Int64) -> Void) {
        self.onProgress = onProgress
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        onProgress(totalBytesSent)
    }
}

private struct ShareImportError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

private struct ShareTag: Decodable, Identifiable {
    let id: String
    let name: String
}
private struct ShareTagDirectory: Decodable { let items: [ShareTag] }
private struct SharePlace: Decodable, Identifiable {
    let id: String
    let label: String
}
private struct ShareZone: Decodable, Identifiable {
    let id: String
    let name: String
    let locations: [SharePlace]
}
private struct ShareZoneDirectory: Decodable { let items: [ShareZone] }

private final class ShareLocationReader: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private var continuation: CheckedContinuation<CLLocation, Error>?

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    func currentLocation() async throws -> CLLocation {
        guard continuation == nil else { throw ShareImportError("A location request is already running.") }
        return try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            switch manager.authorizationStatus {
            case .authorizedAlways, .authorizedWhenInUse: manager.requestLocation()
            case .notDetermined: manager.requestWhenInUseAuthorization()
            default: finish(.failure(ShareImportError("Allow Daythread to use your location, or choose a saved place.")))
            }
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        guard continuation != nil else { return }
        if [.authorizedAlways, .authorizedWhenInUse].contains(manager.authorizationStatus) { manager.requestLocation() }
        else if manager.authorizationStatus != .notDetermined {
            finish(.failure(ShareImportError("Allow Daythread to use your location, or choose a saved place.")))
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last,
              location.horizontalAccuracy >= 0, location.horizontalAccuracy <= 1000,
              abs(location.timestamp.timeIntervalSinceNow) < 120 else {
            finish(.failure(ShareImportError("Current location is unavailable. Choose a saved place instead.")))
            return
        }
        finish(.success(location))
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) { finish(.failure(error)) }

    private func finish(_ result: Result<CLLocation, Error>) {
        let pending = continuation
        continuation = nil
        pending?.resume(with: result)
    }
}

import XCTest
import UIKit
import OmiCore
import Network

@MainActor
final class ShareImportOptionsTests: XCTestCase {
    func testRecordingStartedIsFirstButMissingDateDefaultsToRecordingEnded() throws {
        XCTAssertEqual(RecordingTimeReference.allCases, [.start, .end])
        let editor = ShareRecordingTimeView(name: "Recording.wav", suggestion: nil)
        let reference = try XCTUnwrap(editor.arrangedSubviews.compactMap { $0 as? UISegmentedControl }.first)
        XCTAssertEqual(reference.titleForSegment(at: 0), AppLocalization.text("Recording started"))
        XCTAssertEqual(reference.titleForSegment(at: 1), AppLocalization.text("Recording ended"))
        XCTAssertEqual(reference.selectedSegmentIndex, 1)
        XCTAssertEqual(editor.reference, .end)
        reference.selectedSegmentIndex = 0
        reference.sendActions(for: .valueChanged)
        XCTAssertEqual(editor.reference, .start)
    }

    func testImportHeadingWrapsInANarrowShareSheet() throws {
        let controller = ShareViewController()
        controller.loadViewIfNeeded()
        controller.recordingOptions.addArrangedSubview(ShareRecordingTimeView(
            name: "10-29_Weekly_Meeting_Basecamp_Plotting_Enhancements_Calibration_Curves_and_Empower_Integration.wav", suggestion: nil))
        controller.status.text = "Review each recording’s date and time before importing."
        controller.view.frame = CGRect(x: 0, y: 0, width: 320, height: 700)
        controller.view.layoutIfNeeded()
        func labels(in view: UIView) -> [UILabel] {
            (view as? UILabel).map { [$0] } ?? view.subviews.flatMap { labels(in: $0) }
        }
        let heading = try XCTUnwrap(labels(in: controller.view).first { $0.text == "Import audio to Daythread" })
        XCTAssertEqual(heading.numberOfLines, 0)
        XCTAssertTrue(heading.adjustsFontForContentSizeCategory)
        let fitted = heading.sizeThatFits(CGSize(width: heading.bounds.width, height: .greatestFiniteMagnitude))
        XCTAssertGreaterThan(fitted.height, heading.font.lineHeight)
        XCTAssertGreaterThanOrEqual(heading.bounds.height, fitted.height - 1)
        let image = UIGraphicsImageRenderer(bounds: controller.view.bounds).image { context in
            controller.view.layer.render(in: context.cgContext)
        }
        let attachment = XCTAttachment(image: image)
        attachment.name = "share-import-320"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testMissingDatePreventsImportUntilTheDisplayedTimeIsConfirmed() async throws {
        let controller = ShareViewController()
        controller.endpoints = BackendEndpointConfiguration(endpoints: [.init(address: "https://example.invalid")])
        controller.profileID = "test-profile"
        let url = URL(fileURLWithPath: "/tmp/recording.wav")
        let editor = ShareRecordingTimeView(name: "Recording", suggestion: nil)
        controller.stagedURLs = [url]
        controller.timeEditors = [url: editor]
        editor.onChanged = { controller.updateImportAvailability() }
        controller.updateImportAvailability()
        XCTAssertFalse(controller.saveButton.isEnabled)

        let confirm = try XCTUnwrap(editor.arrangedSubviews.compactMap { $0 as? UIButton }.first)
        confirm.sendActions(for: .touchUpInside)
        XCTAssertTrue(controller.saveButton.isEnabled)
        controller.busy = true
        controller.updateImportAvailability()
        XCTAssertFalse(controller.saveButton.isEnabled)
    }

    func testEmbeddedRecordingStartIsEditableAndRetryLocksIt() async throws {
        let start = Date(timeIntervalSince1970: 1_790_000_000)
        let suggestion = RecordingDateSuggestion(date: start, reference: .start,
                                                 source: "Embedded recording date", needsConfirmation: false)
        let editor = ShareRecordingTimeView(name: "Plaud.mp3", suggestion: suggestion)
        XCTAssertEqual(editor.recordedAt, start)
        XCTAssertEqual(editor.reference, .start)
        XCTAssertTrue(editor.isConfirmed)
        let picker = try XCTUnwrap(editor.arrangedSubviews.compactMap { $0 as? UIDatePicker }.first)
        let reference = try XCTUnwrap(editor.arrangedSubviews.compactMap { $0 as? UISegmentedControl }.first)
        picker.date = start.addingTimeInterval(-3600)
        picker.sendActions(for: .valueChanged)
        reference.selectedSegmentIndex = 1
        reference.sendActions(for: .valueChanged)
        XCTAssertEqual(editor.recordedAt, start.addingTimeInterval(-3600))
        XCTAssertEqual(editor.reference, .end)
        editor.locked = true
        editor.setEnabled(true)
        XCTAssertFalse(picker.isEnabled, "Retry must keep the accepted capture time frozen")
        XCTAssertFalse(reference.isEnabled)
    }

    func testExportAndFilesystemDateSuggestionsRequireReview() async {
        for source in ["Embedded creation date", "File modification date"] {
            let suggestion = RecordingDateSuggestion(date: Date().addingTimeInterval(-3600), reference: .end,
                                                     source: source, needsConfirmation: true)
            let editor = ShareRecordingTimeView(name: "Plaud.wav", suggestion: suggestion)
            XCTAssertFalse(editor.isConfirmed)
            XCTAssertTrue(editor.arrangedSubviews.compactMap { $0 as? UILabel }.contains { $0.text?.contains(source) == true })
        }
    }

    func testTagsMenuAllowsCreatingATagWhenArchiveHasNoTags() async throws {
        let controller = ShareViewController()
        controller.refreshOptionMenus()
        let actions = try XCTUnwrap(controller.tagsButton.menu).children.compactMap { $0 as? UIAction }
        XCTAssertTrue(actions.contains { $0.title == "Create tag…" })
        controller.pendingTagNames = ["Work"]
        controller.refreshOptionMenus()
        let selected = controller.tagsButton.menu?.children.compactMap { $0 as? UIAction }.first { $0.title == "Work" }
        XCTAssertEqual(selected?.state, .on)
    }

    func testBackgroundUploadReturnsWithDurableAudioBeforeServerAcknowledgesThenCleansUp() async throws {
        let workspace = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: workspace, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: workspace) }
        let source = workspace.appendingPathComponent("shared-recording.wav")
        try WAV.encode(Data(repeating: 0, count: 3200)).write(to: source)
        let converted = try await RecordingAudioConverter.convert(source, into: workspace.appendingPathComponent("parts"))
        let id = UUID()
        let start = Date().addingTimeInterval(-600)
        let startedAt = DurableStore.stamp(start)
        let endedAt = DurableStore.stamp(start.addingTimeInterval(Double(converted.sampleCount) / Double(RecordingImport.sampleRate)))
        let receipt: [String: Any] = ["id": id.uuidString, "device_id": RecordingImport.deviceID,
                                    "started_at": startedAt, "ended_at": endedAt, "state": "queued"]
        let received = expectation(description: "Background service sent the file")
        let ready = expectation(description: "Local test archive is listening")
        let server = try GatedShareUploadServer(body: JSONSerialization.data(withJSONObject: receipt), ready: ready, received: received)
        defer { server.stop() }
        await fulfillment(of: [ready], timeout: 10)
        let port = try XCTUnwrap(server.port)
        let archive = "background-integration-" + UUID().uuidString
        let profile = UUID().uuidString
        let configuration = BackendEndpointConfiguration(endpoints: [.init(address: "http://127.0.0.1:\(port)")], archiveIdentity: archive)
        let planBody: [String: Any] = ["recording_id": UUID().uuidString, "source_id": source.lastPathComponent,
            "started_at": startedAt, "ended_at": endedAt,
            "parts": [["id": id.uuidString, "digest": converted.parts[0].digest, "uploaded": false]]]
        let plan = try JSONDecoder().decode(RecordingUploadPlan.self, from: JSONSerialization.data(withJSONObject: planBody))
        let jobID = try BackgroundRecordingImport.enqueue(converted: converted, plan: plan, sourceName: source.lastPathComponent,
            configuration: configuration, profileID: profile)
        let group = try XCTUnwrap(FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: BackgroundRecordingImport.groupID))
        let directory = group.appendingPathComponent("BackgroundRecordingImports/\(jobID.uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        XCTAssertTrue(FileManager.default.fileExists(atPath: directory.appendingPathComponent("job.json").path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: directory.appendingPathComponent("part-0.wav").path))
        let pending = try XCTUnwrap(BackgroundRecordingImport.pendingJobs(archiveIdentity: archive, profileID: profile).first)
        XCTAssertEqual(pending.id, jobID)
        XCTAssertEqual(pending.uploadedParts, 0, "Enqueue returns before any acknowledgement")
        // The gate is held until the real iOS background service has sent the body.
        await fulfillment(of: [received], timeout: 30)
        XCTAssertTrue(FileManager.default.fileExists(atPath: directory.path), "Audio must survive while the archive has not acknowledged it")
        let request = server.request
        XCTAssertTrue(request.headers.contains("PUT /v1/audio/\(id.uuidString.lowercased())?"))
        XCTAssertTrue(request.headers.lowercased().contains("x-omi-profile: \(profile.lowercased())"))
        XCTAssertEqual(String(data: request.body.prefix(4), encoding: .ascii), "RIFF")
        let completed = expectation(description: "Matching receipt releases durable audio")
        var observed = false
        let token = NotificationCenter.default.addObserver(forName: .backgroundRecordingImportChanged, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated {
                if !observed && !FileManager.default.fileExists(atPath: directory.path) {
                    observed = true
                    completed.fulfill()
                }
            }
        }
        defer { NotificationCenter.default.removeObserver(token) }
        server.releaseResponse()
        await fulfillment(of: [completed], timeout: 30)
        XCTAssertTrue(BackgroundRecordingImport.pendingJobs(archiveIdentity: archive, profileID: profile).isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
    }
}

/// A private test archive that accepts the entire body but withholds its receipt
/// until the test has verified the background handoff and retained local audio.
private final class GatedShareUploadServer: @unchecked Sendable {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "share-upload-test-server")
    private let response: Data
    private let received: XCTestExpectation
    private var connection: NWConnection?
    private var buffer = Data()
    private var headers = ""
    private var body = Data()
    private var released = false
    private var complete = false
    private var responded = false

    init(body: Data, ready: XCTestExpectation, received: XCTestExpectation) throws {
        listener = try NWListener(using: .tcp, on: .any)
        response = Data("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n".utf8) + body
        self.received = received
        listener.stateUpdateHandler = { state in if case .ready = state { ready.fulfill() } }
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { return }
            self.connection = connection
            connection.start(queue: self.queue)
            self.receive(connection)
        }
        listener.start(queue: queue)
    }

    var port: UInt16? { listener.port?.rawValue }
    var request: (headers: String, body: Data) { queue.sync { (headers, body) } }
    func releaseResponse() { queue.async { self.released = true; self.respondIfReady() } }
    func stop() { queue.async { self.connection?.cancel(); self.listener.cancel() } }

    private func receive(_ connection: NWConnection) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, ended, error in
            guard let self else { return }
            if let data { self.buffer.append(data) }
            if let range = self.buffer.range(of: Data("\r\n\r\n".utf8)) {
                let headers = String(decoding: self.buffer[..<range.lowerBound], as: UTF8.self)
                let length = headers.components(separatedBy: "\r\n")
                    .first { $0.lowercased().hasPrefix("content-length:") }
                    .flatMap { Int($0.dropFirst("content-length:".count).trimmingCharacters(in: .whitespaces)) } ?? 0
                let body = self.buffer[range.upperBound...]
                if body.count >= length {
                    self.headers = headers
                    self.body = Data(body.prefix(length))
                    self.complete = true
                    self.received.fulfill()
                    self.respondIfReady()
                    return
                }
            }
            if error == nil && !ended { self.receive(connection) }
        }
    }

    private func respondIfReady() {
        guard complete, released, !responded, let connection else { return }
        responded = true
        connection.send(content: response, completion: .contentProcessed { _ in connection.cancel() })
    }
}

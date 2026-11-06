import XCTest
@testable import OmiCore

final class BackgroundRecordingImportTests: XCTestCase {
    func testCompetingOwnersCannotMutateMetadataUntilTheLeaseIsReleased() throws {
        try withWorkspace { root in
            let fixture = try fixture(in: root)
            var job = try stage(fixture, root: root)
            let directory = job.directory(in: root)
            var extensionOwner = try BackgroundImportLease.claim(in: directory)
            XCTAssertNotNil(extensionOwner)
            XCTAssertNil(try BackgroundImportLease.claim(in: directory), "Foreground retry must skip a live metadata writer without blocking")
            job.state = .details
            job.acknowledged = [0]
            job.detailsSaved = [0]
            try job.save(in: directory)
            extensionOwner = nil
            let appOwner = try BackgroundImportLease.claim(in: directory)
            XCTAssertNotNil(appOwner, "Closing the original descriptor permits recovery after extension termination")
            let latest = try BackgroundImportJob.load(from: directory)
            XCTAssertEqual(latest.state, .details)
            XCTAssertEqual(latest.detailsSaved, [0], "The new owner must reload the latest durable state after claiming it")
            withExtendedLifetime(appOwner) { XCTAssertNil(try? BackgroundImportLease.claim(in: directory)) }
        }
    }

    func testHandoffMovesAudioAndPersistsDestinationPlanAndOptionalDetailsBeforeNetworking() throws {
        try withWorkspace { root in
            let fixture = try fixture(in: root, treatAsMeeting: true)
            let location = Data(#"{"zone_id":"saved-zone","location_id":"saved-place"}"#.utf8)
            let job = try stage(fixture, root: root, location: location)
            let directory = job.directory(in: root)
            let restored = try BackgroundImportJob.load(from: directory)
            XCTAssertEqual(restored.state, .prepared)
            XCTAssertEqual(restored.plan.recordingID, fixture.plan.recordingID)
            XCTAssertEqual(restored.plan.startedAt, "2026-10-29T10:00:00Z")
            XCTAssertTrue(restored.plan.resolvedTreatAsMeeting)
            XCTAssertEqual(restored.configuration.archiveIdentity, "original-archive")
            XCTAssertEqual(restored.profileID, "original-profile")
            XCTAssertEqual(restored.tagIDs, ["work-id"])
            XCTAssertEqual(restored.tagNames, ["Meeting"])
            XCTAssertEqual(restored.locationData, location)
            XCTAssertTrue(restored.wiFiOnly)
            XCTAssertFalse(FileManager.default.fileExists(atPath: fixture.converted.parts[0].url.path))
            XCTAssertEqual(try Data(contentsOf: directory.appendingPathComponent("part-0.wav")), fixture.audio)
            XCTAssertFalse(restored.canReleaseAudio)
        }
    }

    func testRetryUsesOriginalIDsCaptureTimeProfileAndArchiveAfterReload() throws {
        try withWorkspace { root in
            let fixture = try fixture(in: root, name: "Café + meeting.m4a")
            var job = try stage(fixture, root: root)
            let request = try job.uploadRequest(for: 0)
            job.state = .failed
            job.error = "Connection lost"
            try job.save(in: job.directory(in: root))
            let restored = try BackgroundImportJob.load(from: job.directory(in: root))
            let retry = try restored.uploadRequest(for: 0)
            XCTAssertEqual(retry, request)
            XCTAssertEqual(retry.httpMethod, "PUT")
            XCTAssertEqual(retry.value(forHTTPHeaderField: "X-Omi-Profile"), "original-profile")
            XCTAssertEqual(retry.value(forHTTPHeaderField: "X-Daythread-Destination"), "own:original-archive")
            XCTAssertFalse(retry.allowsCellularAccess)
            let url = try XCTUnwrap(retry.url)
            XCTAssertEqual(url.host, "original.invalid")
            XCTAssertTrue(url.path.hasSuffix(fixture.plan.parts[0].id.uuidString.lowercased()))
            let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
            XCTAssertEqual(query.first { $0.name == "source_id" }?.value, "Café + meeting.m4a")
            XCTAssertEqual(query.first { $0.name == "started_at" }?.value, "2026-10-29T10:00:00.000Z")
            XCTAssertEqual(query.first { $0.name == "vendor_id" }?.value, "plaud")
            XCTAssertEqual(restored.error, "Connection lost")
        }
    }

    func testSuccessStatusWithWrongReceiptNeverAcknowledgesOrReleasesAudio() throws {
        try withWorkspace { root in
            let fixture = try fixture(in: root)
            var job = try stage(fixture, root: root)
            let response = try http(200)
            var receipt = try receipt(for: fixture.plan)
            receipt["id"] = UUID().uuidString
            XCTAssertThrowsError(try job.accept(JSONSerialization.data(withJSONObject: receipt), response: response, part: 0))
            XCTAssertTrue(job.acknowledged.isEmpty)
            receipt = try self.receipt(for: fixture.plan)
            receipt["started_at"] = "2026-10-29T10:00:01Z"
            XCTAssertThrowsError(try job.accept(JSONSerialization.data(withJSONObject: receipt), response: response, part: 0))
            XCTAssertTrue(job.acknowledged.isEmpty)
            XCTAssertFalse(job.canReleaseAudio)
            XCTAssertTrue(FileManager.default.fileExists(atPath: job.directory(in: root).appendingPathComponent("part-0.wav").path))
        }
    }

    func testMatchingReceiptSurvivesRestartAndDetailsMustCompleteBeforeAudioRelease() throws {
        try withWorkspace { root in
            let fixture = try fixture(in: root)
            var job = try stage(fixture, root: root)
            let data = try JSONSerialization.data(withJSONObject: receipt(for: fixture.plan))
            try job.accept(data, response: http(200), part: 0)
            try job.save(in: job.directory(in: root))
            var restored = try BackgroundImportJob.load(from: job.directory(in: root))
            XCTAssertEqual(restored.acknowledged, [0])
            XCTAssertFalse(restored.canReleaseAudio, "Audio receipts do not acknowledge optional tag/location writes")
            restored.detailsSaved.insert(0)
            try restored.save(in: restored.directory(in: root))
            XCTAssertTrue(try BackgroundImportJob.load(from: restored.directory(in: root)).canReleaseAudio)
            // Repeated receipt delivery remains idempotent.
            try restored.accept(data, response: http(200), part: 0)
            XCTAssertEqual(restored.acknowledged, [0])
        }
    }

    func testHTTPFailureAndMissingResponseCannotAcknowledgeEvenAMatchingBody() throws {
        try withWorkspace { root in
            let fixture = try fixture(in: root)
            var job = try stage(fixture, root: root)
            let data = try JSONSerialization.data(withJSONObject: receipt(for: fixture.plan))
            XCTAssertThrowsError(try job.accept(data, response: http(503), part: 0))
            XCTAssertThrowsError(try job.accept(data, response: nil, part: 0))
            XCTAssertThrowsError(try job.accept(data, response: http(200), part: 1))
            XCTAssertTrue(job.acknowledged.isEmpty)
        }
    }

    func testRestorationRejectsMismatchedDirectoryAndImpossibleAcknowledgements() throws {
        try withWorkspace { root in
            let fixture = try fixture(in: root)
            var job = try stage(fixture, root: root)
            let directory = job.directory(in: root)
            job.acknowledged = [1]
            try job.save(in: directory)
            XCTAssertThrowsError(try BackgroundImportJob.load(from: directory))
            job.acknowledged = []
            job.detailsSaved = [0]
            try job.save(in: directory)
            XCTAssertThrowsError(try BackgroundImportJob.load(from: directory))
            job.detailsSaved = []
            try job.save(in: directory)
            let other = root.appendingPathComponent(UUID().uuidString)
            try FileManager.default.moveItem(at: directory, to: other)
            XCTAssertThrowsError(try BackgroundImportJob.load(from: other))
        }
    }

    func testInvalidDestinationIsRejectedBeforeMovingAudio() throws {
        try withWorkspace { root in
            let fixture = try fixture(in: root)
            XCTAssertThrowsError(try BackgroundImportJob.stage(converted: fixture.converted, plan: fixture.plan,
                sourceName: "Recording", configuration: .init(endpoints: [.init(address: "file:///tmp/archive")]),
                profileID: "profile", tagIDs: [], tagNames: [], locationData: nil, wiFiOnly: false, root: root))
            XCTAssertTrue(FileManager.default.fileExists(atPath: fixture.converted.parts[0].url.path))
        }
    }

    private struct Fixture {
        let converted: ConvertedRecording
        let plan: RecordingUploadPlan
        let audio: Data
    }

    private func fixture(in root: URL, name: String = "Meeting.wav", treatAsMeeting: Bool = false) throws -> Fixture {
        let audio = Data("test audio body".utf8)
        let source = root.appendingPathComponent("original-part.wav")
        try audio.write(to: source)
        let body: [String: Any] = [
            "recording_id": UUID().uuidString, "source_id": name,
            "started_at": "2026-10-29T10:00:00Z", "ended_at": "2026-10-29T10:00:01Z", "vendor_id": "plaud",
            "treat_as_meeting": treatAsMeeting,
            "parts": [["id": UUID().uuidString, "digest": "test-digest", "uploaded": false]],
        ]
        let plan = try JSONDecoder().decode(RecordingUploadPlan.self, from: JSONSerialization.data(withJSONObject: body))
        return Fixture(converted: ConvertedRecording(parts: [.init(url: source, digest: "test-digest", samples: 16_000)]),
                       plan: plan, audio: audio)
    }

    private func stage(_ fixture: Fixture, root: URL, location: Data? = nil) throws -> BackgroundImportJob {
        try BackgroundImportJob.stage(converted: fixture.converted, plan: fixture.plan,
            sourceName: fixture.plan.sourceID, configuration: .init(endpoints: [.init(address: "https://original.invalid")], archiveIdentity: "original-archive"),
            profileID: "original-profile", tagIDs: ["work-id"], tagNames: ["Meeting"], locationData: location, wiFiOnly: true, root: root)
    }

    private func receipt(for plan: RecordingUploadPlan) throws -> [String: Any] {
        ["id": plan.parts[0].id.uuidString, "device_id": RecordingImport.deviceID,
         "started_at": plan.startedAt, "ended_at": plan.endedAt, "state": "queued"]
    }

    private func http(_ status: Int) throws -> HTTPURLResponse {
        try XCTUnwrap(HTTPURLResponse(url: URL(string: "https://original.invalid")!, statusCode: status, httpVersion: nil, headerFields: nil))
    }

    private func withWorkspace(_ body: (URL) throws -> Void) throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try body(root)
    }
}

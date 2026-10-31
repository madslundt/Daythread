import XCTest
@testable import OmiCore

@MainActor
final class UserInitiatedUploadTests: XCTestCase {
    private func store() throws -> DurableStore {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        let store = try DurableStore(directory: directory); store.destinationID = "home"
        return store
    }
    private func audio(_ store: DurableStore, at date: Date, device: String = "pendant") throws -> UUID {
        try store.begin(deviceID: device, codec: 0, clock: CaptureClock(wall: date))
        try store.append(frame: Data(count: 32000), decoded: Data(count: 32000)); try store.finish()
        return try XCTUnwrap(store.items.last?.id)
    }
    func testWearableWindowAudioAndJobPreemptOlderUnrelatedAudio() throws {
        let store = try store(), end = Date(timeIntervalSince1970: 1000)
        let historical = try audio(store, at: end.addingTimeInterval(-600))
        let recent = try audio(store, at: end.addingTimeInterval(-15))
        let job = try store.wearableAction(deviceID: "pendant", at: end, action: .answer, durationSeconds: 30)
        XCTAssertEqual(store.nextUpload(destinationID: "home")?.id, recent)
        try store.acknowledge(recent)
        XCTAssertEqual(store.nextUpload(destinationID: "home")?.id, job)
        try store.acknowledge(job)
        XCTAssertEqual(store.nextUpload(destinationID: "home")?.id, historical)
    }
    func testMarkAndMeetingPreemptBulkAudioButKeepTheirClickOrder() throws {
        let store = try store(), end = Date(timeIntervalSince1970: 1000)
        let historical = try audio(store, at: end.addingTimeInterval(-600))
        let mark = try store.event(deviceID: "pendant", at: end, action: .mark)
        let meeting = try store.event(deviceID: "pendant", at: end.addingTimeInterval(1), action: .meetingToggle)
        XCTAssertEqual(store.nextUpload(destinationID: "home")?.id, mark)
        try store.acknowledge(mark)
        XCTAssertEqual(store.nextUpload(destinationID: "home")?.id, meeting)
        try store.acknowledge(meeting)
        XCTAssertEqual(store.nextUpload(destinationID: "home")?.id, historical)
    }
    private func receipt(_ item: OutboxItem, file: URL) throws -> Data {
        var body: [String: Any]
        if item.kind == .audio {
            body = ["id": item.id.uuidString, "device_id": item.deviceID, "started_at": DurableStore.stamp(item.capturedAt),
                    "ended_at": DurableStore.stamp(item.endedAt!), "state": "queued"]
        } else {
            body = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: file)) as? [String: Any])
            if item.kind == .generation { body["state"] = "queued" }
            if item.kind == .captureState { body["event_id"] = item.id.uuidString }
        }
        return try JSONSerialization.data(withJSONObject: body)
    }
    func testRegularHandoffSendsOnlyOriginalWindowAudioThenJobAndValidatesEachReceipt() async throws {
        let store = try store(), end = Date(timeIntervalSince1970: 1000)
        let historical = try audio(store, at: end.addingTimeInterval(-600))
        let recent = try audio(store, at: end.addingTimeInterval(-15))
        let job = try store.wearableAction(deviceID: "pendant", at: end, action: .answer, durationSeconds: 30)
        var sent: [UUID] = []
        let accepted = try await UserInitiatedUpload.drain(store: store, submissionID: job, destinationID: "home") { item, file in
            sent.append(item.id)
            XCTAssertTrue(FileManager.default.fileExists(atPath: file.path))
            return try self.receipt(item, file: file)
        }
        XCTAssertTrue(accepted); XCTAssertEqual(sent, [recent, job])
        XCTAssertEqual(store.pending.map(\.id), [historical])
        let repeated = try await UserInitiatedUpload.drain(store: store, submissionID: job, destinationID: "home") { _, _ in
            XCTFail("An accepted original ID must not upload again"); return Data()
        }
        XCTAssertTrue(repeated)
    }
    func testInvalidOrCancelledHandoffPreservesOriginalIDAndFilesForDurableRetry() async throws {
        let store = try store(), at = Date(timeIntervalSince1970: 1000)
        let job = try store.wearableAction(deviceID: "pendant", at: at, action: .answer, durationSeconds: 30)
        do {
            _ = try await UserInitiatedUpload.drain(store: store, submissionID: job, destinationID: "home") { _, _ in Data("{}".utf8) }
            XCTFail("A proxy's arbitrary success cannot acknowledge the job")
        } catch { XCTAssertTrue(error is CaptureError) }
        do {
            _ = try await UserInitiatedUpload.drain(store: store, submissionID: job, destinationID: "home") { _, _ in throw CancellationError() }
            XCTFail("Cancellation must reach the handoff owner")
        } catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertEqual(store.pending.map(\.id), [job])
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.file(job, extension: "json").path))
    }
    func testDependenciesExcludeOtherDevicesAndFreezeBeforeLaterUnfinishedAudio() throws {
        let store = try store(), end = Date(timeIntervalSince1970: 1000)
        _ = try audio(store, at: end.addingTimeInterval(-20), device: "phone")
        let original = try audio(store, at: end.addingTimeInterval(-15))
        let job = try store.wearableAction(deviceID: "pendant", at: end, action: .answer, durationSeconds: 30)
        try store.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: end.addingTimeInterval(-0.02)))
        try store.append(frame: Data(count: 640), decoded: Data(count: 640))
        XCTAssertEqual(store.nextSubmissionUpload(job, destinationID: "home")?.id, original)
        try store.acknowledge(original)
        XCTAssertEqual(store.nextSubmissionUpload(job, destinationID: "home")?.id, job,
                       "Post-click segments must not extend the original audio barrier")
    }
    func testHeldJobDoesNotBlockOtherClicksAndLostHoldExpires() throws {
        let store = try store(), at = Date(timeIntervalSince1970: 1000)
        let job = try store.wearableAction(deviceID: "pendant", at: at, action: .answer, durationSeconds: 30)
        let mark = try store.event(deviceID: "pendant", at: at.addingTimeInterval(1), action: .mark)
        var holds = UserInitiatedUploadHolds(); holds.hold(job, at: 100)
        XCTAssertEqual(store.nextUpload(destinationID: "home", excluding: holds.active(at: 101))?.id, mark)
        XCTAssertEqual(store.nextUpload(destinationID: "home", excluding: holds.active(at: 108))?.id, job)
        holds.hold(job, at: 200); holds.release(job)
        XCTAssertTrue(holds.active(at: 201).isEmpty)
    }
    func testClickCaptureStatePreemptsBulkAndSurvivesRestart() async throws {
        let store = try store(), at = Date(timeIntervalSince1970: 1000)
        _ = try audio(store, at: at.addingTimeInterval(-600))
        let state = try store.captureState(phoneID: UUID(), at: at, state: "paused", cause: "user_pause")
        try store.prioritize(state)
        let reopened = try DurableStore(directory: store.directory)
        XCTAssertEqual(reopened.nextUpload(destinationID: "home")?.id, state)
        let accepted = try await UserInitiatedUpload.drain(store: reopened, submissionID: state, destinationID: "home") { item, file in
            try self.receipt(item, file: file)
        }
        XCTAssertTrue(accepted)
    }

}

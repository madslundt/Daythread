import XCTest
@testable import OmiCore

final class WearableAudioCutoffTests: XCTestCase {
    private func store() throws -> DurableStore {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        let store = try DurableStore(directory: directory); store.destinationID = "home"
        return store
    }
    private func packet(_ sequence: UInt16, _ fragment: UInt8, _ payload: Data) -> Data {
        Data([UInt8(truncatingIfNeeded: sequence), UInt8(truncatingIfNeeded: sequence >> 8), fragment]) + payload
    }
    func testPreClickFrameCompletingAfterClickIsIncludedBeforePublication() throws {
        let store = try store(), end = Date(timeIntervalSince1970: 1000)
        let tail = Data(repeating: 9, count: 640), afterClick = Data(repeating: 8, count: 640)
        let tailClock = CaptureClock(wall: end.addingTimeInterval(-0.02), uptime: 500)
        var reassembler = OmiReassembler(), cutoffs = WearableAudioCutoff()
        _ = reassembler.consume(packet(1, 0, tail.prefix(320)))
        XCTAssertTrue(reassembler.hasPendingFrame)
        try store.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: end.addingTimeInterval(-1)))
        for _ in 0..<49 { try store.append(frame: Data(count: 640), decoded: Data(count: 640)) }
        let originalAudioID = try XCTUnwrap(store.items.first?.id)
        let job = try cutoffs.enqueue(store: store, deviceID: "pendant", end: end, action: .answer,
                                     durationSeconds: 30, pendingFrame: tailClock)
        XCTAssertTrue(store.isRecording, "Keep the latest question and its tail in one transcription chunk")
        XCTAssertFalse(try XCTUnwrap(store.items.first(where: { $0.id == job })).ready)
        XCTAssertNil(store.nextSubmissionUpload(job, destinationID: "home"))
        XCTAssertNil(reassembler.consume(packet(2, 1, tail.suffix(320))).frame)
        let completed = try XCTUnwrap(reassembler.consume(packet(3, 0, afterClick)).frame)
        try store.append(frame: completed, decoded: FrameDecoder(codec: 0).decode(completed))
        XCTAssertEqual(try cutoffs.completedFrame(tailClock, store: store), [job])
        let request = try XCTUnwrap(store.items.first(where: { $0.id == job }))
        XCTAssertTrue(request.ready); XCTAssertEqual(request.capturedAt, end)
        XCTAssertEqual(store.audioDependencies(for: request).map(\.id), [originalAudioID])
        let audio = try XCTUnwrap(store.items.first(where: { $0.id == originalAudioID }))
        XCTAssertEqual(audio.endedAt, end)
        let pcm = try Data(contentsOf: store.file(originalAudioID, extension: "wav")).dropFirst(44)
        XCTAssertEqual(pcm.count, 32000); XCTAssertEqual(pcm.suffix(640), tail)
        // Even a later segment timestamped 1ms before the click is outside the frozen frame boundary.
        try store.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: end.addingTimeInterval(-0.001)))
        try store.append(frame: afterClick, decoded: afterClick); try store.finish()
        XCTAssertEqual(store.audioDependencies(for: request).map(\.id), [originalAudioID])
    }
    func testMultipleClicksSharingOneFrameKeepIndependentEndsAndIDs() throws {
        let store = try store(), end = Date(timeIntervalSince1970: 1000)
        let frame = CaptureClock(wall: end.addingTimeInterval(-0.02), uptime: 500)
        var cutoffs = WearableAudioCutoff()
        let first = try cutoffs.enqueue(store: store, deviceID: "pendant", end: end, action: .answer,
                                       durationSeconds: 30, pendingFrame: frame)
        let secondEnd = end.addingTimeInterval(0.005)
        let second = try cutoffs.enqueue(store: store, deviceID: "pendant", end: secondEnd, action: .summarize,
                                        durationSeconds: 60, pendingFrame: frame)
        XCTAssertNotEqual(first, second)
        XCTAssertTrue(try cutoffs.completedFrame(CaptureClock(wall: frame.wall, uptime: 501), store: store).isEmpty)
        try store.begin(deviceID: "pendant", codec: 0, clock: frame)
        try store.append(frame: Data(count: 640), decoded: Data(count: 640))
        XCTAssertEqual(try cutoffs.completedFrame(frame, store: store), [first, second])
        XCTAssertEqual(store.items.first(where: { $0.id == first })?.capturedAt, end)
        XCTAssertEqual(store.items.first(where: { $0.id == second })?.capturedAt, secondEnd)
        XCTAssertTrue(cutoffs.pendingIDs.isEmpty)
    }
    func testExpiredTailPublishesPrefixAndGapWithoutTakingLaterSpeech() throws {
        let store = try store(), end = Date(timeIntervalSince1970: 1000)
        let frame = CaptureClock(wall: end.addingTimeInterval(-0.02), uptime: 500)
        var cutoffs = WearableAudioCutoff()
        try store.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: end.addingTimeInterval(-1)))
        for _ in 0..<49 { try store.append(frame: Data(count: 640), decoded: Data(count: 640)) }
        let prefix = try XCTUnwrap(store.items.first?.id)
        let job = try cutoffs.enqueue(store: store, deviceID: "pendant", end: end, action: .answer,
                                     durationSeconds: 30, pendingFrame: frame)
        XCTAssertEqual(try cutoffs.expire([job], store: store), [job])
        let request = try XCTUnwrap(store.items.first(where: { $0.id == job }))
        let gap = try XCTUnwrap(store.items.first(where: { $0.id == request.cutoffGapID }))
        XCTAssertEqual(gap.capturedAt, frame.wall); XCTAssertEqual(gap.endedAt, end)
        XCTAssertEqual(gap.destinationID, "home"); XCTAssertEqual(gap.cause, "incomplete_frame")
        XCTAssertEqual(store.nextSubmissionUpload(job, destinationID: "home")?.id, gap.id)
        try store.acknowledge(gap.id)
        XCTAssertEqual(store.nextSubmissionUpload(job, destinationID: "home")?.id, prefix)
        try store.acknowledge(prefix)
        XCTAssertEqual(store.nextSubmissionUpload(job, destinationID: "home")?.id, job)
        try store.begin(deviceID: "pendant", codec: 0, clock: frame)
        try store.append(frame: Data(count: 640), decoded: Data(count: 640)); try store.finish()
        XCTAssertTrue(try cutoffs.completedFrame(frame, store: store).isEmpty)
        XCTAssertTrue(store.audioDependencies(for: request).isEmpty, "Acknowledged prefix only; never extend a timed-out request")
    }
    func testRestartFinalizesDraftFromRecoveredPrefixAndRecordsMissingTailInOriginalArchive() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        let end = Date(timeIntervalSince1970: 1000)
        var original: DurableStore? = try DurableStore(directory: directory)
        original!.destinationID = "home"; original!.profileID = "work"
        try original!.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: end.addingTimeInterval(-1)))
        for _ in 0..<49 { try original!.append(frame: Data(count: 640), decoded: Data(count: 640)) }
        var cutoffs = WearableAudioCutoff()
        let id = try cutoffs.enqueue(store: original!, deviceID: "pendant", end: end, action: .answer,
                                    durationSeconds: 30, pendingFrame: CaptureClock(wall: end.addingTimeInterval(-0.02), uptime: 500))
        original = nil
        let recovered = try DurableStore(directory: directory)
        let request = try XCTUnwrap(recovered.items.first(where: { $0.id == id }))
        XCTAssertTrue(request.ready); XCTAssertNil(request.awaitingAudioTailAt)
        XCTAssertEqual(request.capturedAt, end); XCTAssertEqual(request.destinationID, "home")
        let gap = try XCTUnwrap(recovered.items.first(where: { $0.id == request.cutoffGapID }))
        XCTAssertEqual(gap.destinationID, "home"); XCTAssertEqual(gap.resolvedProfileID, "work")
        XCTAssertEqual(recovered.nextSubmissionUpload(id, destinationID: "home")?.id, gap.id)
        XCTAssertEqual(recovered.audioDependencies(for: request).count, 1)
    }
    func testRecoveryReusesGapIfInterruptedBetweenGapAndDraftCommit() throws {
        let store = try store(), end = Date(timeIntervalSince1970: 1000)
        var cutoffs = WearableAudioCutoff()
        let id = try cutoffs.enqueue(store: store, deviceID: "pendant", end: end, action: .answer,
                                    durationSeconds: 30, pendingFrame: CaptureClock(wall: end.addingTimeInterval(-0.02), uptime: 500))
        let reservedGap = try XCTUnwrap(store.items.first(where: { $0.id == id })?.cutoffGapID)
        XCTAssertFalse(store.items.contains { $0.id == reservedGap }, "Reserve identity without inventing a gap on successful capture")
        try cutoffs.expire([id], store: store)
        let index = store.directory.appendingPathComponent("index.json")
        var records = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: index)) as? [[String: Any]])
        let jobIndex = try XCTUnwrap(records.firstIndex { $0["id"] as? String == id.uuidString })
        records[jobIndex]["ready"] = false
        records[jobIndex]["awaitingAudioTailAt"] = end.addingTimeInterval(-0.02).timeIntervalSince1970 * 1000
        try JSONSerialization.data(withJSONObject: records).write(to: index)
        let recovered = try DurableStore(directory: store.directory)
        XCTAssertEqual(recovered.items.filter { $0.kind == .gap }.count, 1)
        XCTAssertEqual(recovered.items.first(where: { $0.id == id })?.cutoffGapID, reservedGap)
        XCTAssertTrue(try XCTUnwrap(recovered.items.first(where: { $0.id == id })).ready)
    }
    func testNoPendingFrameCheckpointsAndPublishesImmediately() throws {
        let store = try store(), end = Date(timeIntervalSince1970: 1000)
        var cutoffs = WearableAudioCutoff()
        try store.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: end.addingTimeInterval(-1)))
        try store.append(frame: Data(count: 32000), decoded: Data(count: 32000))
        let id = try cutoffs.enqueue(store: store, deviceID: "pendant", end: end, action: .translate,
                                    durationSeconds: 30, pendingFrame: nil)
        XCTAssertFalse(store.isRecording); XCTAssertTrue(cutoffs.pendingIDs.isEmpty)
        XCTAssertTrue(try XCTUnwrap(store.items.first(where: { $0.id == id })).ready)
    }
}

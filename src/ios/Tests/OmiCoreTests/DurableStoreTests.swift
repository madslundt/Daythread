import XCTest
@testable import OmiCore

final class DurableStoreTests: XCTestCase {
    private func folder() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }
    func testRestartReconstructsUnfinishedSourceAndKeepsStableIDAndMilliseconds() throws {
        let directory = try folder()
        let captured = Date(timeIntervalSince1970: 1000.125)
        var original: DurableStore? = try DurableStore(directory: directory)
        try original!.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: captured, uptime: 50))
        let id = try XCTUnwrap(original!.items.first?.id)
        try original!.append(frame: Data(repeating: 7, count: 640), decoded: Data(repeating: 7, count: 640))
        original = nil // Simulates process death before a WAV/index completion.
        let recovered = try DurableStore(directory: directory)
        XCTAssertEqual(recovered.pending.first?.id, id)
        XCTAssertEqual(try XCTUnwrap(recovered.pending.first).capturedAt.timeIntervalSince1970, 1000.125, accuracy: 0.0001)
        XCTAssertEqual(recovered.pending.first?.endedAt, captured.addingTimeInterval(0.02))
        XCTAssertEqual(try Data(contentsOf: recovered.file(id, extension: "wav")).count, 684)
        XCTAssertTrue(FileManager.default.fileExists(atPath: recovered.file(id, extension: "frames").path))
    }
    func testEventsAndGapsPersistOriginalPayloadOnRetry() throws {
        let directory = try folder(); let store = try DurableStore(directory: directory)
        let date = Date(timeIntervalSince1970: 1000.123)
        let id = try store.event(deviceID: "pendant", at: date, action: .meetingToggle, meetingMaxMinutes: 45, meetingMinSeconds: 90, markerCooldownSeconds: 120)
        let body = try Data(contentsOf: store.file(id, extension: "json"))
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(payload["meeting_max_minutes"] as? Int, 45)
        XCTAssertEqual(payload["meeting_min_seconds"] as? Int, 90)
        XCTAssertEqual(payload["marker_cooldown_seconds"] as? Int, 120)
        try store.failed(id, message: "Offline")
        let reopened = try DurableStore(directory: directory)
        XCTAssertEqual(reopened.pending.first?.id, id)
        XCTAssertEqual(reopened.pending.first?.attempts, 1)
        XCTAssertEqual(reopened.pending.first?.meetingMaxMinutes, 45)
        XCTAssertEqual(reopened.pending.first?.meetingMinSeconds, 90)
        XCTAssertEqual(reopened.pending.first?.markerCooldownSeconds, 120)
        XCTAssertEqual(try Data(contentsOf: reopened.file(id, extension: "json")), body)
        let gap = try reopened.gap(deviceID: "pendant", start: date, end: date.addingTimeInterval(2), reason: "BLE loss")
        XCTAssertNotEqual(gap, id)
        XCTAssertEqual(reopened.pending.count, 2)
        try reopened.acknowledge(id)
        XCTAssertEqual(reopened.pending.map(\.id), [gap])
    }
    func testStorageBoundNeverDeletesUnsentAudio() throws {
        let store = try DurableStore(directory: folder())
        try store.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: Date(timeIntervalSince1970: 1000)))
        try store.append(frame: Data(count: 640), decoded: Data(count: 640)); try store.finish()
        let id = try XCTUnwrap(store.pending.first?.id)
        store.maximumBytes = 1
        XCTAssertThrowsError(try store.begin(deviceID: "pendant", codec: 0, clock: CaptureClock()))
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.file(id, extension: "wav").path))
        XCTAssertEqual(store.pending.count, 1)
    }
    func testOnlyAcknowledgedAudioExpiresAndEventHistoryRemains() throws {
        let store = try DurableStore(directory: folder())
        let date = Date(timeIntervalSince1970: 1000)
        let event = try store.event(deviceID: "p", at: date, action: .mark)
        try store.acknowledge(event)
        try store.begin(deviceID: "p", codec: 0, clock: CaptureClock(wall: date))
        try store.append(frame: Data(count: 640), decoded: Data(count: 640)); try store.finish()
        let audio = try XCTUnwrap(store.pending.first?.id)
        try store.pruneAcknowledged(before: .distantFuture)
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.file(audio, extension: "wav").path))
        try store.acknowledge(audio); try store.pruneAcknowledged(before: .distantFuture)
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.file(audio, extension: "wav").path))
        XCTAssertEqual(store.items.map(\.id), [event])
    }
    func testLocalRewindPreservesMissingTimeAsSilence() throws {
        let store = try DurableStore(directory: folder())
        let start = Date(timeIntervalSince1970: 1000)
        try store.begin(deviceID: "p", codec: 0, clock: CaptureClock(wall: start))
        try store.append(frame: Data(repeating: 1, count: 32000), decoded: Data(repeating: 1, count: 32000)); try store.finish()
        let rewind = try store.rewind(minutes: 1, end: start.addingTimeInterval(60))
        let data = try Data(contentsOf: rewind.url)
        XCTAssertEqual(data.count, 60 * 32000 + 44)
        XCTAssertEqual(rewind.gaps, 1)
        XCTAssertEqual(data[44], 1); XCTAssertEqual(data[data.count - 1], 0)
        try FileManager.default.removeItem(at: rewind.url)
    }
    func testCorruptIndexStopsInsteadOfDiscardingRecordings() throws {
        let directory = try folder()
        try Data("bad json".utf8).write(to: directory.appendingPathComponent("index.json"))
        try Data([1, 2, 3]).write(to: directory.appendingPathComponent("unrecoverable.frames"))
        XCTAssertThrowsError(try DurableStore(directory: directory))
        XCTAssertTrue(FileManager.default.fileExists(atPath: directory.appendingPathComponent("unrecoverable.frames").path))
    }
    func testFailedFinalizationKeepsCaptureForRetryWithoutRestart() throws {
        let store = try DurableStore(directory: folder())
        try store.begin(deviceID: "p", codec: 0, clock: CaptureClock())
        let id = try XCTUnwrap(store.items.first?.id)
        try store.append(frame: Data(count: 640), decoded: Data(count: 640))
        let wav = store.file(id, extension: "wav")
        try FileManager.default.createDirectory(at: wav, withIntermediateDirectories: false)
        XCTAssertThrowsError(try store.finish())
        XCTAssertTrue(store.isRecording)
        XCTAssertEqual(store.segmentSeconds, 0.02)
        XCTAssertThrowsError(try store.append(frame: Data(count: 640), decoded: Data(count: 640)))
        try FileManager.default.removeItem(at: wav)
        try store.finish()
        XCTAssertFalse(store.isRecording)
        XCTAssertEqual(store.pending.map(\.id), [id])
        XCTAssertEqual(try Data(contentsOf: wav).count, 684)
    }
    func testFailedMetadataIndexSaveDoesNotExposePhantomEvent() throws {
        let directory = try folder(); let store = try DurableStore(directory: directory)
        let index = directory.appendingPathComponent("index.json")
        try FileManager.default.removeItem(at: index)
        try FileManager.default.createDirectory(at: index, withIntermediateDirectories: false)
        XCTAssertThrowsError(try store.event(deviceID: "p", at: Date(), action: .mark))
        XCTAssertTrue(store.items.isEmpty)
        let payloads = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil).filter { $0.lastPathComponent != "index.json" }
        XCTAssertTrue(payloads.isEmpty)
    }
    func testRestartRebuildsMissingMetadataUploadFilesFromIndex() throws {
        let directory = try folder(); let store = try DurableStore(directory: directory)
        let event = try store.event(deviceID: "p", at: Date(), action: .mark)
        let gap = try store.gap(deviceID: "p", start: Date(), end: Date().addingTimeInterval(1), reason: "BLE loss")
        let eventBody = try Data(contentsOf: store.file(event, extension: "json"))
        let gapBody = try Data(contentsOf: store.file(gap, extension: "json"))
        try FileManager.default.removeItem(at: store.file(event, extension: "json"))
        try FileManager.default.removeItem(at: store.file(gap, extension: "json"))
        let restored = try DurableStore(directory: directory)
        XCTAssertEqual(try JSONSerialization.jsonObject(with: Data(contentsOf: restored.file(event, extension: "json"))) as? NSDictionary, try JSONSerialization.jsonObject(with: eventBody) as? NSDictionary)
        XCTAssertEqual(try JSONSerialization.jsonObject(with: Data(contentsOf: restored.file(gap, extension: "json"))) as? NSDictionary, try JSONSerialization.jsonObject(with: gapBody) as? NSDictionary)
    }
    func testStorageAccountingIncludesMetadataAndIndexAcrossRestart() throws {
        let directory = try folder(); let store = try DurableStore(directory: directory)
        _ = try store.event(deviceID: "p", at: Date(), action: .mark)
        try store.begin(deviceID: "p", codec: 0, clock: CaptureClock())
        try store.append(frame: Data(count: 640), decoded: Data(count: 640)); try store.finish()
        let restored = try DurableStore(directory: directory)
        XCTAssertEqual(store.storageBytes, restored.storageBytes)
        restored.maximumBytes = restored.storageBytes + 1
        XCTAssertThrowsError(try restored.event(deviceID: "p", at: Date(), action: .mark))
    }
    func testFailedFinalizationIndexSaveKeepsSegmentForRetryAndCountsWAVOnce() throws {
        let directory = try folder(); let store = try DurableStore(directory: directory)
        try store.begin(deviceID: "p", codec: 0, clock: CaptureClock())
        let id = try XCTUnwrap(store.items.first?.id)
        try store.append(frame: Data(count: 640), decoded: Data(count: 640))
        let index = directory.appendingPathComponent("index.json")
        let originalIndex = try Data(contentsOf: index)
        try FileManager.default.removeItem(at: index)
        try FileManager.default.createDirectory(at: index, withIntermediateDirectories: false)
        XCTAssertThrowsError(try store.finish())
        XCTAssertTrue(store.isRecording)
        XCTAssertTrue(store.pending.isEmpty)
        try FileManager.default.removeItem(at: index)
        try originalIndex.write(to: index)
        try store.finish()
        XCTAssertEqual(store.pending.map(\.id), [id])
        XCTAssertEqual(store.storageBytes, try DurableStore(directory: directory).storageBytes)
    }
    func testRecoveryKeepsCompleteFramesBeforeCorruptFrame() throws {
        let directory = try folder()
        var store: DurableStore? = try DurableStore(directory: directory)
        try store!.begin(deviceID: "p", codec: 0, clock: CaptureClock())
        let id = try XCTUnwrap(store!.items.first?.id)
        try store!.append(frame: Data(repeating: 7, count: 640), decoded: Data(repeating: 7, count: 640))
        // Complete framing with an invalid odd-length PCM payload must not hide earlier audio.
        try store!.append(frame: Data([1]), decoded: Data())
        store = nil
        let recovered = try DurableStore(directory: directory)
        XCTAssertEqual(recovered.pending.map(\.id), [id])
        XCTAssertEqual(try Data(contentsOf: recovered.file(id, extension: "wav")).count, 684)
        XCTAssertNotNil(recovered.pending.first?.error)
        XCTAssertEqual(try Data(contentsOf: recovered.file(id, extension: "frames")).count, 649)
    }
    func testFailedAcknowledgementSaveKeepsItemPendingInMemoryAndAfterRestart() throws {
        let directory = try folder(); let store = try DurableStore(directory: directory)
        let id = try store.event(deviceID: "p", at: Date(), action: .mark)
        let index = directory.appendingPathComponent("index.json")
        let originalIndex = try Data(contentsOf: index)
        try FileManager.default.removeItem(at: index)
        try FileManager.default.createDirectory(at: index, withIntermediateDirectories: false)
        XCTAssertThrowsError(try store.acknowledge(id))
        XCTAssertEqual(store.pending.map(\.id), [id])
        try FileManager.default.removeItem(at: index)
        try originalIndex.write(to: index)
        XCTAssertEqual(try DurableStore(directory: directory).pending.map(\.id), [id])
    }
    func testUploadRebuildsMissingMetadataWithoutRestart() throws {
        let store = try DurableStore(directory: folder())
        let id = try store.event(deviceID: "p", at: Date(), action: .mark)
        let item = try XCTUnwrap(store.pending.first)
        let url = store.file(id, extension: "json")
        let original = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? NSDictionary
        try FileManager.default.removeItem(at: url)
        let restored = try store.uploadFile(for: item)
        XCTAssertEqual(try JSONSerialization.jsonObject(with: Data(contentsOf: restored)) as? NSDictionary, original)
        XCTAssertEqual(store.pending.map(\.id), [id])
    }
    func testMetadataCannotConsumeSpaceReservedForCurrentWAV() throws {
        let store = try DurableStore(directory: folder())
        try store.begin(deviceID: "p", codec: 0, clock: CaptureClock())
        try store.append(frame: Data(count: 640), decoded: Data(count: 640))
        store.maximumBytes = store.storageBytes + 1000
        XCTAssertThrowsError(try store.event(deviceID: "p", at: Date(), action: .mark))
        try store.finish()
        XCTAssertEqual(store.pending.count, 1)
        XCTAssertLessThan(store.storageBytes, store.maximumBytes)
    }
}

extension DurableStoreTests {
    func testCaptureTransitionsKeepIdentityCauseAndPayloadAcrossRecovery() throws {
        let directory = try folder(), store = try DurableStore(directory: directory)
        let phone = UUID(), at = Date(timeIntervalSince1970: 1000.125)
        let id = try store.captureState(phoneID: phone, at: at, state: "paused", cause: "user_pause")
        let before = try Data(contentsOf: store.uploadFile(for: XCTUnwrap(store.pending.first)))
        try store.failed(id, message: "Offline")
        try FileManager.default.removeItem(at: store.file(id, extension: "json"))
        let recovered = try DurableStore(directory: directory)
        let item = try XCTUnwrap(recovered.pending.first)
        XCTAssertEqual(item.id,id); XCTAssertEqual(item.captureState,"paused"); XCTAssertEqual(item.cause,"user_pause")
        XCTAssertEqual(item.attempts,1)
        XCTAssertEqual(try Data(contentsOf: recovered.uploadFile(for: item)),before)
        let value = try XCTUnwrap(JSONSerialization.jsonObject(with: before) as? [String:Any])
        XCTAssertEqual(value["phone_id"] as? String,phone.uuidString)
        XCTAssertEqual(value["occurred_at"] as? String,DurableStore.stamp(at))
    }
    func testTypedNewGapDoesNotRewriteLegacyGapPayloads() throws {
        let directory = try folder(), store = try DurableStore(directory: directory), at = Date()
        let legacyID = try store.gap(deviceID:"p",start:at,end:at.addingTimeInterval(1),reason:"Disconnected or paused")
        let legacy = try Data(contentsOf: store.file(legacyID,extension:"json"))
        let newID = try store.gap(deviceID:"p",start:at,end:at.addingTimeInterval(1),reason:"Packet loss",cause:"packet_loss")
        let typed = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: store.file(newID,extension:"json"))) as? [String:Any])
        XCTAssertEqual(typed["cause"] as? String,"packet_loss")
        let reopened = try DurableStore(directory:directory)
        XCTAssertEqual(try Data(contentsOf: reopened.file(legacyID,extension:"json")),legacy)
        XCTAssertNil(reopened.items.first(where:{$0.id == legacyID})?.cause)
    }
}

extension DurableStoreTests {
    func testCaptureStopObservationHasBoundedMetadataReserveWithoutEvictingAudio() throws {
        let directory = try folder(), store = try DurableStore(directory:directory)
        let at = Date(timeIntervalSince1970:1000)
        try store.begin(deviceID:"p",codec:0,clock:CaptureClock(wall:at,uptime:0))
        try store.append(frame:Data(repeating:1,count:640),decoded:Data(repeating:1,count:640)); try store.finish()
        let audio = try XCTUnwrap(store.items.first)
        store.maximumBytes = store.storageBytes + 1
        XCTAssertThrowsError(try store.gap(deviceID:"p",start:at,end:at.addingTimeInterval(1),reason:"Full"))
        let id = try store.captureState(phoneID:UUID(),at:at,state:"blocked",cause:"storage")
        XCTAssertTrue(store.pending.contains(where:{$0.id == id}))
        XCTAssertTrue(FileManager.default.fileExists(atPath:store.file(audio.id,extension:"wav").path))
        XCTAssertFalse(try XCTUnwrap(store.items.first(where:{$0.id == audio.id})).acknowledged)
        XCTAssertLessThan(store.storageBytes,store.maximumBytes + 65_536)
    }
}

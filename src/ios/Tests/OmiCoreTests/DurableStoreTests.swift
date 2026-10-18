import XCTest
@testable import OmiCore

final class DurableStoreTests: XCTestCase {
    private func folder() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }
    func testAudioSyncIsBatchedToOneSecondAndFinishesPartialTail() throws {
        var synchronizations = 0
        let store = try DurableStore(directory: folder(), synchronizeSource: {
            synchronizations += 1; try $0.synchronize()
        })
        try store.begin(deviceID: "p", codec: 0, clock: CaptureClock())
        let frame = Data(repeating: 7, count: 640) // 20 ms of 16 kHz mono PCM.
        for _ in 0..<49 { try store.append(frame: frame, decoded: frame) }
        XCTAssertEqual(synchronizations, 0)
        try store.append(frame: frame, decoded: frame)
        XCTAssertEqual(synchronizations, 1)
        for _ in 0..<5 { try store.append(frame: frame, decoded: frame) }
        XCTAssertEqual(synchronizations, 1)
        try store.finish()
        XCTAssertEqual(synchronizations, 2)
        let item = try XCTUnwrap(store.pending.first)
        XCTAssertEqual(try Data(contentsOf: store.file(item.id, extension: "wav")).count, 55 * 640 + 44)

        // A new segment starts with a fresh sync budget.
        try store.begin(deviceID: "p", codec: 0, clock: CaptureClock())
        try store.append(frame: frame, decoded: frame)
        XCTAssertEqual(synchronizations, 2)
        try store.finish()
        XCTAssertEqual(synchronizations, 3)
    }
    func testFailedBatchedSyncKeepsAudioForFinalizationRetry() throws {
        var attempts = 0
        let store = try DurableStore(directory: folder(), synchronizeSource: {
            attempts += 1
            if attempts == 1 { throw CocoaError(.fileWriteUnknown) }
            try $0.synchronize()
        })
        try store.begin(deviceID: "p", codec: 0, clock: CaptureClock())
        let frame = Data(repeating: 7, count: 640)
        for _ in 0..<49 { try store.append(frame: frame, decoded: frame) }
        XCTAssertThrowsError(try store.append(frame: frame, decoded: frame))
        XCTAssertTrue(store.isRecording)
        XCTAssertEqual(store.segmentSeconds, 1)
        try store.finish()
        XCTAssertEqual(attempts, 2)
        let item = try XCTUnwrap(store.pending.first)
        XCTAssertEqual(try Data(contentsOf: store.file(item.id, extension: "wav")).dropFirst(44), Data(repeating: 7, count: 32_000))
    }
    func testProfileOwnershipSurvivesSwitchRetryAndRestart() throws {
        let directory = try folder(), store = try DurableStore(directory: directory)
        let captured = Date(timeIntervalSince1970: 1000)
        try store.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: captured))
        try store.append(frame: Data(count: 640), decoded: Data(count: 640))
        try store.finish()
        let audio = try XCTUnwrap(store.pending.first)
        store.profileID = "second"
        let eventID = try store.event(deviceID: "pendant", at: captured, action: .mark)
        let gapID = try store.gap(deviceID: "pendant", start: captured, end: captured.addingTimeInterval(1), reason: "BLE loss")
        let stateID = try store.captureState(phoneID: UUID(), at: captured, state: "paused", cause: "user_pause")
        try store.failed(audio.id, message: "offline")
        let restored = try DurableStore(directory: directory)
        XCTAssertEqual(restored.items.first { $0.id == audio.id }?.resolvedProfileID, "default")
        for id in [eventID, gapID, stateID] {
            XCTAssertEqual(restored.items.first { $0.id == id }?.resolvedProfileID, "second")
        }
        XCTAssertEqual(restored.pending.count, 4)
        restored.profileID = "second"
        XCTAssertThrowsError(try restored.rewind(minutes: 1, end: captured.addingTimeInterval(60)))
        restored.profileID = "default"
        let rewind = try restored.rewind(minutes: 1, end: captured.addingTimeInterval(60))
        try FileManager.default.removeItem(at: rewind.url)
    }
    func testLegacyOutboxWithoutProfileStaysInDefault() throws {
        let directory = try folder(), store = try DurableStore(directory: directory)
        let id = try store.event(deviceID: "pendant", at: Date(), action: .mark)
        let index = directory.appendingPathComponent("index.json")
        var records = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: index)) as? [[String: Any]])
        records[0].removeValue(forKey: "profileID")
        try JSONSerialization.data(withJSONObject: records).write(to: index)
        let restored = try DurableStore(directory: directory)
        restored.profileID = "second"
        XCTAssertEqual(restored.pending.first?.id, id)
        XCTAssertEqual(restored.pending.first?.resolvedProfileID, "default")
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

extension DurableStoreTests {
    func testQueuedMeetingIntentsKeepTheirTargetAfterRestart() throws {
        let directory = try folder(), store = try DurableStore(directory: directory)
        let startID = try store.event(deviceID: "pendant", at: Date(), action: .meetingToggle, meetingIntent: "start")
        let endID = try store.event(deviceID: "pendant", at: Date().addingTimeInterval(90), action: .meetingToggle, meetingIntent: "end", meetingID: startID)
        let restored = try DurableStore(directory: directory)
        let start = try XCTUnwrap(restored.items.first { $0.id == startID })
        let end = try XCTUnwrap(restored.items.first { $0.id == endID })
        XCTAssertEqual(start.meetingIntent, "start")
        XCTAssertNil(start.meetingID)
        XCTAssertEqual(end.meetingIntent, "end")
        XCTAssertEqual(end.meetingID, startID)
        let request = try JSONSerialization.jsonObject(with: Data(contentsOf: restored.file(endID, extension: "json"))) as? [String: Any]
        XCTAssertEqual(request?["meeting_intent"] as? String, "end")
        XCTAssertEqual(request?["meeting_id"] as? String, startID.uuidString)
    }
}


extension DurableStoreTests {
    func testFlagQueuedDuringMeetingDoesNotSendMeetingTarget() throws {
        let store = try DurableStore(directory: folder())
        let activeMeetingID = try store.event(deviceID: "pendant", at: Date(), action: .meetingToggle, meetingIntent: "start")
        let flagID = try store.event(deviceID: "pendant", at: Date(), action: .mark, meetingID: activeMeetingID)
        let request = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: store.file(flagID, extension: "json"))) as? [String: Any])
        XCTAssertEqual(request["action"] as? String, "mark")
        XCTAssertNil(request["meeting_intent"])
        XCTAssertNil(request["meeting_id"])
        let restored = try DurableStore(directory: store.directory)
        let flag = try XCTUnwrap(restored.items.first { $0.id == flagID })
        XCTAssertNil(flag.meetingIntent)
        XCTAssertNil(flag.meetingID)
    }
}


extension DurableStoreTests {
    func testLegacyFlagCooldownDoesNotOverrideFixedOneMinuteWait() {
        let at = Date(timeIntervalSince1970: 1000)
        var oldFlag = OutboxItem(id: UUID(), kind: .event, deviceID: "pendant", capturedAt: at)
        oldFlag.action = .mark; oldFlag.markerCooldownSeconds = 3600
        XCTAssertEqual(oldFlag.flagAvailableAgainAt, at.addingTimeInterval(60))
        oldFlag.markerCooldownSeconds = 0
        XCTAssertEqual(oldFlag.flagAvailableAgainAt, at.addingTimeInterval(60))
        oldFlag.action = .meetingToggle
        XCTAssertNil(oldFlag.flagAvailableAgainAt)
    }
}

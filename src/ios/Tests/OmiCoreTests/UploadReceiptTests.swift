import XCTest
@testable import OmiCore

final class UploadReceiptTests: XCTestCase {
    private func data(_ value: [String: Any]) throws -> Data { try JSONSerialization.data(withJSONObject: value) }
    func testAudioReceiptRequiresExactOriginalIdentityAndCaptureBounds() throws {
        let start = Date(timeIntervalSince1970: 1000.125)
        var item = OutboxItem(id: UUID(), kind: .audio, deviceID: "pendant", capturedAt: start)
        item.endedAt = start.addingTimeInterval(30)
        var receipt: [String: Any] = ["id": item.id.uuidString, "device_id": item.deviceID,
            "started_at": DurableStore.stamp(start), "ended_at": DurableStore.stamp(item.endedAt!), "state": "queued"]
        XCTAssertNoThrow(try UploadReceipt.validate(data(receipt), for: item))
        receipt["id"] = UUID().uuidString
        XCTAssertThrowsError(try UploadReceipt.validate(data(receipt), for: item))
        receipt["id"] = item.id.uuidString; receipt["started_at"] = DurableStore.stamp(start.addingTimeInterval(1))
        XCTAssertThrowsError(try UploadReceipt.validate(data(receipt), for: item))
    }
    func testProxyJSONAndHTMLNeverAcknowledgeAudio() throws {
        let item = OutboxItem(id: UUID(), kind: .audio, deviceID: "pendant", capturedAt: Date())
        XCTAssertThrowsError(try UploadReceipt.validate(Data("{}".utf8), for: item))
        XCTAssertThrowsError(try UploadReceipt.validate(Data("<html>success</html>".utf8), for: item))
    }
    func testEventReceiptRequiresOriginalIdentityEvenIfHintWasSuppressed() throws {
        var item = OutboxItem(id: UUID(), kind: .event, deviceID: "pendant", capturedAt: Date())
        item.action = .mark; item.meetingMaxMinutes = 45; item.meetingMinSeconds = 90; item.markerCooldownSeconds = 120
        var receipt: [String: Any] = ["markers": [], "meetings": [], "active_meeting": NSNull(),
            "event_id": item.id.uuidString, "device_id": item.deviceID, "occurred_at": DurableStore.stamp(item.capturedAt),
            "action": "mark", "meeting_max_minutes": 45, "meeting_min_seconds": 90, "marker_cooldown_seconds": 120]
        XCTAssertNoThrow(try UploadReceipt.validate(data(receipt), for: item))
        XCTAssertThrowsError(try UploadReceipt.validate(data(["markers": [], "meetings": [], "active_meeting": NSNull()]), for: item))
        XCTAssertThrowsError(try UploadReceipt.validate(data(["ok": true]), for: item))
        receipt["event_id"] = UUID().uuidString
        XCTAssertThrowsError(try UploadReceipt.validate(data(receipt), for: item))
        receipt["event_id"] = item.id.uuidString; receipt["marker_cooldown_seconds"] = 60
        XCTAssertThrowsError(try UploadReceipt.validate(data(receipt), for: item))
    }
    func testGapReceiptRequiresOriginalIDDeviceAndTimes() throws {
        let start = Date(timeIntervalSince1970: 1000)
        var item = OutboxItem(id: UUID(), kind: .gap, deviceID: "pendant", capturedAt: start)
        item.endedAt = start.addingTimeInterval(2)
        var receipt: [String: Any] = ["gap_id": item.id.uuidString, "device_id": "pendant", "started_at": DurableStore.stamp(start), "ended_at": DurableStore.stamp(item.endedAt!)]
        XCTAssertNoThrow(try UploadReceipt.validate(data(receipt), for: item))
        receipt["device_id"] = "other"
        XCTAssertThrowsError(try UploadReceipt.validate(data(receipt), for: item))
    }
    func testRetentionExpiredReceiptStopsRetryOnlyForOriginalRecording() throws {
        let start = Date(timeIntervalSince1970: 1000)
        var item = OutboxItem(id: UUID(), kind: .audio, deviceID: "p", capturedAt: start)
        item.endedAt = start.addingTimeInterval(30)
        var receipt: [String: Any] = ["id": item.id.uuidString, "device_id": "p", "started_at": DurableStore.stamp(start),
            "ended_at": DurableStore.stamp(item.endedAt!), "state": "expired", "audio_available": false]
        XCTAssertNoThrow(try UploadReceipt.validate(data(receipt), for: item))
        receipt["id"] = UUID().uuidString
        XCTAssertThrowsError(try UploadReceipt.validate(data(receipt), for: item))
    }
}

import Foundation

public enum UploadReceipt {
    /// A proxy's arbitrary 200 JSON response must never release original audio for expiry.
    public static func validate(_ data: Data, for item: OutboxItem) throws {
        guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw CaptureError.invalidReceipt }
        func matches(_ key: String, _ id: UUID) -> Bool { (value[key] as? String).flatMap(UUID.init(uuidString:)) == id }
        func date(_ key: String) -> Date? {
            guard let raw = value[key] as? String else { return nil }
            let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            return formatter.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
        }
        switch item.kind {
        case .audio:
            guard matches("id", item.id), value["device_id"] as? String == item.deviceID,
                  let started = date("started_at"), abs(started.timeIntervalSince(item.capturedAt)) < 0.002,
                  let ended = date("ended_at"), let expectedEnd = item.endedAt,
                  abs(ended.timeIntervalSince(expectedEnd)) < 0.002,
                  let state = value["state"] as? String, ["queued", "transcribing", "done", "failed", "expired", "blocked"].contains(state) else { throw CaptureError.invalidReceipt }
        case .captureState:
            guard matches("event_id", item.id), (value["phone_id"] as? String).flatMap(UUID.init(uuidString:)) == UUID(uuidString: item.deviceID),
                  let occurred = date("occurred_at"), abs(occurred.timeIntervalSince(item.capturedAt)) < 0.002,
                  value["state"] as? String == item.captureState,
                  value["cause"] as? String == item.cause else { throw CaptureError.invalidReceipt }
        case .gap:
            guard matches("gap_id", item.id), value["device_id"] as? String == item.deviceID,
                  let started = date("started_at"), abs(started.timeIntervalSince(item.capturedAt)) < 0.002,
                  let ended = date("ended_at"), let expectedEnd = item.endedAt,
                  abs(ended.timeIntervalSince(expectedEnd)) < 0.002,
                  item.cause == nil || value["cause"] as? String == item.cause else { throw CaptureError.invalidReceipt }
        case .event:
            // Suppressed hints still need an individual durable receipt. A valid timeline
            // could be a cached response to a different event and cannot acknowledge this one.
            guard matches("event_id", item.id), value["device_id"] as? String == item.deviceID,
                  let occurred = date("occurred_at"), abs(occurred.timeIntervalSince(item.capturedAt)) < 0.002,
                  value["action"] as? String == item.action?.rawValue,
                  item.meetingMaxMinutes == nil || value["meeting_max_minutes"] as? Int == item.meetingMaxMinutes,
                  item.meetingMinSeconds == nil || value["meeting_min_seconds"] as? Int == item.meetingMinSeconds,
                  item.markerCooldownSeconds == nil || value["marker_cooldown_seconds"] as? Int == item.markerCooldownSeconds else { throw CaptureError.invalidReceipt }
        }
    }
}

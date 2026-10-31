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
        case .generation:
            guard matches("id", item.id), value["kind"] as? String == "wearable_action",
                  let parameters = value["parameters"] as? [String: Any],
                  parameters["action"] as? String == item.wearableAction?.rawValue,
                  parameters["duration_seconds"] as? Int == item.durationSeconds,
                  parameters["device_id"] as? String == item.deviceID,
                  let rawEnd = parameters["end"] as? String,
                  let end = ISO8601DateFormatter().date(from: rawEnd) ?? {
                      let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                      return formatter.date(from: rawEnd)
                  }(), abs(end.timeIntervalSince(item.capturedAt)) < 0.002,
                  item.language == nil || parameters["language"] as? String == item.language,
                  let state = value["state"] as? String, ["queued", "running", "completed", "failed"].contains(state) else { throw CaptureError.invalidReceipt }
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
                  item.meetingIntent == nil || value["meeting_intent"] as? String == item.meetingIntent,
                  item.meetingID == nil || (value["meeting_id"] as? String).flatMap(UUID.init(uuidString:)) == item.meetingID,
                  item.meetingMaxMinutes == nil || value["meeting_max_minutes"] as? Int == item.meetingMaxMinutes,
                  item.meetingMinSeconds == nil || value["meeting_min_seconds"] as? Int == item.meetingMinSeconds,
                  item.markerCooldownSeconds == nil || value["marker_cooldown_seconds"] as? Int == item.markerCooldownSeconds else { throw CaptureError.invalidReceipt }
        }
    }
}

/// The same receipt-checked handoff runs in foreground wake time and keeps durable fallback IDs.
public enum UserInitiatedUpload {
    @MainActor
    public static func drain(store: DurableStore, submissionID: UUID, destinationID: String,
                             send: (OutboxItem, URL) async throws -> Data,
                             acknowledged: (OutboxItem) -> Void = { _ in }) async throws -> Bool {
        while let item = store.nextSubmissionUpload(submissionID, destinationID: destinationID) {
            try Task.checkCancellation()
            let data = try await send(item, store.uploadFile(for: item))
            try UploadReceipt.validate(data, for: item)
            try store.acknowledge(item.id)
            acknowledged(item)
        }
        return store.items.contains { $0.id == submissionID && $0.destinationID == destinationID && $0.acknowledged }
    }
}

/// Notification scheduling may briefly hold a job, but an abandoned callback cannot block uploads.
public struct UserInitiatedUploadHolds {
    private var deadlines: [UUID: TimeInterval] = [:]
    public init() {}
    public mutating func hold(_ id: UUID, at uptime: TimeInterval) { deadlines[id] = uptime + 8 }
    public mutating func release(_ id: UUID) { deadlines.removeValue(forKey: id) }
    public mutating func active(at uptime: TimeInterval) -> Set<UUID> {
        deadlines = deadlines.filter { $0.value > uptime }
        return Set(deadlines.keys)
    }
}

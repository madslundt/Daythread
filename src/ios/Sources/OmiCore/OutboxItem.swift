import Foundation

public struct OutboxItem: Codable, Identifiable {
    public enum Kind: String, Codable { case audio, event, gap, captureState, generation }
    public let id: UUID
    public let kind: Kind
    public let deviceID: String
    public let capturedAt: Date
    public var profileID: String?
    public var destinationID: String?
    public var resolvedProfileID: String { profileID ?? "default" }
    public var endedAt: Date?
    public var location: CaptureLocation?
    public var sourceKind: String?
    public var sourceID: String?
    public var codec: Int?
    public var clock: CaptureClock?
    public var action: SemanticAction?
    public var wearableAction: WearableAction?
    public var durationSeconds: Int?
    public var language: String?
    public var audioDependencyIDs: [UUID]?
    public var awaitingAudioTailAt: Date?
    public var cutoffGapID: UUID?
    public var userInitiated: Bool?
    public var prioritizesUpload: Bool { kind == .generation || kind == .event || userInitiated == true }
    public var meetingIntent: String?
    public var meetingID: UUID?
    public var meetingMaxMinutes: Int?
    public var meetingMinSeconds: Int?
    public var markerCooldownSeconds: Int?
    /// Archived cooldowns remain receipt metadata; current flag behavior is fixed.
    public var flagAvailableAgainAt: Date? {
        action == .mark ? capturedAt.addingTimeInterval(60) : nil
    }
    public var reason: String?
    public var cause: String?
    public var captureState: String?
    public var ready = false
    public var acknowledged = false
    public var attempts = 0
    public var retryAt = Date.distantPast
    public var error: String?
}

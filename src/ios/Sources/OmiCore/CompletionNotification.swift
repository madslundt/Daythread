import Foundation

public struct CompletionNotification: Codable, Equatable, Identifiable, Sendable {
    public let id: UUID
    public let sequence: Int
    public let kind: String
    public let title: String
    public let body: String
    public let target: Target
    public let createdAt: String

    public struct Target: Codable, Equatable, Sendable {
        public let jobID: UUID?
        public let day: String?
        public let weekStart: String?
        public let versionID: String?
        enum CodingKeys: String, CodingKey {
            case jobID = "job_id", day, weekStart = "week_start", versionID = "version_id"
        }
    }
    enum CodingKeys: String, CodingKey {
        case id, sequence, kind, title, body, target, createdAt = "created_at"
    }
}

/// The feed cursor advances only after a complete page is consumed. An out-of-order
/// push must never skip earlier feed events; both transports share the same IDs.
public struct CompletionInbox: Codable, Sendable {
    public private(set) var cursor = 0
    public private(set) var items: [CompletionNotification] = []
    private var seen: [UUID] = []
    // Persist transport metadata only. Preview text is never restored from a
    // cache after archive retention or source deletion may have removed it.
    enum CodingKeys: String, CodingKey { case cursor, seen }
    public init() {}

    @discardableResult
    public mutating func receive(_ event: CompletionNotification) -> Bool {
        let isNew = !seen.contains(event.id) && event.sequence > cursor
        if !seen.contains(event.id) {
            seen.append(event.id)
            if seen.count > 1000 { seen.removeFirst(seen.count - 1000) }
        }
        if !items.contains(where: { $0.id == event.id }) { items.insert(event, at: 0) }
        items.sort { $0.sequence > $1.sequence }
        if items.count > 100 { items.removeLast(items.count - 100) }
        return isNew
    }

    public mutating func advanceCursor(to value: Int) { cursor = max(cursor, value) }
    public mutating func clearPreviews() { items = [] }

    public static func acceptsPush(profile: String, installation: String, currentProfile: String, currentInstallation: String) -> Bool {
        !currentProfile.isEmpty && !currentInstallation.isEmpty && profile == currentProfile && installation == currentInstallation
    }
}

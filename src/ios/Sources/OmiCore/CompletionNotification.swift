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

/// Immediate local acknowledgements are distinct from saved backend completion results.
public enum ClickNotification {
    public static let preferenceKey = "clickNotificationsEnabled"
    public static let markerKey = "daythread_local_notice"
    public static let marker = "pendant_click"
    public static func enabled(in defaults: UserDefaults = .standard) -> Bool {
        defaults.object(forKey: preferenceKey) == nil || defaults.bool(forKey: preferenceKey)
    }
    public static func identifier(jobID: UUID? = nil) -> String {
        if let jobID { return "wearable-action:" + jobID.uuidString.lowercased() }
        return "pendant-click:" + UUID().uuidString.lowercased()
    }
    public static func isAcknowledgement(identifier: String, marker: String?, isRemote: Bool) -> Bool {
        !isRemote && marker == Self.marker &&
            (identifier.hasPrefix("pendant-click:") || identifier.hasPrefix("wearable-action:"))
    }
    public static func title(for click: WearableClick) -> String {
        click == .single ? AppLocalization.source("Single click registered") : AppLocalization.source("Double click registered")
    }
}

public enum ClickActionFeedback: Equatable {
    case queued(WearableAction, seconds: Int)
    case marked, alreadyMarked(seconds: Int)
    case meetingStarted(provisionalSeconds: Int), meetingEnded, shortMeetingIgnored
    case capturePaused, captureResumed, noAction, failed(String)

    public var message: String {
        switch self {
        case .queued(.summarize, let seconds): AppLocalization.source("Summary queued for the last \(seconds) seconds.")
        case .queued(.answer, let seconds): AppLocalization.source("Answer queued for the last \(seconds) seconds.")
        case .queued(.translate, let seconds): AppLocalization.source("Translation queued for the last \(seconds) seconds.")
        case .queued: AppLocalization.source("Recent audio request queued.")
        case .marked: AppLocalization.source("Marked nearby speech as important")
        case .alreadyMarked(let seconds): AppLocalization.source("Already marked nearby. Another flag is available in \(seconds) seconds.")
        case .meetingStarted(let seconds): AppLocalization.source("Meeting hint started · provisional for \(seconds) seconds")
        case .meetingEnded: AppLocalization.source("Meeting hint ended")
        case .shortMeetingIgnored: AppLocalization.source("Short meeting ignored. Audio is kept.")
        case .capturePaused: AppLocalization.source("Recording paused on this iPhone.")
        case .captureResumed: AppLocalization.source("Recording resumed on this iPhone.")
        case .noAction: AppLocalization.source("No action assigned.")
        case .failed(let reason): AppLocalization.source("Could not complete the click action: \(reason)")
        }
    }
}

/// A physical click owns a short execution window until both asynchronous handoffs finish.
public struct ClickExecutionState {
    public enum Part: Hashable { case confirmation, haptic, submission }
    private var pending: Set<Part>
    public private(set) var finished = false
    public init(pending: Set<Part> = [.confirmation, .haptic]) { self.pending = pending }
    public mutating func complete(_ part: Part) -> Bool {
        guard !finished, pending.remove(part) != nil, pending.isEmpty else { return false }
        finished = true
        return true
    }
    public mutating func expire() -> Bool {
        guard !finished else { return false }
        finished = true; pending.removeAll()
        return true
    }
}

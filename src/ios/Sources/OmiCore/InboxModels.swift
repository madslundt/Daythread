import Foundation

public struct InboxDeadline: Codable, Equatable, Sendable {
    public let precision: String
    public let day: String?
    public let instant: String?
    public let timezone: String?
    public let phrase: String?
    public let basis: String?
    public var label: String {
        if precision == "date" { return day ?? AppLocalization.text("Date unknown") }
        if precision == "datetime" { return "\(instant ?? "") (\(timezone ?? ""))" }
        return phrase.map { AppLocalization.text("Unresolved: \($0)") } ?? AppLocalization.text("Deadline unknown")
    }
}

public struct InboxFields: Codable, Equatable, Sendable {
    public let kind: String
    public let title: String
    public let owner: String?
    public let deadline: InboxDeadline?
}

public struct InboxEvidence: Codable, Equatable, Sendable {
    public let source_id: String
    public let revision: String
    public let start: Int
    public let end: Int
    public let quote: String
    public let started_at: String
    public let audio_available: Bool
    public let current: Bool
}

public struct InboxEvent: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let kind: String
    public let created_at: String
}

public struct InboxEvidenceSpan: Codable, Equatable, Sendable {
    public let source_id: String
    public let start: Int
    public let end: Int
    public let current: Bool
}

public struct InboxTopicPassage: Equatable, Sendable {
    public let sourceID: String
    public let start: Int
    public let end: Int
    public init(sourceID: String, start: Int, end: Int) {
        self.sourceID = sourceID; self.start = start; self.end = end
    }
    public func contains(sourceID: String, start: Int, end: Int) -> Bool {
        self.sourceID == sourceID && start >= self.start && end <= self.end && end > start
    }
    public func containsCurrent(sourceID: String, sourceVersion: String, currentVersions: [String: String], start: Int, end: Int) -> Bool {
        !sourceVersion.isEmpty && currentVersions[sourceID] == sourceVersion && contains(sourceID: sourceID, start: start, end: end)
    }
}

public enum InboxIntent: String, CaseIterable, Sendable {
    case todo, questions, schedule, reflection, information
    public init?(kind: String) {
        switch kind {
        case "commitment", "deadline": self = .todo
        case "question": self = .questions
        case "event", "reminder": self = .schedule
        case "reflection": self = .reflection
        case "note": self = .information
        default: return nil
        }
    }
}

public struct InboxItem: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let kind: String
    public let title: String
    public let owner: String?
    public let deadline: InboxDeadline?
    public let status: String
    public let health: String
    public let version: Int
    public let reviewed_at: String?
    public let duplicate_of: String?
    public let overdue: Bool?
    public let proposal: InboxFields
    public let evidence: [InboxEvidence]?
    public let history: [InboxEvent]?
    /// List responses include all occurrence sources; older servers may omit this field.
    public let source_ids: [String]?
    public let evidence_spans: [InboxEvidenceSpan]?
    public let user_note: String?
    public let user_note_provenance: String?

    public static let kinds = ["commitment", "deadline", "question", "reminder", "event", "reflection", "note"]
    public var intent: InboxIntent? { InboxIntent(kind: kind) }
    public var isReviewOnly: Bool { kind == "reflection" || kind == "note" }
    public var appearsInDayOverview: Bool {
        health != "withdrawn" && (status == "pending" || (status == "completed" && isReviewOnly))
    }
    public var canSuggestFollowUps: Bool { status == "pending" && health == "current" && Self.kinds.contains(kind) && !isReviewOnly }

    public func matchesSources(_ ids: Set<String>) -> Bool {
        ids.isEmpty || !ids.isDisjoint(with: Set(source_ids ?? evidence?.map(\.source_id) ?? []))
    }

    public func matchesTopic(sources: Set<String>, passages: [InboxTopicPassage]) -> Bool {
        guard !sources.isEmpty else { return false }
        // A recording can contain unrelated topics. Use current anchor containment when
        // a topic passage is known; source membership alone cannot establish a match.
        let spans = evidence_spans ?? evidence?.map {
            InboxEvidenceSpan(source_id: $0.source_id, start: $0.start, end: $0.end, current: $0.current)
        } ?? []
        return spans.contains { span in
            guard span.current, sources.contains(span.source_id) else { return false }
            let ranges = passages.filter { $0.sourceID == span.source_id }
            return ranges.contains { $0.contains(sourceID: span.source_id, start: span.start, end: span.end) }
        }
    }
}

public struct InboxJob: Codable, Identifiable, Sendable {
    public let id: String
    public let state: String
    public let attempts: Int
    public let error: String?
    public let recording_start: String?
}

public struct InboxCoverage: Codable, Sendable {
    public let jobs: [String: Int]
    public let unscanned_sources: Int
}

public struct InboxPage: Codable, Sendable {
    public let items: [InboxItem]
    public let total: Int
    public let pending: Int
    public let coverage: InboxCoverage
    public let recent_jobs: [InboxJob]
}

public struct InboxFollowUps: Decodable {
    public let item_id: String
    public let item_version: Int
    public let timezone: String
    public let suggestions: [DecisionFollowUp]
}

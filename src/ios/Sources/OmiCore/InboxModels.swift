import Foundation

public struct InboxDeadline: Codable, Equatable, Sendable {
    public let precision: String
    public let day: String?
    public let instant: String?
    public let timezone: String?
    public let phrase: String?
    public let basis: String?
    public var label: String {
        if precision == "date" { return day ?? "Date unknown" }
        if precision == "datetime" { return "\(instant ?? "") (\(timezone ?? ""))" }
        return phrase.map { "Unresolved: \($0)" } ?? "Deadline unknown"
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

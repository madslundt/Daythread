import Foundation

public struct DayQuestion: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let jobID: String
    public let question: String
    public let answer: String?
    public let askedAt: String
    public let timePrecision: String?

    enum CodingKeys: String, CodingKey {
        case id, question, answer
        case jobID = "job_id", askedAt = "asked_at", timePrecision = "time_precision"
    }

    public var askedDate: Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: askedAt) ?? ISO8601DateFormatter().date(from: askedAt)
    }

    public func belongsToClick(jobID: String) -> Bool {
        !jobID.isEmpty && self.jobID.caseInsensitiveCompare(jobID) == .orderedSame
    }
}

public struct DayQuestions: Codable, Equatable, Sendable {
    public let day: String
    public let timezone: String?
    public let selectedJobID: String?
    public let selectedQuestionIDs: [String]?
    public let questions: [DayQuestion]
    public let groups: [DayQuestionGroup]?
    public let processing: Bool
    public let error: String?

    enum CodingKeys: String, CodingKey {
        case day, timezone, questions, groups, processing, error
        case selectedJobID = "selected_job_id", selectedQuestionIDs = "selected_question_ids"
    }

    public var highlightedIDs: Set<String> {
        if let ids = selectedQuestionIDs, !ids.isEmpty { return Set(ids) }
        return Set(questions.filter { $0.belongsToClick(jobID: selectedJobID ?? "") }.map(\.id))
    }

    public var firstHighlightedID: String? {
        let ids = highlightedIDs
        return questions.first { ids.contains($0.id) }?.id
    }

    /// New servers retain every occurrence in its click. Older flat responses
    /// can still be grouped by job, preserving their existing newest-first order.
    public var clickGroups: [DayQuestionGroup] {
        if let groups { return groups.filter { !$0.questions.isEmpty } }
        var order: [String] = []
        var members: [String: [DayQuestion]] = [:]
        for question in questions {
            let key = question.jobID.lowercased()
            if members[key] == nil { order.append(key) }
            members[key, default: []].append(question)
        }
        return order.map { id in
            let questions = members[id] ?? []
            return DayQuestionGroup(jobID: id, end: questions.first?.askedAt ?? "",
                                    questionCount: questions.count, questions: questions)
        }
    }

    public func isSelected(_ group: DayQuestionGroup) -> Bool {
        guard let selectedJobID, !selectedJobID.isEmpty else { return false }
        return selectedJobID.caseInsensitiveCompare(group.jobID) == .orderedSame
    }

    public var firstHighlightedRowID: String? {
        clickGroups.first(where: isSelected)?.rows.first?.id
    }
    public var firstHighlightedGroupID: String? {
        clickGroups.first(where: isSelected)?.headerID
    }
}

public struct DayQuestionGroup: Codable, Equatable, Identifiable, Sendable {
    public let jobID: String
    public let end: String
    public let questionCount: Int
    public let questions: [DayQuestion]
    public var id: String { jobID.lowercased() }
    public var headerID: String { "question-group:" + id }

    enum CodingKeys: String, CodingKey {
        case jobID = "job_id", end, questionCount = "question_count", questions
    }

    public var capturedDate: Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: end) ?? ISO8601DateFormatter().date(from: end)
    }

    public struct Row: Identifiable {
        public let id: String
        public let question: DayQuestion
    }

    /// Overlapping clicks may contain the same question ID. List/scroll identity
    /// belongs to its occurrence in this click, rather than the question alone.
    public var rows: [Row] {
        questions.map { Row(id: id + ":" + $0.id, question: $0) }
    }
}

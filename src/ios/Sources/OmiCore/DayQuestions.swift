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
    public let selectedJobID: String
    public let selectedQuestionIDs: [String]?
    public let questions: [DayQuestion]
    public let processing: Bool
    public let error: String?

    enum CodingKeys: String, CodingKey {
        case day, timezone, questions, processing, error
        case selectedJobID = "selected_job_id", selectedQuestionIDs = "selected_question_ids"
    }

    public var highlightedIDs: Set<String> {
        if let ids = selectedQuestionIDs, !ids.isEmpty { return Set(ids) }
        return Set(questions.filter { $0.belongsToClick(jobID: selectedJobID) }.map(\.id))
    }

    public var firstHighlightedID: String? {
        let ids = highlightedIDs
        return questions.first { ids.contains($0.id) }?.id
    }
}

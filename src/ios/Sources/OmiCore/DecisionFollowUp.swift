import Foundation

public struct DecisionFollowUp: Codable, Equatable {
    public enum Kind: String, Codable { case reminder, event }
    public let kind: Kind
    public let title: String
    public let notes: String
    public let reason: String
    public let start_at: String?
    public let end_at: String?
    public let due_at: String?
    public let all_day: Bool
    public let location: String
    public enum LocationTrigger: String, Codable { case arrive, leave }
    public var location_trigger: LocationTrigger? = nil

    public var hasLocationTrigger: Bool {
        kind == .reminder && location_trigger != nil && !location.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    public func actionDate(timeZone: TimeZone) -> Date? {
        Self.date(kind == .event ? start_at : due_at, allDay: all_day, timeZone: timeZone)
    }

    // Backend timestamps must include an offset; accepting local timestamps would silently
    // move a suggestion when the phone and archive use different time zones.
    public static func date(_ value: String?, allDay: Bool = false, timeZone: TimeZone = .current) -> Date? {
        guard let value else { return nil }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let instant = fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value) else { return nil }
        guard allDay else { return instant }
        // An all-day date is a calendar label, rather than an instant. Keep the source
        // year/month/day even when the model used a different offset from the archive.
        let parts = value.prefix(10).split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = timeZone
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }

    public func calendarComposerDates(timeZone: TimeZone) -> (start: Date, end: Date)? {
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = timeZone
        guard kind == .event, let start = actionDate(timeZone: timeZone) else { return nil }
        let proposedEnd = Self.date(end_at, allDay: all_day, timeZone: timeZone)
        // An actual proposed start is required. Only a missing end gets a temporary
        // composer default; it never mutates the proposal or becomes a visible link.
        let defaultEnd = all_day ? calendar.date(byAdding: .day, value: 1, to: start)! : start.addingTimeInterval(3600)
        return (start, proposedEnd.flatMap { $0 > start ? $0 : nil } ?? defaultEnd)
    }
}

public struct DecisionFollowUps: Decodable {
    public let decision_id: String
    public let decision_version: Int
    public let timezone: String
    public let suggestions: [DecisionFollowUp]
}

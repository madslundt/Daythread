import Foundation

/// Device-local policy. Weekdays use ISO numbering (Monday = 1, Sunday = 7).
public struct RecordingSchedule: Codable, Equatable, Sendable {
    public var enabled = true
    public var scheduled = false
    public var manualOverride = false
    public var weekdays: Set<Int> = [1, 2, 3, 4, 5]
    public var startMinute = 9 * 60
    public var endMinute = 17 * 60
    public var source = "phone"
    public var manualSource = "phone"
    public var activeSource: String { manualOverride ? manualSource : source }

    public init() {}

    /// Phone hints must never borrow quiet evidence from a previously paired pendant.
    public func hintDeviceID(phoneID: UUID, pendantID: String) -> String {
        activeSource == "phone" ? "phone-" + phoneID.uuidString.lowercased() : pendantID
    }

    public func allows(_ date: Date, calendar: Calendar = .current) -> Bool {
        guard enabled else { return false }
        if manualOverride || !scheduled { return true }
        guard (0..<1440).contains(startMinute), (0..<1440).contains(endMinute), startMinute != endMinute else { return false }
        let day = (calendar.component(.weekday, from: date) + 5) % 7 + 1
        let minute = calendar.component(.hour, from: date) * 60 + calendar.component(.minute, from: date)
        if startMinute < endMinute { return weekdays.contains(day) && minute >= startMinute && minute < endMinute }
        return (weekdays.contains(day) && minute >= startMinute)
            || (weekdays.contains(day == 1 ? 7 : day - 1) && minute < endMinute)
    }

    /// The next policy change, so an idle app need not poll between recording windows.
    public func nextTransition(after date: Date, calendar: Calendar = .current) -> Date? {
        guard enabled, scheduled, !manualOverride, !weekdays.intersection(Set(1...7)).isEmpty,
              (0..<1440).contains(startMinute), (0..<1440).contains(endMinute), startMinute != endMinute,
              let horizon = calendar.date(byAdding: .day, value: 8, to: date) else { return nil }
        var candidates: Set<Date> = []
        for minute in [startMinute, endMinute] {
            for repetition in [Calendar.RepeatedTimePolicy.first, .last] {
                var cursor = date
                while let next = calendar.nextDate(after: cursor,
                    matching: DateComponents(hour: minute / 60, minute: minute % 60, second: 0),
                    matchingPolicy: .nextTime, repeatedTimePolicy: repetition), next <= horizon {
                    candidates.insert(next); cursor = next
                }
            }
        }
        // A backward clock change can itself move the wall time out of a window.
        if let transition = calendar.timeZone.nextDaylightSavingTimeTransition(after: date), transition <= horizon {
            candidates.insert(transition)
        }
        return candidates.sorted().first {
            allows($0.addingTimeInterval(-0.001), calendar: calendar) != allows($0, calendar: calendar)
        }
    }

    /// A recorder deadline also enforces the stop time while iOS suspends UI timers.
    public func recordingDeadline(_ date: Date, calendar: Calendar = .current) -> Date? {
        guard scheduled, !manualOverride, allows(date, calendar: calendar) else { return nil }
        return nextTransition(after: date, calendar: calendar)
    }
}

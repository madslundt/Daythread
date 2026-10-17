import Foundation

public enum DiaryPeriod: String, CaseIterable {
    case week, month, year
}

/// Civil diary dates use the backend's Gregorian calendar and home timezone.
public struct DiaryCalendar {
    public let calendar: Calendar

    public init(timeZone: TimeZone, locale: Locale = AppLocalization.locale) {
        var calendar = Calendar(identifier: .gregorian)
        calendar.locale = locale
        calendar.timeZone = timeZone
        calendar.firstWeekday = Calendar.current.firstWeekday
        if locale.language.languageCode?.identifier == "da" { calendar.firstWeekday = 2 }
        self.calendar = calendar
    }

    public func dayKey(_ date: Date) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year!, parts.month!, parts.day!)
    }

    public func monthKey(_ date: Date) -> String { String(dayKey(date).prefix(7)) }

    public func date(for day: String) -> Date? {
        let parts = day.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        let date = calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12))
        guard let date, dayKey(date) == day else { return nil }
        return date
    }

    public func monthStart(_ date: Date) -> Date {
        calendar.date(from: calendar.dateComponents([.year, .month], from: date))!
    }

    public func month(_ date: Date, offset: Int) -> Date {
        calendar.date(byAdding: .month, value: offset, to: monthStart(date))!
    }

    /// Inclusive civil dates for complete periods. Weeks always run Monday–Sunday.
    public func dateRange(for period: DiaryPeriod, containing date: Date, previous: Bool = false) -> ClosedRange<Date> {
        let noon = calendar.date(bySettingHour: 12, minute: 0, second: 0, of: date)!
        if period == .week {
            let daysSinceMonday = (calendar.component(.weekday, from: noon) + 5) % 7
            let start = calendar.date(byAdding: .day, value: -daysSinceMonday - (previous ? 7 : 0), to: noon)!
            return start...calendar.date(byAdding: .day, value: 6, to: start)!
        }
        let component: Calendar.Component = period == .month ? .month : .year
        let components: Set<Calendar.Component> = period == .month ? [.year, .month] : [.year]
        let first = calendar.date(from: calendar.dateComponents(components, from: noon))!
        let start = calendar.date(byAdding: component, value: previous ? -1 : 0, to: first)!
        let next = calendar.date(byAdding: component, value: 1, to: start)!
        let end = calendar.date(byAdding: .day, value: -1, to: next)!
        return calendar.date(bySettingHour: 12, minute: 0, second: 0, of: start)!...calendar.date(bySettingHour: 12, minute: 0, second: 0, of: end)!
    }

    /// Padding keeps the locale's weekday columns aligned without selectable outside days.
    public func cells(in month: Date) -> [Date?] {
        let start = monthStart(month)
        let days = calendar.range(of: .day, in: .month, for: start)!
        let leading = (calendar.component(.weekday, from: start) - calendar.firstWeekday + 7) % 7
        let count = ((leading + days.count + 6) / 7) * 7
        return (0..<count).map { index in
            guard index >= leading, index < leading + days.count else { return nil }
            return calendar.date(byAdding: .day, value: index - leading, to: start)
        }
    }

    public func label(_ date: Date, template: String) -> String {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = calendar.locale
        formatter.timeZone = calendar.timeZone
        formatter.setLocalizedDateFormatFromTemplate(template)
        return formatter.string(from: date)
    }

    public var weekdaySymbols: [String] {
        let symbols = calendar.veryShortStandaloneWeekdaySymbols
        let offset = calendar.firstWeekday - 1
        return Array(symbols[offset...] + symbols[..<offset])
    }
}

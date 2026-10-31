import Foundation

/// Question times stay relative for the first hour, then use the diary's clock.
public enum QuestionTimestamp {
    public enum Style: Equatable, Sendable {
        case relative, clock
    }

    public static func style(for date: Date, now: Date) -> Style {
        abs(now.timeIntervalSince(date)) < 3600 ? .relative : .clock
    }

    public static func label(for date: Date, now: Date = Date(), locale: Locale = AppLocalization.locale,
                             timeZone: TimeZone = .current) -> String {
        switch style(for: date, now: now) {
        case .relative:
            let formatter = RelativeDateTimeFormatter()
            formatter.locale = locale
            formatter.unitsStyle = .full
            formatter.dateTimeStyle = .named
            return formatter.localizedString(for: date, relativeTo: now)
        case .clock:
            let formatter = DateFormatter()
            formatter.locale = locale
            formatter.timeZone = timeZone
            formatter.dateStyle = .none
            formatter.timeStyle = .short
            return formatter.string(from: date)
        }
    }
}

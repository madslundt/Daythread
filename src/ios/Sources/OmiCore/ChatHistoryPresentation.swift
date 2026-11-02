import Foundation

public enum ChatHistoryPresentation {
    public static func title(_ title: String, firstQuestion: String = "", fallback: String = AppLocalization.text("Conversation")) -> String {
        let name = normalized(title)
        let generic = ["conversation", "new conversation", "chat", "new chat", fallback.lowercased()]
        if !name.isEmpty && !generic.contains(name.lowercased()) { return name }
        let question = normalized(firstQuestion)
        return question.isEmpty ? (name.isEmpty ? fallback : name) : question
    }

    public static func timestamp(_ date: Date, relativeTo now: Date = Date(), locale: Locale = .current,
                                 timeZone: TimeZone = .current) -> String {
        let age = now.timeIntervalSince(date)
        let relative = RelativeDateTimeFormatter()
        relative.locale = locale
        relative.unitsStyle = .full
        if abs(age) < 60 {
            relative.dateTimeStyle = .named
            return relative.localizedString(from: DateComponents(second: 0))
        }
        if age >= 0 && age < 24 * 60 * 60 {
            relative.dateTimeStyle = .numeric
            return relative.localizedString(for: date, relativeTo: now)
        }

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.timeZone = timeZone
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
           calendar.isDate(date, inSameDayAs: yesterday) {
            relative.dateTimeStyle = .named
            formatter.setLocalizedDateFormatFromTemplate("jm")
            return relative.localizedString(from: DateComponents(day: -1)) + " · " + formatter.string(from: date)
        }
        if age >= 0 && age < 7 * 24 * 60 * 60 {
            formatter.setLocalizedDateFormatFromTemplate("EEEjm")
        } else {
            let sameYear = calendar.component(.year, from: date) == calendar.component(.year, from: now)
            formatter.setLocalizedDateFormatFromTemplate(sameYear ? "dMMMjm" : "dMMMyyyyjm")
        }
        return formatter.string(from: date)
    }

    private static func normalized(_ value: String) -> String {
        String(value.split(whereSeparator: \.isWhitespace).joined(separator: " ").prefix(120))
    }
}

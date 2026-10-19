import SwiftUI
import OmiCore

/// The date control for Today. Expanding it keeps date selection in context with the day view.
struct DayCalendarPicker: View {
    @Environment(AppModel.self) private var model
    @Binding var selectedDate: Date?
    @State private var expanded = false
    @State private var month = Date()
    @State private var days: [String: CalendarDay] = [:]
    @State private var loading = false
    @State private var error: String?
    @State private var requestID = UUID()

    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var visibleDate: Date { selectedDate ?? model.now }
    private var calendarKey: String { model.archiveIdentity + model.archiveRevision + diary.monthKey(month) + diary.calendar.timeZone.identifier }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(spacing: 10) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    if !expanded { month = diary.monthStart(visibleDate) }
                    expanded.toggle()
                }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "calendar").foregroundStyle(Theme.accent)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(diary.label(visibleDate, template: "EEEE d MMMM")).font(.headline)
                        Text(selectedDate == nil ? L10n.text("Today · Tap to choose a date") : L10n.text("Tap to choose a date"))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("day.datePicker")

            if expanded {
                MonthCalendar(diary: diary, month: $month, day: visibleDate, today: model.now, days: days) { date in
                    selectedDate = diary.dayKey(date) == diary.dayKey(model.now) ? nil : date
                    withAnimation(.easeInOut(duration: 0.2)) { expanded = false }
                }
                HStack(spacing: 6) {
                    Circle().fill(Theme.accent).frame(width: 6, height: 6)
                    Text(L10n.text("Saved recap")).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if loading { ProgressView().accessibilityLabel(L10n.text("Loading saved recap dates")) }
                    if selectedDate != nil {
                        Button(L10n.text("Today")) { selectedDate = nil; month = diary.monthStart(model.now) }
                            .font(.caption.weight(.medium)).buttonStyle(.borderless).accessibilityIdentifier("day.goToToday")
                    }
                }
                ErrorNote(message: error)
                if error != nil { Button(L10n.text("Retry calendar")) { Task { await loadCalendar() } }.font(.caption) }
                Text(L10n.text("Dates in \(diary.calendar.timeZone.identifier)")).font(.caption2).foregroundStyle(.tertiary)
            }
        }
        .task(id: expanded ? calendarKey : "") {
            if expanded { await loadCalendar() }
        }
        .onChange(of: selectedDate) { _, value in
            guard let value else { return }
            month = diary.monthStart(value)
        }
        .onChange(of: model.serverTimeZoneIdentifier) { _, _ in
            month = diary.monthStart(visibleDate)
            days = [:]
        }
    }

    private func loadCalendar() async {
        let key = calendarKey
        let requestedMonth = diary.monthKey(month)
        let token = UUID()
        requestID = token
        loading = true
        error = nil
        defer { if requestID == token { loading = false } }
        do {
            let response = try await model.client().request("v1/calendar", query: ["month": requestedMonth])
            guard !Task.isCancelled, requestID == token, calendarKey == key else { return }
            guard response["month"].text == requestedMonth, case .array = response["days"] else {
                throw BackendFailure(message: L10n.text("The calendar response was invalid."))
            }
            days = [:]
            for value in response["days"].array {
                let entry = CalendarDay(value)
                if entry.day.hasPrefix(requestedMonth + "-"), diary.date(for: entry.day) != nil { days[entry.day] = entry }
            }
        } catch {
            if !Task.isCancelled, requestID == token, calendarKey == key { self.error = L10n.error(error) }
        }
    }
}

private struct CalendarDay {
    let metadata: JSONValue
    init(_ metadata: JSONValue) { self.metadata = metadata }
    var day: String { metadata["day"].text }
    var hasSummary: Bool { metadata["has_summary"] == .bool(true) }
}

private struct MonthCalendar: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let diary: DiaryCalendar
    @Binding var month: Date
    let day: Date
    let today: Date
    let days: [String: CalendarDay]
    let select: (Date) -> Void
    private let columns = Array(repeating: GridItem(.flexible(), spacing: 2), count: 7)
    private var todayKey: String { diary.dayKey(today) }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(spacing: 12) {
            HStack {
                Button { month = diary.month(month, offset: -1) } label: { Image(systemName: "chevron.left").frame(minWidth: 44, minHeight: 44) }
                    .accessibilityLabel(L10n.text("Previous month"))
                Spacer(minLength: 0)
                Text(diary.label(month, template: "MMMM yyyy")).font(.subheadline.weight(.medium))
                Spacer(minLength: 0)
                Button { month = diary.month(month, offset: 1) } label: { Image(systemName: "chevron.right").frame(minWidth: 44, minHeight: 44) }
                    .disabled(diary.monthKey(month) >= diary.monthKey(today)).accessibilityLabel(L10n.text("Next month"))
            }.buttonStyle(.borderless)
            if dynamicTypeSize >= .xxLarge {
                ForEach(diary.cells(in: month).compactMap { $0 }, id: \.self) { date in accessibleDay(date) }
            } else {
                LazyVGrid(columns: columns, spacing: 4) {
                    ForEach(Array(diary.weekdaySymbols.enumerated()), id: \.offset) { _, symbol in
                        Text(symbol).font(.caption.weight(.semibold)).foregroundStyle(.secondary).frame(maxWidth: .infinity).accessibilityHidden(true)
                    }
                    ForEach(Array(diary.cells(in: month).enumerated()), id: \.offset) { _, date in
                        if let date { cell(date) } else { Color.clear.frame(height: 44).accessibilityHidden(true) }
                    }
                }
            }
        }.padding(.vertical, 4)
    }

    private func cell(_ date: Date) -> some View {
        let key = diary.dayKey(date)
        let selected = key == diary.dayKey(day)
        let future = key > todayKey
        let summary = days[key]?.hasSummary == true
        return Button { select(date) } label: {
            VStack(spacing: 4) {
                Text("\(diary.calendar.component(.day, from: date))").font(.body.monospacedDigit()).fontWeight(key == todayKey ? .bold : .regular)
                Circle().fill(summary ? (selected ? Theme.onAccent : Theme.accent) : Color.clear).frame(width: 5, height: 5)
            }
            .frame(maxWidth: .infinity, minHeight: 44)
            .foregroundStyle(selected ? Theme.onAccent : future ? Color.secondary.opacity(0.45) : Color.primary)
            .background(selected ? Theme.accent : Color.clear, in: RoundedRectangle(cornerRadius: 10))
            .overlay { if key == todayKey && !selected { RoundedRectangle(cornerRadius: 10).stroke(Theme.accent, lineWidth: 1) } }
            .contentShape(Rectangle())
        }.buttonStyle(.plain).disabled(future)
            .accessibilityLabel(diary.label(date, template: "EEEE d MMMM yyyy") + (key == todayKey ? L10n.text(", today") : "") + (summary ? L10n.text(", saved summary") : ""))
            .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func accessibleDay(_ date: Date) -> some View {
        let key = diary.dayKey(date)
        return Button { select(date) } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(diary.label(date, template: "EEEE d MMMM"))
                if days[key]?.hasSummary == true { Label(L10n.text("Saved summary"), systemImage: "circle.fill").font(.caption) }
            }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).padding(8)
                .background(key == diary.dayKey(day) ? Theme.accent.opacity(0.12) : Color.clear, in: RoundedRectangle(cornerRadius: 10))
        }.buttonStyle(.plain).disabled(key > todayKey)
            .accessibilityAddTraits(key == diary.dayKey(day) ? .isSelected : [])
    }
}

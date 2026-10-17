import SwiftUI
import OmiCore

struct DiaryView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.goToToday) private var goToToday
    @State private var month = Date()
    @State private var selected = Date()
    @State private var entries: [JSONValue] = []
    @State private var days: [String: CalendarDay] = [:]
    @State private var error: String?
    @State private var calendarError: String?
    @State private var loading = false
    @State private var calendarLoading = false
    @State private var loadedCalendarKey = ""
    @State private var entryRequest = UUID()
    @State private var calendarRequest = UUID()
    @State private var path: [String] = []

    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var calendarKey: String { model.archiveIdentity + model.archiveRevision + diary.monthKey(month) + diary.calendar.timeZone.identifier }

    var body: some View {
        List {
            Section {
                EditorialTitle(eyebrow: "Your diary", title: "Look back. See more.").editorialRow()
            }
            Section {
                NavigationLink { WeeklyReviewView() } label: {
                    DestinationLabel(title: "Weekly review", subtitle: "Patterns, decisions & what changed", symbol: "calendar.badge.clock", color: Theme.sage)
                }
            }
            Section {
                MonthCalendar(diary: diary, month: $month, day: selected, today: model.now, days: days) { date in
                    selected = date
                    path.append(diary.dayKey(date))
                }
                HStack(spacing: 6) {
                    Circle().fill(Theme.accent).frame(width: 6, height: 6)
                    Text("Saved recap").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if calendarLoading { ProgressView().accessibilityLabel("Loading saved recap dates") }
                }
                ErrorNote(message: calendarError)
                if calendarError != nil { Button("Retry calendar") { Task { await loadCalendar() } } }
            } footer: { Text("Dates in \(diary.calendar.timeZone.identifier)") }
            Section("Saved days") {
                if loading && entries.isEmpty { ProgressView("Opening your diary…") }
                ErrorNote(message: error)
                if error != nil { Button("Retry diary") { Task { await load() } } }
                if entries.isEmpty && !loading && error == nil {
                    ContentUnavailableView("The start of your diary", systemImage: "book.closed", description: Text("Completed daily recaps are saved here, ready to return to."))
                }
                ForEach(Array(entries.enumerated()), id: \.element["day"].text) { _, entry in
                    if let date = diary.date(for: entry["day"].text) {
                        NavigationLink(value: entry["day"].text) {
                            HStack(alignment: .top, spacing: 14) {
                                VStack(spacing: 2) {
                                    Text(diary.label(date, template: "MMM").uppercased()).font(.caption2.weight(.semibold))
                                    Text(diary.label(date, template: "d")).font(.system(.title2, design: .serif))
                                }.foregroundStyle(Theme.accent).frame(width: 48, height: 54)
                                    .background(Theme.accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
                                    .accessibilityHidden(true)
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(diary.label(date, template: "EEEE d MMM")).font(.headline)
                                    Text(preview(entry["summary"].text)).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                                }
                            }.padding(.vertical, 6)
                        }
                    }
                }
            }
            Section {
                NavigationLink { ChatView(initialScope: .all) } label: { Label("Chat across your diary", systemImage: "bubble.left.and.bubble.right") }
            }
        }
        .listStyle(.insetGrouped).omiScreen()
        .navigationTitle("Diary").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Today", action: goToToday).accessibilityIdentifier("diary.today") } }
        .navigationDestination(for: String.self) { day in
            if let date = diary.date(for: day) { DayView(date: date) }
        }
        .navigationDestination(isPresented: Binding(get: { !path.isEmpty }, set: { if !$0 { path = [] } })) {
            if let key = path.last, let date = diary.date(for: key) { DayView(date: date) }
        }
        .task(id: model.archiveIdentity + model.archiveRevision) { await load() }
        .task(id: calendarKey) { await loadCalendar() }
        .task(id: model.archiveIdentity + ".diary-poll") {
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                async let entries: Void = load()
                async let dates: Void = loadCalendar()
                _ = await (entries, dates)
            }
        }
        .refreshable {
            async let entries: Void = load()
            async let dates: Void = loadCalendar()
            _ = await (entries, dates)
        }
        .onChange(of: model.serverTimeZoneIdentifier) { old, _ in
            let previous = DiaryCalendar(timeZone: TimeZone(identifier: old ?? "") ?? .current)
            month = diary.date(for: previous.dayKey(month)) ?? model.now
            selected = diary.date(for: previous.dayKey(selected)) ?? model.now
        }
    }

    private func preview(_ text: String) -> String {
        let lines = text.components(separatedBy: .newlines)
            .filter { !$0.hasPrefix("#") && !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        return lines.joined(separator: " ").replacingOccurrences(of: "**", with: "").replacingOccurrences(of: "- ", with: "")
    }

    private func load() async {
        let identity = model.archiveIdentity, token = UUID()
        entryRequest = token; loading = true; error = nil
        defer { if entryRequest == token { loading = false } }
        do {
            let response = try await model.client().request("v1/diary")
            guard !Task.isCancelled, identity == model.archiveIdentity, entryRequest == token else { return }
            entries = response["items"].array.sorted { $0["day"].text > $1["day"].text }
        } catch {
            if !Task.isCancelled, identity == model.archiveIdentity, entryRequest == token { self.error = error.localizedDescription }
        }
    }

    private func loadCalendar() async {
        let key = calendarKey, requestedMonth = diary.monthKey(month), token = UUID()
        calendarRequest = token; calendarLoading = true; calendarError = nil
        if loadedCalendarKey != key { days = [:] }
        defer { if calendarRequest == token { calendarLoading = false } }
        do {
            let response = try await model.client().request("v1/calendar", query: ["month": requestedMonth])
            guard !Task.isCancelled, calendarRequest == token, calendarKey == key else { return }
            guard response["month"].text == requestedMonth, case .array = response["days"] else { throw BackendFailure(message: "The calendar response was invalid.") }
            days = [:]; loadedCalendarKey = key
            for value in response["days"].array {
                let entry = CalendarDay(value)
                if entry.day.hasPrefix(requestedMonth + "-"), diary.date(for: entry.day) != nil { days[entry.day] = entry }
            }
        } catch {
            if !Task.isCancelled, calendarRequest == token, calendarKey == key { calendarError = error.localizedDescription }
        }
    }
}

/// Keep the complete day metadata so additional calendar annotations can share this model.
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

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Button { month = diary.month(month, offset: -1) } label: { Image(systemName: "chevron.left").frame(minWidth: 44, minHeight: 44) }
                    .accessibilityLabel("Previous month")
                Spacer(minLength: 0)
                Text(diary.label(month, template: "MMMM yyyy")).font(.subheadline.weight(.medium))
                Spacer(minLength: 0)
                Button { month = diary.month(month, offset: 1) } label: { Image(systemName: "chevron.right").frame(minWidth: 44, minHeight: 44) }
                    .disabled(diary.monthKey(month) >= diary.monthKey(today)).accessibilityLabel("Next month")
            }.buttonStyle(.borderless)
            if dynamicTypeSize >= .xxLarge {
                ForEach(diary.cells(in: month).compactMap { $0 }, id: \.self) { date in
                    accessibleDay(date)
                }
            } else {
                LazyVGrid(columns: columns, spacing: 4) {
                    ForEach(Array(diary.weekdaySymbols.enumerated()), id: \.offset) { _, symbol in
                        Text(symbol).font(.caption.weight(.semibold)).foregroundStyle(.secondary).frame(maxWidth: .infinity).accessibilityHidden(true)
                    }
                    ForEach(Array(diary.cells(in: month).enumerated()), id: \.offset) { _, date in
                        if let date { cell(date) }
                        else { Color.clear.frame(height: 44).accessibilityHidden(true) }
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
            .accessibilityLabel(diary.label(date, template: "EEEE d MMMM yyyy") + (key == todayKey ? ", today" : "") + (summary ? ", saved summary" : ""))
            .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func accessibleDay(_ date: Date) -> some View {
        let key = diary.dayKey(date)
        return Button { select(date) } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(diary.label(date, template: "EEEE d MMMM"))
                if days[key]?.hasSummary == true { Label("Saved summary", systemImage: "circle.fill").font(.caption) }
            }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).padding(8)
                .background(key == diary.dayKey(day) ? Theme.accent.opacity(0.12) : Color.clear, in: RoundedRectangle(cornerRadius: 10))
        }.buttonStyle(.plain).disabled(key > todayKey)
            .accessibilityAddTraits(key == diary.dayKey(day) ? .isSelected : [])
    }
}


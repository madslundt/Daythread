import SwiftUI
import OmiCore

struct DayView: View {
    @Environment(AppModel.self) private var model
    @State private var prepOpen = false
    @State private var day = Date()
    @State private var month = Date()
    @State private var daySelected = false
    @State private var recap: JSONValue = .null
    @State private var transcripts: [JSONValue] = []
    @State private var error: String?
    @State private var loading = false
    @State private var recapRefreshError: String?
    @State private var cached = false
    @State private var total = 0
    @State private var days: [String: CalendarDay] = [:]
    @State private var calendarError: String?
    @State private var calendarLoading = false
    @State private var calendarRevision = 0
    @State private var dayRequest = UUID()
    @State private var calendarRequest = UUID()

    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var dayKey: String { diary.dayKey(day) }
    private var cacheKey: String { model.archiveIdentity + ".day." + dayKey }
    private var monthKey: String { diary.monthKey(month) }
    private var calendarKey: String { model.archiveIdentity + ".calendar." + monthKey + "." + diary.calendar.timeZone.identifier }
    private var calendarTaskKey: String { calendarKey + ".\(calendarRevision)" }

    var body: some View {
        List {
            Section {
                EditorialTitle(eyebrow: "Your diary", title: "A day, remembered.", subtitle: "Return to your conversations, ideas, and little details.")
                    .editorialRow()
            }
            Section {
                DatePicker("Selected day", selection: Binding(get: { day }, set: { select($0) }), in: ...model.now, displayedComponents: .date)
                    .environment(\.calendar, diary.calendar).environment(\.timeZone, diary.calendar.timeZone)
                DisclosureGroup {
                    MonthCalendar(diary: diary, month: $month, day: day, today: model.now, days: days, select: select)
                    HStack(spacing: 6) {
                        Circle().fill(Theme.accent).frame(width: 6, height: 6)
                        Text("Saved summary").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        if calendarLoading { ProgressView().accessibilityLabel("Loading calendar summaries") }
                    }
                    if let calendarError {
                        ErrorNote(message: calendarError)
                        Button("Retry calendar") { calendarRevision += 1 }
                    }
                } label: { Label("Browse your calendar", systemImage: "calendar") }
                if cached { Label("Saved on this iPhone · refresh to update", systemImage: "internaldrive").font(.caption).foregroundStyle(.secondary) }
                if loading { ProgressView("Reading your day…") }
                ErrorNote(message: error)
            } footer: { Text("Dates in \(diary.calendar.timeZone.identifier)") }
            Section("Daily recap · \(diary.label(day, template: "d MMM yyyy"))") {
                ErrorNote(message: recapRefreshError)
                if recap["stale"] == .bool(true) { Text("New or changed evidence is available. Regenerate this recap.").font(.caption).foregroundStyle(.orange) }
                if let included = AppModel.date(recap["included_through"].text) {
                    Text("Includes completed conversations through \(diary.label(included, template: "d MMM HHmm")).").font(.caption).foregroundStyle(.secondary)
                } else if recap["included_through"] == .null && recap != .null && recap["summary"].text.isEmpty {
                    Text("No completed conversations included yet.").font(.caption).foregroundStyle(.secondary)
                }
                if recap["deferred_conversations"].integer > 0 || recap["deferred_sources"].integer > 0 {
                    Text("Current conversations are waiting to finish before being included. Active conversations stay out of the recap.").font(.caption).foregroundStyle(.secondary)
                }
                if !recap["boundary_note"].text.isEmpty { Text(recap["boundary_note"].text).font(.caption).foregroundStyle(.secondary) }
                if !recap["summary"].text.isEmpty {
                    Text(.init(recap["summary"].text)).textSelection(.enabled)
                    RecapImportanceView(recap: recap)
                    EvidenceFeedbackView(outputID: recap["output_id"].text, evidence: recap["evidence"], outputText: recap["summary"].text)
                    if !recap["generated_at"].text.isEmpty { Text("Generated \(recap["generated_at"].text)").font(.caption).foregroundStyle(.secondary) }
                } else {
                    VStack(alignment: .leading, spacing: 10) {
                        Image(systemName: "book.pages").font(.title2).foregroundStyle(Theme.accent).accessibilityHidden(true)
                        Text("Your day, in a few words.").font(.headline)
                        Text(recapEmptyMessage).foregroundStyle(.secondary)
                    }.padding(.vertical, 8)
                }
                Button { Task { await generate() } } label: { Label(recap["summary"].text.isEmpty ? "Generate daily recap" : "Regenerate daily recap", systemImage: "sparkle") }
                    .buttonStyle(PrimaryActionStyle()).disabled(loading)
                CompletenessView(value: recap["completeness"])
            }
            Section("Make space for what’s next") {
                InboxLink()
                NavigationLink { LabelsView() } label: { Label("People & projects", systemImage: "person.2") }
                NavigationLink {
                    ChatView(initialScope: .day(dayKey)).id(dayKey)
                } label: { Label("Chat with this day", systemImage: "bubble.left.and.bubble.right") }
                    .accessibilityIdentifier("day.chat")
                Button { prepOpen = true } label: { Label("Prepare for a conversation", systemImage: "person.crop.rectangle") }
            }
            DecisionsSection(day: dayKey).id(model.archiveIdentity + dayKey + model.archiveRevision)
            if !dayMeetings.isEmpty {
                Section("Meetings") {
                    ForEach(Array(dayMeetings.enumerated()), id: \.offset) { _, meeting in
                        NavigationLink {
                            ChatView(initialScope: .meeting(meeting["id"].text)).id(meeting["id"].text)
                        } label: {
                            Label("Chat with meeting · " + (AppModel.date(meeting["started_at"].text).map { diary.label($0, template: "HHmm") } ?? meeting["started_at"].text), systemImage: "person.2")
                        }
                        ImportanceButton(target: ImportanceTarget(kind: "meeting", id: meeting["id"].text), label: "meeting")
                        NavigationLink {
                            SpeakersView(kind: "meeting", conversationID: meeting["id"].text)
                        } label: { Label("Meeting speakers · listen & name", systemImage: "person.wave.2") }
                    }
                }
            }
            Section("Transcript · \(total) chunks") {
                if transcripts.isEmpty && !loading { ContentUnavailableView("No transcript yet", systemImage: "text.alignleft", description: Text("Capture audio with Omi, then sync to home-core.")) }
                SourceList(sources: transcripts)
                if transcripts.count < total {
                    Button("Load more") { Task { await loadMore() } }.disabled(loading)
                }
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle("Day").navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $prepOpen) { ConversationPrepView() }

            .task(id: cacheKey + model.archiveRevision) { await load() }
            .task(id: calendarTaskKey) { await loadCalendar() }
            .task(id: cacheKey + model.archiveRevision + ".recap-refresh") {
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .seconds(60)) } catch { return }
                    guard !Task.isCancelled else { return }
                    if dayKey == diary.dayKey(model.now) { await refreshRecap() }
                }
            }
            .refreshable {
                async let details: Void = load()
                async let calendar: Void = loadCalendar()
                _ = await (details, calendar)
            }
            .onChange(of: model.serverTimeZoneIdentifier) { old, _ in
                let previous = DiaryCalendar(timeZone: TimeZone(identifier: old ?? "") ?? .current)
                day = daySelected ? diary.date(for: previous.dayKey(day)) ?? model.now : model.now
                month = diary.date(for: previous.dayKey(month)) ?? day
                if !daySelected { month = day }
            }
            .onDisappear { dayRequest = UUID(); calendarRequest = UUID() }
    }

    private var recapEmptyMessage: String {
        if recap["no_recap_content"] == .bool(true) {
            return "No useful recap content in completed conversations yet. Your recordings and transcripts remain in the archive under your retention settings."
        }
        if recap["deferred_conversations"].integer > 0 || recap["deferred_sources"].integer > 0 {
            return "Your current conversation is waiting to finish before it can be included in a recap."
        }
        if recap["pending_chunks"].integer > 0 { return "Audio is still being transcribed. Completed conversations can be included once it is ready." }
        return "Completed conversations will bring the important parts of your day together here."
    }

    private var dayMeetings: [JSONValue] {
        model.serverTimeline["meetings"].array.filter {
            !$0["id"].text.isEmpty && AppModel.date($0["started_at"].text).map { diary.dayKey($0) == dayKey } == true
        }
    }

    private func select(_ date: Date) {
        day = date; month = date; daySelected = true
    }

    private func loadCalendar() async {
        let key = calendarKey, requestedMonth = monthKey, request = UUID()
        calendarRequest = request; calendarLoading = true; calendarError = nil; days = [:]
        defer { if calendarRequest == request { calendarLoading = false } }
        do {
            let response = try await model.client().request("v1/calendar", query: ["month": requestedMonth])
            guard !Task.isCancelled, calendarRequest == request, calendarKey == key else { return }
            guard response["month"].text == requestedMonth, case .array = response["days"] else {
                throw BackendFailure(message: "Home-core returned an invalid calendar response.")
            }
            for value in response["days"].array {
                let entry = CalendarDay(value)
                if entry.day.hasPrefix(requestedMonth + "-"), diary.date(for: entry.day) != nil { days[entry.day] = entry }
            }
        } catch {
            guard !Task.isCancelled, calendarRequest == request, calendarKey == key else { return }
            calendarError = "Could not load summary markers. \(error.localizedDescription)"
        }
    }

    private func load() async {
        let key = cacheKey, requestedDay = dayKey, request = UUID()
        dayRequest = request; error = nil; recapRefreshError = nil; loading = true; cached = false
        recap = .null; transcripts = []; total = 0
        if let saved = LocalCache.load(key) {
            recap = saved["recap"]; transcripts = saved["transcripts"].array; total = saved["total"].integer; cached = true
        }
        defer { if dayRequest == request { loading = false } }
        do {
            let client = try model.client()
            async let transcriptRequest = client.request("v1/transcripts", query: ["day": requestedDay, "limit": "100"])
            async let recapRequest = client.request("v1/recaps/\(requestedDay)")
            let (transcript, result) = try await (transcriptRequest, recapRequest)
            guard !Task.isCancelled, dayRequest == request, cacheKey == key else { return }
            transcripts = transcript["items"].array; total = transcript["total"].integer; recap = result; cached = false; save(key)
        } catch {
            guard !Task.isCancelled, dayRequest == request, cacheKey == key else { return }
            self.error = error.localizedDescription
        }
    }

    private func refreshRecap() async {
        guard !loading else { return }
        let key = cacheKey, requestedDay = dayKey, request = dayRequest
        do {
            let result = try await model.client().request("v1/recaps/\(requestedDay)")
            guard !Task.isCancelled, dayRequest == request, cacheKey == key else { return }
            recap = result; recapRefreshError = nil; save(key)
        } catch {
            guard !Task.isCancelled, dayRequest == request, cacheKey == key else { return }
            recapRefreshError = "Could not refresh the recap. \(error.localizedDescription)"
        }
    }

    private func generate() async {
        let key = cacheKey, requestedDay = dayKey, address = model.archiveIdentity, request = UUID()
        dayRequest = request; loading = true; error = nil; recapRefreshError = nil
        defer { if dayRequest == request { loading = false } }
        do {
            let result = try await model.client().request("v1/recaps/\(requestedDay)", method: "POST")
            if !Task.isCancelled, model.archiveIdentity == address { calendarRevision += 1 }
            guard !Task.isCancelled, dayRequest == request, cacheKey == key else { return }
            recap = result; cached = false; save(key)
        } catch {
            guard !Task.isCancelled, dayRequest == request, cacheKey == key else { return }
            self.error = error.localizedDescription
        }
    }

    private func loadMore() async {
        let key = cacheKey, requestedDay = dayKey, offset = transcripts.count, request = UUID()
        dayRequest = request; loading = true; error = nil
        defer { if dayRequest == request { loading = false } }
        do {
            let result = try await model.client().request("v1/transcripts", query: ["day": requestedDay, "limit": "100", "offset": "\(offset)"])
            guard !Task.isCancelled, dayRequest == request, cacheKey == key else { return }
            transcripts.append(contentsOf: result["items"].array); total = result["total"].integer; save(key)
        } catch {
            guard !Task.isCancelled, dayRequest == request, cacheKey == key else { return }
            self.error = error.localizedDescription
        }
    }

    private func save(_ key: String) { LocalCache.save(.object(["recap": recap, "transcripts": .array(transcripts), "total": .number(Double(total))]), key: key) }
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
            Text(diary.label(month, template: "MMMM yyyy")).font(.headline).frame(maxWidth: .infinity, alignment: .leading).accessibilityAddTraits(.isHeader)
            HStack {
                Button { month = diary.month(month, offset: -1) } label: { Image(systemName: "chevron.left").frame(minWidth: 44, minHeight: 44) }
                    .accessibilityLabel("Previous month")
                Spacer(minLength: 0)
                Button("Today") { select(today) }.font(.subheadline).frame(minHeight: 44)
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

import SwiftUI
import OmiCore

struct WeeklyReviewView: View {
    init(week: String = "", version: String = "") {
        _selectedWeek = State(initialValue: week)
        _selectedVersion = State(initialValue: version)
    }
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.goToToday) private var goToToday
    @State private var selectedWeek = ""
    @State private var selectedVersion = ""
    @State private var review: JSONValue = .null
    @State private var job: JSONValue = .null
    @State private var history: [JSONValue] = []
    @State private var showHistory = false
    @State private var loading = false
    @State private var busy = false
    @State private var error: String?
    @State private var historyError: String?
    @State private var refresh = UUID()
    @State private var requestID = UUID()
    @State private var operation: Task<Void, Never>?
    @State private var generationRequest: (context: String, force: Bool, id: UUID)?
    @State private var export: ArchiveMarkdownExport?
    @State private var selectedSource: SourceSelection?
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var currentWeek: String { diary.dayKey(diary.dateRange(for: .week, containing: model.now).lowerBound) }
    private var week: String { selectedWeek.isEmpty ? shift(currentWeek, by: -7) : selectedWeek }
    private var path: String { "v1/weekly-reviews/" + week }
    private var context: String { [model.archiveIdentity, model.archiveRevision, diary.calendar.timeZone.identifier, week, selectedVersion].joined(separator: "|") }
    private var loadKey: String { context + refresh.uuidString }
    private var running: Bool { ["queued", "running"].contains(job["state"].text) }
    private var version: JSONValue { review["version"] }
    private var versionQuery: [String: String] { selectedVersion.isEmpty ? [:] : ["version_id": selectedVersion] }

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Text("A little perspective").font(.caption.weight(.medium)).foregroundStyle(Theme.accent)
                    Text("Your week, considered.").font(.system(.largeTitle, design: .serif).weight(.medium))
                    Text("Decisions, commitments, and conversations worth coming back to.").foregroundStyle(.secondary)
                }.padding(.vertical, 8)
            }.listRowBackground(Color.clear)
            navigation
            if error != nil {
                Section { ErrorNote(message: error); Button("Try again") { refresh = UUID() } }
            }
            if loading && review == .null { Section { ProgressView("Reading your week…") } }
            if review != .null {
                if version != .null {
                    WeeklyReviewContent(version: version, sources: review["sources"].array, selectSource: { selectedSource = SourceSelection(source: $0) })
                }
                actions
                if showHistory { historySection }
                status
                Section("About this review") {
                    Text("This review uses two weeks of received recordings. It is not a complete backlog of older commitments. Absence of later mention never establishes completion.")
                    Text("Unuploaded phone backlog is unknown. Missing recordings do not establish silence or device failure. Transcripts cover whole overlapping chunks, so dates and audio boundaries can differ.")
                    if version != .null {
                        Text("Saved \(timestamp(version["generated_at"].text, timezone: version["timezone"].text)) · cutoff \(timestamp(version["cutoff"].text, timezone: version["timezone"].text))\(version["partial"] == .bool(true) ? " · partial week" : "")")
                        Text("Saved evidence: \(version["coverage"]["speech_sources"].integer) speech sources this week, \(version["comparison_coverage"]["speech_sources"].integer) the previous week. Bounds \(timestamp(version["start"].text, timezone: version["timezone"].text)) to \(timestamp(version["end"].text, timezone: version["timezone"].text)) (exclusive) · \(version["timezone"].text).")
                    }
                }.font(.caption).foregroundStyle(.secondary)
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle("Weekly review").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Today", action: goToToday) } }
            .task(id: loadKey) { await load() }
            .task(id: showHistory ? loadKey + ".history" : "") { if showHistory { await loadHistory() } }
            .task(id: context + job["id"].text + (running ? ".poll" : ".idle")) { await poll() }
            .refreshable { refresh = UUID() }
            .onChange(of: scenePhase) { _, phase in if phase == .active { refresh = UUID() } }
            .onChange(of: context) { _, _ in
                operation?.cancel(); operation = nil; busy = false; selectedSource = nil; export?.remove(); export = nil
            }
            .onDisappear { requestID = UUID(); operation?.cancel(); operation = nil; busy = false }
            .sheet(item: $export) { ArchiveMarkdownShareSheet(export: $0) }
            .sheet(item: $selectedSource) { selection in
                SourceDetail(source: selection.source, onCorrect: { selectedVersion = ""; refresh = UUID() })
            }
    }

    private var navigation: some View {
        Section {
            HStack {
                Button { select(shift(week, by: -7)) } label: { Image(systemName: "chevron.left").frame(minWidth: 44, minHeight: 44) }
                    .accessibilityLabel("Previous week")
                DatePicker("Week beginning", selection: Binding(get: { diary.date(for: week) ?? model.now }, set: {
                    select(diary.dayKey(diary.dateRange(for: .week, containing: $0).lowerBound))
                }), in: ...model.now, displayedComponents: .date)
                    .environment(\.timeZone, diary.calendar.timeZone)
                Button { select(shift(week, by: 7)) } label: { Image(systemName: "chevron.right").frame(minWidth: 44, minHeight: 44) }
                    .disabled(week >= currentWeek).accessibilityLabel("Next week")
            }.buttonStyle(.borderless)
            HStack {
                Button("Last completed week") { select(shift(currentWeek, by: -7)) }
                Spacer()
                Button("This week") { select(currentWeek) }
            }.buttonStyle(.borderless).font(.subheadline)
            Text("\(dateLabel(week)) – \(dateLabel(shift(week, by: 6)))").font(.headline)
            Text("Compared with \(dateLabel(shift(week, by: -7))) – \(dateLabel(shift(week, by: -1))) · \(diary.calendar.timeZone.identifier)")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var status: some View {
        Section("Recorded coverage") {
            if review["partial"] == .bool(true) {
                Label("Week in progress · evidence through \(timestamp(review["cutoff"].text))", systemImage: "clock")
                    .font(.caption).foregroundStyle(.secondary)
            }
            WeeklyCoverageRow(title: "Selected week", coverage: review["coverage"])
            WeeklyCoverageRow(title: "Previous week", coverage: review["comparison_coverage"])
            if review["comparison_coverage"]["speech_sources"].integer == 0 {
                Text("There is not enough recorded evidence to compare with the previous week.").font(.caption).foregroundStyle(.secondary)
            }
            if review["state"].text == "empty" {
                ContentUnavailableView("No transcribed speech yet", systemImage: "waveform", description: Text("Pending audio may appear here later."))
            }
            if review["stale"] == .bool(true) {
                Label("Recorded evidence or configuration changed. Refresh this saved review before relying on it.", systemImage: "arrow.triangle.2.circlepath")
                    .font(.callout).foregroundStyle(Theme.accent)
            }
            if !selectedVersion.isEmpty {
                Text("Historical version. Claims and quotes reflect its cutoff; source inspection shows current transcripts.").font(.caption).foregroundStyle(.secondary)
                Button("Return to latest review") { selectedVersion = "" }
            }
        }
    }

    private var actions: some View {
        Section {
            if running {
                ProgressView(job["state"].text == "queued" ? "Queued for local generation…" : "Generating locally · \(job["completed_calls"].integer) model calls")
                Text("You can leave this view; home-core keeps working.").font(.caption).foregroundStyle(.secondary)
            }
            if job["state"].text == "failed" { ErrorNote(message: job["error"].text.isEmpty ? "Generation failed. Try again." : job["error"].text) }
            Button { generate(force: false) } label: {
                Label(busy ? "Working…" : version == .null ? "Generate review" : review["stale"] == .bool(true) ? "Refresh review" : "Use saved review", systemImage: "sparkles")
            }.disabled(busy || running || review["coverage"]["speech_sources"].integer == 0)
            if version != .null {
                Button("Regenerate review", systemImage: "arrow.clockwise") { generate(force: true) }.disabled(busy || running)
                Button("Export Markdown", systemImage: "square.and.arrow.up") { exportMarkdown() }.disabled(busy)
            }
            NavigationLink {
                ChatView(initialScope: .range(week, shift(week, by: 6)))
            } label: { Label("Chat about this week", systemImage: "bubble.left.and.bubble.right") }
            Button(showHistory ? "Hide review history" : "Review history", systemImage: "clock.arrow.circlepath") { showHistory.toggle() }
        }
    }

    private var historySection: some View {
        Section("Saved reviews") {
            ErrorNote(message: historyError)
            if historyError != nil { Button("Retry history") { refresh = UUID() } }
            if history.isEmpty { Text("No saved versions.").foregroundStyle(.secondary) }
            ForEach(Array(history.enumerated()), id: \.element["id"].text) { _, entry in
                Button { selectedVersion = entry["id"].text } label: {
                    HStack {
                        Text(timestamp(entry["created_at"].text))
                        Spacer()
                        if selectedVersion == entry["id"].text { Image(systemName: "checkmark") }
                    }
                }
            }
        }
    }

    private func load() async {
        let key = loadKey, token = UUID(); requestID = token
        loading = true; error = nil; review = .null; job = .null
        defer { if requestID == token { loading = false } }
        do {
            let value = try await model.client().request(path, query: versionQuery)
            guard !Task.isCancelled, key == loadKey, requestID == token else { return }
            guard value["week_start"].text == week else { throw BackendFailure(message: "Home-core returned a review for a different week.") }
            review = value; job = value["job"]
        } catch {
            guard !Task.isCancelled, key == loadKey, requestID == token else { return }
            self.error = error.localizedDescription
        }
    }

    private func loadHistory() async {
        let key = loadKey; history = []; historyError = nil
        do {
            let value = try await model.client().request(path + "/versions")
            guard !Task.isCancelled, key == loadKey else { return }
            history = value["items"].array
        } catch { if !Task.isCancelled, key == loadKey { historyError = error.localizedDescription } }
    }

    private func poll() async {
        guard running else { return }
        let key = context, id = job["id"].text
        do {
            while !Task.isCancelled {
                try await Task.sleep(for: .seconds(2))
                let value = try await model.client().request("v1/weekly-reviews/jobs/" + id)
                guard !Task.isCancelled, context == key, job["id"].text == id else { return }
                job = value
                if !running { refresh = UUID(); return }
            }
        } catch { if !Task.isCancelled, context == key { self.error = error.localizedDescription } }
    }

    private func generate(force: Bool) {
        guard !busy && !running else { return }
        let key = context, requestedPath = path
        if generationRequest?.context != key || generationRequest?.force != force {
            generationRequest = (key, force, UUID())
        }
        let id = generationRequest!.id
        busy = true; error = nil
        operation = Task {
            defer { if context == key { busy = false; operation = nil } }
            do {
                _ = try await model.client().request(requestedPath + "/generate", method: "POST", body: ["request_id": id.uuidString, "force": force])
                guard !Task.isCancelled, context == key else { return }
                generationRequest = nil; selectedVersion = ""; refresh = UUID()
            } catch { if !Task.isCancelled, context == key { self.error = error.localizedDescription } }
        }
    }

    private func exportMarkdown() {
        guard !busy else { return }
        let key = context, requestedPath = path + ".md", query = versionQuery, name = "week-" + week
        busy = true; error = nil
        operation = Task {
            defer { if context == key { busy = false; operation = nil } }
            do {
                let result = try await ArchiveMarkdownExport.prepare(client: model.client(), path: requestedPath, query: query, name: name)
                guard !Task.isCancelled, key == context else { result.remove(); return }
                export = result
            } catch { if !Task.isCancelled, context == key { self.error = error.localizedDescription } }
        }
    }

    private func select(_ value: String) { selectedWeek = value; selectedVersion = ""; error = nil; generationRequest = nil }
    private func shift(_ day: String, by days: Int) -> String {
        guard let date = diary.date(for: day), let next = diary.calendar.date(byAdding: .day, value: days, to: date) else { return day }
        return diary.dayKey(next)
    }
    private func dateLabel(_ day: String) -> String { diary.date(for: day).map { diary.label($0, template: "d MMM") } ?? day }
    private func timestamp(_ text: String, timezone: String? = nil) -> String {
        let zone = TimeZone(identifier: timezone ?? review["timezone"].text) ?? diary.calendar.timeZone
        return AppModel.date(text).map { DiaryCalendar(timeZone: zone).label($0, template: "d MMM yyyy HHmm") } ?? text
    }
}

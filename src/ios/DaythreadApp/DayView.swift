import SwiftUI
import OmiCore

struct DayView: View {
    /// Nil follows the current server day, including midnight while the app is open.
    var date: Date? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.goToToday) private var goToToday
    private enum Destination: Hashable { case rewind, chat(String) }
    @State private var destination: Destination?
    @State private var importing = false
    @State private var recap: JSONValue = .null
    @State private var recordingGroups: [JSONValue] = []
    @State private var timeline: JSONValue = .null
    @State private var query = ""
    @State private var error: String?
    @State private var recapError: String?
    @State private var timelineError: String?
    @State private var loading = false
    @State private var cached = false
    @State private var total = 0
    @State private var totalRecordings = 0
    @State private var requestID = UUID()

    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var day: Date { date ?? model.now }
    private var dayKey: String { diary.dayKey(day) }
    private var cacheKey: String { model.archiveIdentity + ".day-recording-groups." + dayKey + "." + query }

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    EditorialTitle(eyebrow: diary.label(day, template: "EEEE d MMMM"), title: date == nil ? "Your day, together." : "A day, remembered.")
                    Text("The moments that matter, ready to return to.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }.editorialRow()
                DayQuickActions(importAudio: { importing = true }, rewind: { destination = .rewind }, ask: { destination = .chat(dayKey) })
                    .listRowInsets(EdgeInsets(top: 12, leading: 0, bottom: 0, trailing: 0)).editorialRow()
            }
            if date == nil { Section { CaptureGlance() } }
            if cached {
                Label("Saved on this iPhone · pull to refresh", systemImage: "internaldrive")
                    .font(.caption).foregroundStyle(.secondary).editorialRow()
            }
            recapSection
            Section {
                if total > 0 || !query.isEmpty {
                    HStack(spacing: 10) {
                        Image(systemName: "magnifyingglass").foregroundStyle(.secondary).accessibilityHidden(true)
                        TextField("Search this day’s recordings", text: $query).submitLabel(.search)
                            .accessibilityIdentifier("day.search")
                    }
                }
                ErrorNote(message: error)
                if error != nil { Button("Retry recordings") { Task { await load() } } }
                if loading { ProgressView("Finding your recordings…") }
                if recordingGroups.isEmpty && !loading && error == nil {
                    VStack(alignment: .leading, spacing: 12) {
                        Label(query.isEmpty ? "Every day starts somewhere." : "No matching recordings", systemImage: query.isEmpty ? "waveform" : "magnifyingglass").font(.headline)
                        Text(query.isEmpty ? "Add a recording from Files, or connect your Omi in Capture. Your recordings will appear here." : "Try another word, or choose a different day in Diary.")
                            .font(.subheadline).foregroundStyle(.secondary)
                        if query.isEmpty { Button("Add your first recording") { importing = true }.buttonStyle(.borderless) }
                    }.padding(.vertical, 12)
                }
                TimelineRecordingGroups(groups: recordingGroups, day: dayKey, query: query, timeline: timeline, onCorrect: { Task { await load() } })
                if recordingGroups.count < total {
                    Button("Load more recordings") { Task { await loadMore() } }.disabled(loading)
                }
            } header: {
                HStack { Text("Recordings"); Spacer(); if totalRecordings > 0 { Text("\(totalRecordings)").monospacedDigit() } }
            }
            Section("Follow through") {
                InboxLink()
                NavigationLink { DayDecisionsView(day: dayKey) } label: { Label("Decisions from this day", systemImage: "arrow.triangle.branch") }
                NavigationLink { LabelsView() } label: { Label("People & projects", systemImage: "person.2") }
            }
            if !dayMeetings.isEmpty || !dayMarkers.isEmpty || timelineError != nil {
                Section {
                    ErrorNote(message: timelineError)
                    ForEach(Array(dayMarkers.enumerated()), id: \.element["event_id"].text) { _, marker in
                        Label("Important around " + time(marker["occurred_at"].text), systemImage: "star")
                    }
                    ForEach(Array(dayMeetings.enumerated()), id: \.element["id"].text) { _, meeting in
                        DisclosureGroup("Meeting · " + time(meeting["started_at"].text) + (meeting["ended_at"].text.isEmpty ? " · in progress" : "–" + time(meeting["ended_at"].text))) {
                            NavigationLink { ChatView(initialScope: .meeting(meeting["id"].text)) } label: { Label("Chat with this meeting", systemImage: "bubble.left.and.bubble.right") }
                            ImportanceButton(target: ImportanceTarget(kind: "meeting", id: meeting["id"].text), label: "meeting")
                            NavigationLink { SpeakersView(kind: "meeting", conversationID: meeting["id"].text) } label: { Label("Listen & name speakers", systemImage: "person.wave.2") }
                        }
                    }
                } header: { Text("Moments & meetings") } footer: {
                    Text("Pendant hints point to nearby speech. These times are approximate.")
                }
            }
        }
        .listStyle(.insetGrouped).omiScreen()
        .navigationTitle(date == nil ? "Today" : diary.label(day, template: "d MMM"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                if date == nil {
                    Image("DaythreadMark").resizable().scaledToFit().frame(width: 28, height: 28)
                        .clipShape(RoundedRectangle(cornerRadius: 8)).accessibilityLabel("Daythread")
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                if date != nil { Button("Today", action: goToToday).accessibilityIdentifier("day.today") }
                else { NavigationLink { InboxView() } label: { Image(systemName: "tray").accessibilityLabel("Open inbox") } }
            }
        }
        .navigationDestination(item: $destination) { value in
            switch value {
            case .rewind: RewindView()
            case .chat(let selectedDay): ChatView(initialScope: .day(selectedDay)).id(selectedDay)
            }
        }
        .sheet(isPresented: $importing) { AudioImportView(onImported: { Task { await load() } }) }
        .task(id: cacheKey + model.archiveRevision) {
            if !query.isEmpty { do { try await Task.sleep(for: .milliseconds(300)) } catch { return } }
            await load()
        }
        .task(id: cacheKey + model.archiveRevision + ".poll") {
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                if !loading { await refresh() }
            }
        }
        .refreshable { await load() }
        .onDisappear { requestID = UUID() }
    }

    private var recapSection: some View {
        Section("Day recap") {
            ErrorNote(message: recapError)
            if recapError != nil { Button("Retry recap") { Task { await refresh() } } }
            if loading && recap == .null { ProgressView("Bringing your day together…") }
            else if !recap["summary"].text.isEmpty {
                RecapContentView(recap: recap, day: dayKey, onCorrect: { Task { await load() } })
                NavigationLink { RecapHistoryView(day: dayKey) } label: { Label("Saved versions & export", systemImage: "clock.arrow.circlepath") }
            } else if recapError == nil {
                VStack(alignment: .leading, spacing: 12) {
                    Image(systemName: "text.book.closed").font(.title2).foregroundStyle(Theme.accent).accessibilityHidden(true)
                    Text("A little perspective on your day.").font(.system(.title3, design: .serif))
                    Text(recapEmptyMessage).font(.subheadline).foregroundStyle(.secondary)
                }.padding(.vertical, 12)
            }
            if recap != .null {
                DisclosureGroup("About this recap") {
                    if recap["stale"] == .bool(true) { Text("New or changed evidence is available. This recap will update automatically.").font(.caption).foregroundStyle(Theme.accent) }
                    if let included = AppModel.date(recap["included_through"].text) {
                        Text("Includes completed conversations through \(diary.label(included, template: "d MMM HHmm")).").font(.caption)
                    }
                    if recap["deferred_conversations"].integer > 0 || recap["deferred_sources"].integer > 0 { Text("Current conversations are waiting to finish before being included.").font(.caption) }
                    if !recap["boundary_note"].text.isEmpty { Text(recap["boundary_note"].text).font(.caption) }
                    CompletenessView(value: recap["completeness"])
                    if !recap["summary"].text.isEmpty {
                        EvidenceFeedbackView(outputID: recap["output_id"].text, evidence: recap["evidence"], outputText: recap["summary"].text)
                    }
                }.font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }

    private var recapEmptyMessage: String {
        if recap["no_recap_content"] == .bool(true) { return "No useful recap content in completed conversations yet. Your recordings remain available below." }
        if recap["deferred_conversations"].integer > 0 || recap["deferred_sources"].integer > 0 { return "Your current conversation is waiting to finish. Completed conversations will come together here automatically." }
        if recap["pending_chunks"].integer > 0 { return "Audio is being transcribed. Your recap will come together as completed conversations are ready." }
        return "Your recap comes together automatically from completed conversations. Add audio to start remembering."
    }

    private var dayMarkers: [JSONValue] {
        timeline["markers"].array.filter {
            AppModel.date($0["occurred_at"].text).map { diary.dayKey($0) == dayKey } == true
        }
    }
    private var dayMeetings: [JSONValue] {
        timeline["meetings"].array.filter {
            !$0["id"].text.isEmpty && AppModel.date($0["started_at"].text).map { diary.dayKey($0) == dayKey } == true
        }
    }
    private func time(_ text: String) -> String { AppModel.date(text).map { diary.label($0, template: "HHmm") } ?? text }
    private func current(_ key: String, _ token: UUID) -> Bool { !Task.isCancelled && cacheKey == key && requestID == token }

    private func load() async {
        let key = cacheKey, token = UUID()
        requestID = token; loading = true; error = nil; recapError = nil; timelineError = nil; cached = false
        recap = .null; recordingGroups = []; timeline = .null; total = 0; totalRecordings = 0
        if let saved = LocalCache.load(key) {
            recap = saved["recap"]; recordingGroups = saved["recording_groups"].array
            timeline = saved["timeline"]; total = saved["total"].integer; totalRecordings = saved["total_recordings"].integer; cached = true
        }
        defer { if requestID == token { loading = false } }
        await fetch(key: key, token: token, limit: 30)
    }

    private func refresh() async { await fetch(key: cacheKey, token: requestID, limit: max(30, recordingGroups.count)) }

    private func fetch(key: String, token: UUID, limit: Int) async {
        do {
            let client = try model.client(), requestedDay = dayKey, search = query
            async let recapTask: Void = fetchRecap(client, day: requestedDay, key: key, token: token)
            async let groupsTask: Void = fetchGroups(client, day: requestedDay, search: search, key: key, token: token, limit: limit)
            async let timelineTask: Void = fetchTimeline(client, day: requestedDay, key: key, token: token)
            _ = await (recapTask, groupsTask, timelineTask)
            if current(key, token) { cached = error != nil || recapError != nil; save(key) }
        } catch { if current(key, token) { self.error = error.localizedDescription; recapError = error.localizedDescription } }
    }

    private func fetchRecap(_ client: BackendClient, day: String, key: String, token: UUID) async {
        do {
            let result = try await client.request("v1/recaps/\(day)")
            guard current(key, token) else { return }
            recap = result; recapError = nil
        } catch { if current(key, token) { recapError = error.localizedDescription } }
    }

    private func fetchGroups(_ client: BackendClient, day: String, search: String, key: String, token: UUID, limit: Int) async {
        do {
            var incoming: [JSONValue] = [], result: JSONValue = .null
            for offset in stride(from: 0, to: limit, by: 200) {
                result = try await client.request("v1/recording-groups", query: ["day": day, "q": search, "limit": "\(min(200, limit - offset))", "offset": "\(offset)"])
                guard current(key, token) else { return }
                incoming += result["items"].array
                if incoming.count >= result["total"].integer || result["items"].array.isEmpty { break }
            }
            var seen: Set<String> = []
            recordingGroups = incoming.filter { seen.insert($0["id"].text).inserted }
            total = result["total"].integer; totalRecordings = result["total_recordings"].integer; error = nil
        } catch { if current(key, token) { self.error = error.localizedDescription } }
    }

    private func fetchTimeline(_ client: BackendClient, day: String, key: String, token: UUID) async {
        do {
            let result = try await client.request("v1/timeline")
            guard current(key, token) else { return }
            timeline = result; timelineError = nil
        } catch { if current(key, token) { timelineError = "Pendant hints could not refresh. " + error.localizedDescription } }
    }

    private func loadMore() async {
        let key = cacheKey, token = UUID(), offset = recordingGroups.count
        requestID = token; loading = true; error = nil
        defer { if requestID == token { loading = false } }
        do {
            let result = try await model.client().request("v1/recording-groups", query: ["day": dayKey, "q": query, "limit": "30", "offset": "\(offset)"])
            guard current(key, token) else { return }
            let known = Set(recordingGroups.map { $0["id"].text })
            recordingGroups += result["items"].array.filter { !known.contains($0["id"].text) }
            total = result["total"].integer; totalRecordings = result["total_recordings"].integer; save(key)
        } catch { if current(key, token) { self.error = error.localizedDescription } }
    }

    private func save(_ key: String) {
        LocalCache.save(.object(["recap": recap, "recording_groups": .array(recordingGroups), "timeline": timeline, "total": .number(Double(total)), "total_recordings": .number(Double(totalRecordings))]), key: key)
    }
}

struct TimelineRecordingGroups: View {
    let groups: [JSONValue]
    let day: String
    var query = ""
    var timeline: JSONValue = .null
    var onCorrect: (() -> Void)? = nil

    var body: some View {
        ForEach(Array(groups.enumerated()), id: \.element["id"].text) { _, group in
            if group["recording_count"].integer == 1 {
                SourceList(sources: sourcesWithHints(group["sources"].array, timeline: timeline), onCorrect: onCorrect)
            } else {
                TimelineRecordingSession(group: group, day: day, query: query, timeline: timeline, onCorrect: onCorrect)
            }
        }
    }
}

private struct TimelineRecordingSession: View {
    let group: JSONValue
    let day: String
    var query = ""
    var timeline: JSONValue = .null
    var onCorrect: (() -> Void)? = nil
    @Environment(AppModel.self) private var model
    @State private var expanded = false
    @State private var requestID = UUID()
    @State private var sources: [JSONValue] = []
    @State private var total = 0
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            SourceList(sources: sourcesWithHints(sources, timeline: timeline), onCorrect: { Task { await load(reset: true) }; onCorrect?() })
            if busy { ProgressView("Loading recordings…") }
            ErrorNote(message: error)
            if error != nil || sources.count < total {
                Button(error == nil ? "Load more recordings" : "Retry recordings") {
                    Task { await load(reset: false) }
                }.disabled(busy)
            }
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                Text(group["title"].text).font(.body.weight(.medium)).foregroundStyle(.primary)
                Text("\(time(group["started_at"].text))–\(time(group["ended_at"].text)) · \(group["recording_count"].integer) recordings")
                    .font(.caption).foregroundStyle(.secondary)
                if group["estimated_times"] == .bool(true) {
                    Text("Estimated times").font(.caption).foregroundStyle(.secondary)
                }
            }.padding(.vertical, 5)
        }
        .task(id: "\(day).\(query).\(expanded).\(model.archiveIdentity).\(model.archiveRevision).\(group["recording_count"].integer)") {
            if expanded { await load(reset: true) }
        }
        .task(id: "\(day).\(query).\(expanded).\(model.archiveIdentity).poll") {
            guard expanded else { return }
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                if !busy { await load(reset: true) }
            }
        }
        .onDisappear { requestID = UUID() }
    }

    private func time(_ value: String) -> String {
        AppModel.date(value).map { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current).label($0, template: "HHmm") } ?? value
    }

    private func load(reset: Bool) async {
        guard reset || !busy else { return }
        let token = UUID(); requestID = token
        busy = true; error = nil
        defer { if requestID == token { busy = false } }
        do {
            let client = try model.client(), count = reset ? max(30, sources.count) : 30
            let firstOffset = reset ? 0 : sources.count
            var incoming: [JSONValue] = [], result: JSONValue = .null
            for offset in stride(from: 0, to: count, by: 200) {
                result = try await client.request("v1/recording-groups/\(group["id"].text)/sources", query: [
                    "day": day, "q": query, "limit": "\(min(200, count - offset))", "offset": "\(firstOffset + offset)"
                ])
                guard !Task.isCancelled, requestID == token else { return }
                incoming += result["items"].array
                if firstOffset + incoming.count >= result["total"].integer || result["items"].array.isEmpty { break }
            }
            var known = Set(reset ? [] : sources.map { $0["id"].text })
            sources = (reset ? [] : sources) + incoming.filter { known.insert($0["id"].text).inserted }
            total = result["total"].integer
        } catch { if !Task.isCancelled, requestID == token { self.error = error.localizedDescription } }
    }
}

@MainActor private func sourcesWithHints(_ sources: [JSONValue], timeline: JSONValue) -> [JSONValue] {
    sources.map { source in
        guard case .object(var values) = source,
              let start = AppModel.date(source["started_at"].text),
              let end = AppModel.date(source["ended_at"].text) else { return source }
        @MainActor func overlaps(_ hint: JSONValue) -> Bool {
            guard let lower = AppModel.date(hint["context_start"].text),
                  let upper = AppModel.date(hint["context_end"].text) else { return false }
            return start < upper && end > lower
        }
        if timeline["markers"].array.contains(where: overlaps) { values["important"] = .bool(true) }
        if case .array(let meetings) = timeline["meetings"] {
            values["meeting_hint_ids"] = .array(meetings.filter(overlaps).map { $0["id"] })
        }
        return .object(values)
    }
}

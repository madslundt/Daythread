import SwiftUI
import OmiCore

struct DayView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    /// Nil follows the current server day, including midnight while the app is open.
    @State private var selectedDate: Date?
    private enum Destination: Hashable { case rewind, chat(String) }
    @State private var destination: Destination?
    private enum Sheet: Identifiable {
        case importAudio, filters, source(SourceSelection)
        var id: String {
            switch self {
            case .importAudio: return "import-audio"
            case .filters: return "filters"
            case .source(let selection): return "source-\(selection.id)"
            }
        }
    }
    @State private var sheet: Sheet?
    @State private var recap: JSONValue = .null
    @State private var recordingGroups: [JSONValue] = []
    @State private var timeline: JSONValue = .null
    @State private var query = ""
    @State private var zoneID = ""
    @State private var zones: [LocationZone] = []
    @State private var zoneError: String?
    @State private var tagID = ""
    @State private var tags: [ManualTag] = []
    @State private var tagRevision = ""
    @State private var tagError: String?
    private var filterQuery: [String: String] {
        var result: [String: String] = [:]
        if !zoneID.isEmpty { result["zone_id"] = zoneID }
        if !tagID.isEmpty { result["tag_id"] = tagID }
        return result
    }
    @State private var error: String?
    @State private var recapError: String?
    @State private var timelineError: String?
    @State private var loading = false
    @State private var cached = false
    @State private var total = 0
    @State private var totalRecordings = 0
    @State private var requestID = UUID()

    init(date: Date? = nil) {
        _selectedDate = State(initialValue: date)
    }

    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var day: Date { selectedDate ?? model.now }
    private var dayKey: String { diary.dayKey(day) }
    private var cacheKey: String { model.archiveIdentity + ".day-recording-groups." + dayKey + "." + query + "." + zoneID + "." + String(zones.first { $0.id == zoneID }?.version ?? 0) + "." + tagID + "." + tagRevision }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Section {
                DayQuickActions(importAudio: { sheet = .importAudio }, rewind: { destination = .rewind }, ask: { destination = .chat(dayKey) })
                    .editorialRow()
                CaptureGlance().editorialRow()
            }
            Section { DayCalendarPicker(selectedDate: $selectedDate) }
            if cached {
                Label(L10n.text("Saved on this iPhone · pull to refresh"), systemImage: "internaldrive")
                    .font(.caption).foregroundStyle(.secondary).editorialRow()
            }
            recapSection
            Section {
                if total > 0 || !query.isEmpty || !zoneID.isEmpty || !tagID.isEmpty {
                    HStack(spacing: 10) {
                        Image(systemName: "magnifyingglass").foregroundStyle(.secondary).accessibilityHidden(true)
                        TextField(L10n.text("Search this day’s recordings"), text: $query).submitLabel(.search)
                            .accessibilityIdentifier("day.search")
                    }
                }
                ErrorNote(message: error)
                if error != nil { Button(L10n.text("Retry recordings")) { Task { await load() } } }
                if loading { ProgressView(L10n.text("Finding your recordings…")) }
                if recordingGroups.isEmpty && !loading && error == nil {
                    VStack(alignment: .leading, spacing: 12) {
                        Label(query.isEmpty && zoneID.isEmpty && tagID.isEmpty ? L10n.text("No recordings yet") : L10n.text("No matching recordings"), systemImage: query.isEmpty ? "waveform" : "magnifyingglass").font(.headline)
                        Text(query.isEmpty && zoneID.isEmpty && tagID.isEmpty ? L10n.text("Add a recording from Files, or connect your Omi in Capture. Your recordings will appear here.") : L10n.text("Try another word, or choose a different date above."))
                            .font(.subheadline).foregroundStyle(.secondary)
                        if query.isEmpty && zoneID.isEmpty && tagID.isEmpty { Button(L10n.text("Add your first recording")) { sheet = .importAudio }.buttonStyle(.borderless) }
                    }.padding(.vertical, 12)
                }
                TimelineRecordingGroups(
                    groups: recordingGroups, day: dayKey, query: query, zoneID: zoneID, tagID: tagID,
                    timeline: timeline,
                    onCorrect: { Task { await loadTags(); await load() } },
                    onSelect: { sheet = .source(SourceSelection(source: $0)) }
                )
                if recordingGroups.count < total {
                    Button(L10n.text("Load more recordings")) { Task { await loadMore() } }.disabled(loading)
                }
            } header: {
                HStack {
                    Text(L10n.text("Recordings"))
                    if total > 0 { Text("\(total)").monospacedDigit() }
                    Spacer()
                    Button { sheet = .filters } label: {
                        Label(activeFilterCount == 0 ? L10n.text("Filters") : L10n.text("Filters · \(activeFilterCount)"),
                              systemImage: activeFilterCount == 0 ? "line.3.horizontal.decrease.circle" : "line.3.horizontal.decrease.circle.fill")
                    }.frame(minHeight: 44).textCase(nil).accessibilityIdentifier("day.filters")
                }
            }
            Section {
                DisclosureGroup(L10n.text("Follow through")) {
                    InboxLink()
                    NavigationLink { DayDecisionsView(day: dayKey) } label: { Label(L10n.text("Decisions from this day"), systemImage: "arrow.triangle.branch") }
                    NavigationLink { LabelsView() } label: { Label(L10n.text("People & projects"), systemImage: "person.2") }
                }
            }
            if !dayMeetings.isEmpty || !dayMarkers.isEmpty || timelineError != nil {
                Section {
                    ErrorNote(message: timelineError)
                    ForEach(Array(dayMarkers.enumerated()), id: \.element["event_id"].text) { _, marker in
                        Label(L10n.text("Important around ") + time(marker["occurred_at"].text), systemImage: "star")
                    }
                    ForEach(Array(dayMeetings.enumerated()), id: \.element["id"].text) { _, meeting in
                        DisclosureGroup(L10n.text("Meeting · ") + time(meeting["started_at"].text) + (meeting["ended_at"].text.isEmpty ? L10n.text(" · in progress") : "–" + time(meeting["ended_at"].text))) {
                            NavigationLink { ChatView(initialScope: .meeting(meeting["id"].text)) } label: { Label(L10n.text("Chat with this meeting"), systemImage: "bubble.left.and.bubble.right") }
                            ImportanceButton(target: ImportanceTarget(kind: "meeting", id: meeting["id"].text), label: "meeting")
                            NavigationLink { SpeakersView(kind: "meeting", conversationID: meeting["id"].text) } label: { Label(L10n.text("Listen & name speakers"), systemImage: "person.wave.2") }
                        }
                    }
                } header: { Text(L10n.text("Moments & meetings")) } footer: {
                    Text(L10n.text("Pendant hints point to nearby speech. These times are approximate."))
                }
            }
        }
        .listStyle(.insetGrouped).omiScreen()
        .navigationTitle(L10n.text("Today"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Image("DaythreadMark").resizable().scaledToFit().frame(width: 28, height: 28)
                    .clipShape(RoundedRectangle(cornerRadius: 8)).accessibilityLabel("Daythread")
            }
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink { InboxView() } label: { Image(systemName: "tray").accessibilityLabel(L10n.text("Open inbox")) }
            }
        }
        .navigationDestination(item: $destination) { value in
            switch value {
            case .rewind: RewindView()
            case .chat(let selectedDay): ChatView(initialScope: .day(selectedDay)).id(selectedDay)
            }
        }
        .sheet(item: $sheet) { destination in
            switch destination {
            case .importAudio: AudioImportView(onImported: { Task { await load() } })
            case .filters:
                DayRecordingFilters(tagID: $tagID, zoneID: $zoneID, tags: tags, zones: zones, tagError: tagError, zoneError: zoneError)
            case .source(let selection):
                SourceDetail(source: selection.source, onCorrect: { Task { await loadTags(); await load() } })
            }
        }
        .task(id: model.archiveIdentity) {
            async let zones: Void = loadZones()
            async let tags: Void = loadTags()
            _ = await (zones, tags)
        }
        .onChange(of: model.archiveIdentity) { _, _ in zones = []; zoneID = ""; tags = []; tagID = ""; tagRevision = "" }
        .task(id: cacheKey + model.archiveRevision) {
            if !query.isEmpty { do { try await Task.sleep(for: .milliseconds(300)) } catch { return } }
            await load()
        }
        .task(id: cacheKey + model.archiveRevision + ".poll." + String(describing: scenePhase)) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                if !loading { await refresh() }
            }
        }
        .refreshable { await load() }
        .onDisappear { requestID = UUID() }
    }

    private var recapSection: some View {
        Section(L10n.text("Day recap")) {
            ErrorNote(message: recapError)
            if recapError != nil { Button(L10n.text("Retry recap")) { Task { await refresh() } } }
            if loading && recap == .null { ProgressView(L10n.text("Bringing your day together…")) }
            else if !recap["summary"].text.isEmpty {
                RecapContentView(recap: recap, day: dayKey, onCorrect: { Task { await load() } })
            } else if recapError == nil {
                Text(recapEmptyMessage).font(.subheadline).foregroundStyle(.secondary)
            }
            if recap != .null {
                DisclosureGroup(L10n.text("About this recap")) {
                    if !recap["summary"].text.isEmpty {
                        NavigationLink { RecapHistoryView(day: dayKey) } label: { Label(L10n.text("Saved versions & export"), systemImage: "clock.arrow.circlepath") }
                    }
                    if recap["stale"] == .bool(true) { Text(L10n.text("New or changed evidence is available. This recap will update automatically.")).font(.caption).foregroundStyle(Theme.accent) }
                    if let included = AppModel.date(recap["included_through"].text) {
                        Text(L10n.text("Includes transcribed recordings through \(diary.label(included, template: "d MMM HHmm")).")).font(.caption)
                    }
                    if recap["waiting_for_pause"].integer > 0 { Text(L10n.text("Live conversations are waiting for a longer pause before being summarized.")).font(.caption) }
                    if recap["pending_upload_sources"].integer > 0 || recap["pending_chunks"].integer > 0 { Text(L10n.text("Some recordings are awaiting upload or transcription.")).font(.caption) }
                    if !recap["boundary_note"].text.isEmpty { Text(L10n.key(recap["boundary_note"].text)).font(.caption) }
                    CompletenessView(value: recap["completeness"])
                    if !recap["summary"].text.isEmpty {
                        EvidenceFeedbackView(outputID: recap["output_id"].text, evidence: recap["evidence"], outputText: recap["summary"].text)
                    }
                }.font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }

    private var activeFilterCount: Int { (tagID.isEmpty ? 0 : 1) + (zoneID.isEmpty ? 0 : 1) }

    private var recapEmptyMessage: String {
        if recap["no_recap_content"] == .bool(true) { return L10n.text("No useful recap content in transcribed recordings yet. Your recordings remain available.") }
        if recap["waiting_for_pause"].integer > 0 { return L10n.text("Live conversations are waiting for a longer pause before being summarized.") }
        if recap["pending_upload_sources"].integer > 0 { return L10n.text("Some recordings are awaiting upload or transcription.") }
        if recap["pending_chunks"].integer > 0 { return L10n.text("Some recordings are awaiting upload or transcription.") }
        return L10n.text("No recap yet")
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

    private func loadTags() async {
        let identity = model.archiveIdentity
        do {
            let response: TagDirectory = try await model.client().decisionRequest("v1/tags")
            guard !Task.isCancelled, identity == model.archiveIdentity else { return }
            tags = response.items; tagRevision = response.revision; tagError = nil
        } catch { if !Task.isCancelled, identity == model.archiveIdentity { tagError = L10n.error(error) } }
    }

    private func loadZones() async {
        let identity = model.archiveIdentity
        do {
            let response: ZoneDirectory = try await model.client().decisionRequest("v1/location-zones")
            guard !Task.isCancelled, identity == model.archiveIdentity else { return }
            zones = response.items; zoneError = nil
        } catch { if !Task.isCancelled, identity == model.archiveIdentity { zoneError = L10n.error(error) } }
    }

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
        } catch { if current(key, token) { self.error = L10n.error(error); recapError = L10n.error(error) } }
    }

    private func fetchRecap(_ client: BackendClient, day: String, key: String, token: UUID) async {
        do {
            let result = try await client.request("v1/recaps/\(day)")
            guard current(key, token) else { return }
            recap = result; recapError = nil
        } catch { if current(key, token) { recapError = L10n.error(error) } }
    }

    private func fetchGroups(_ client: BackendClient, day: String, search: String, key: String, token: UUID, limit: Int) async {
        do {
            var incoming: [JSONValue] = [], result: JSONValue = .null
            for offset in stride(from: 0, to: limit, by: 200) {
                result = try await client.request("v1/recording-groups", query: ["day": day, "q": search, "limit": "\(min(200, limit - offset))", "offset": "\(offset)"].merging(filterQuery) { _, new in new })
                guard current(key, token) else { return }
                incoming += result["items"].array
                if incoming.count >= result["total"].integer || result["items"].array.isEmpty { break }
            }
            var seen: Set<String> = []
            recordingGroups = incoming.filter { seen.insert($0["id"].text).inserted }
            total = result["total"].integer; totalRecordings = result["total_recordings"].integer; error = nil
        } catch { if current(key, token) { self.error = L10n.error(error) } }
    }

    private func fetchTimeline(_ client: BackendClient, day: String, key: String, token: UUID) async {
        do {
            let result = try await client.request("v1/timeline")
            guard current(key, token) else { return }
            timeline = result; timelineError = nil
        } catch { if current(key, token) { timelineError = L10n.text("Pendant hints could not refresh. ") + L10n.error(error) } }
    }

    private func loadMore() async {
        let key = cacheKey, token = UUID(), offset = recordingGroups.count
        requestID = token; loading = true; error = nil
        defer { if requestID == token { loading = false } }
        do {
            let result = try await model.client().request("v1/recording-groups", query: ["day": dayKey, "q": query, "limit": "30", "offset": "\(offset)"].merging(filterQuery) { _, new in new })
            guard current(key, token) else { return }
            let known = Set(recordingGroups.map { $0["id"].text })
            recordingGroups += result["items"].array.filter { !known.contains($0["id"].text) }
            total = result["total"].integer; totalRecordings = result["total_recordings"].integer; save(key)
        } catch { if current(key, token) { self.error = L10n.error(error) } }
    }

    private func save(_ key: String) {
        LocalCache.save(.object(["recap": recap, "recording_groups": .array(recordingGroups), "timeline": timeline, "total": .number(Double(total)), "total_recordings": .number(Double(totalRecordings))]), key: key)
    }
}

struct TimelineRecordingGroups: View {
    let groups: [JSONValue]
    let day: String
    var query = ""
    var zoneID = ""
    var tagID = ""
    var timeline: JSONValue = .null
    var onCorrect: (() -> Void)? = nil
    var onSelect: ((JSONValue) -> Void)? = nil

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        ForEach(Array(groups.enumerated()), id: \.element["id"].text) { _, group in
            if group["recording_count"].integer == 1 {
                SourceList(sources: sourcesWithHints(group["sources"].array, timeline: timeline), onCorrect: onCorrect, onSelect: onSelect)
            } else {
                TimelineRecordingSession(group: group, day: day, query: query, zoneID: zoneID, tagID: tagID, timeline: timeline, onCorrect: onCorrect, onSelect: onSelect)
            }
        }
    }
}

private struct TimelineRecordingSession: View {
    let group: JSONValue
    let day: String
    var query = ""
    var zoneID = ""
    var tagID = ""
    var timeline: JSONValue = .null
    var onCorrect: (() -> Void)? = nil
    var onSelect: ((JSONValue) -> Void)? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var expanded = false
    @State private var requestID = UUID()
    @State private var sources: [JSONValue] = []
    @State private var chapters: [JSONValue] = []
    @State private var total = 0
    @State private var busy = false
    @State private var error: String?

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        DisclosureGroup(isExpanded: $expanded) {
            NavigationLink(L10n.text("Conversation tags")) {
                ConversationTagsView(groupID: group["id"].text, day: day, onChanged: onCorrect)
            }
            if !chapters.isEmpty {
                DisclosureGroup(L10n.text("Topics")) {
                    ForEach(Array(chapters.enumerated()), id: \.element["id"].text) { _, chapter in
                        NavigationLink {
                            List {
                                ForEach(Array(chapter["parts"].array.enumerated()), id: \.offset) { _, part in
                                    RecordingChapterPassage(chapterID: chapter["id"].text, part: part, onCorrect: onCorrect)
                                }
                            }.omiScreen().navigationTitle(chapter["title"].text)
                        } label: { Text(chapter["title"].text) }
                    }
                }
            }
            SourceList(
                sources: sourcesWithHints(sources, timeline: timeline),
                onCorrect: { Task { await load(reset: true) }; onCorrect?() },
                onSelect: onSelect
            )
            if busy { ProgressView(L10n.text("Loading recordings…")) }
            ErrorNote(message: error)
            if error != nil || sources.count < total {
                Button(error == nil ? L10n.text("Load more recordings") : L10n.text("Retry recordings")) {
                    Task { await load(reset: false) }
                }.disabled(busy)
            }
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                Text(group["title"].text).font(.body.weight(.medium)).foregroundStyle(.primary)
                let topicCount = group["topic_count"].integer
                Text(L10n.text("\(time(group["started_at"].text))–\(time(group["ended_at"].text)) · \(group["recording_count"].integer) recordings") + (topicCount > 0 ? " · " + L10n.text("\(topicCount) topics") : ""))
                    .font(.caption).foregroundStyle(.secondary)
                if group["estimated_times"] == .bool(true) {
                    Text(L10n.text("Estimated times")).font(.caption).foregroundStyle(.secondary)
                }
            }.padding(.vertical, 5)
        }
        .task(id: "\(day).\(query).\(zoneID).\(tagID).\(expanded).\(model.archiveIdentity).\(model.archiveRevision).\(group["recording_count"].integer)") {
            if expanded { await load(reset: true) }
        }
        .task(id: "\(day).\(query).\(zoneID).\(tagID).\(expanded).\(model.archiveIdentity).\(scenePhase).poll") {
            guard expanded, scenePhase == .active else { return }
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
                ].merging(zoneID.isEmpty ? [:] : ["zone_id": zoneID]) { _, new in new }
                    .merging(tagID.isEmpty ? [:] : ["tag_id": tagID]) { _, new in new })
                guard !Task.isCancelled, requestID == token else { return }
                incoming += result["items"].array
                if firstOffset + incoming.count >= result["total"].integer || result["items"].array.isEmpty { break }
            }
            var known = Set(reset ? [] : sources.map { $0["id"].text })
            sources = (reset ? [] : sources) + incoming.filter { known.insert($0["id"].text).inserted }
            total = result["total"].integer
            chapters = result["chapters"].array
        } catch { if !Task.isCancelled, requestID == token { self.error = L10n.error(error) } }
    }
}

private struct RecordingChapterPassage: View {
    let chapterID: String
    let part: JSONValue
    let onCorrect: (() -> Void)?
    @Environment(AppModel.self) private var model
    @State private var source: JSONValue = .null
    @State private var selected: SourceSelection?
    @State private var error: String?
    @State private var refresh = UUID()
    @State private var loading = true
    private var identity: String { model.archiveIdentity + model.archiveRevision + part["source_id"].text + refresh.uuidString }
    private var section: JSONValue? {
        guard source["transcript_version"] == part["transcript_version"] else { return nil }
        return source["sections"].array.first {
            let id = $0["chapter_id"].text.isEmpty ? source["id"].text + ":" + String($0["start_offset"].integer) : $0["chapter_id"].text
            return id == chapterID && $0["start_offset"] == part["start_offset"] && $0["end_offset"] == part["end_offset"]
        }
    }
    private var startAt: TimeInterval? {
        guard let section, case .number(let seconds) = section["start_seconds"] else { return nil }
        return seconds
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if loading { ProgressView(L10n.text("Loading recordings…")) }
            else if let section {
                SourceTranscriptView(source: source, editing: false,
                    startOffset: section["start_offset"].integer, endOffset: section["end_offset"].integer)
                Button { selected = SourceSelection(source: source) } label: {
                    Label(L10n.text("Full recording & tools"), systemImage: "waveform")
                }.buttonStyle(.borderless)
            } else if error == nil {
                Text(L10n.text("Recorded evidence changed or expired; reload and review it"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            ErrorNote(message: error)
            if !loading && (error != nil || section == nil) {
                Button(L10n.text("Retry recordings")) { refresh = UUID(); onCorrect?() }
            }
        }
        .sheet(item: $selected) { selection in
            SourceDetail(source: selection.source, onCorrect: { refresh = UUID(); onCorrect?() }, startAt: startAt)
        }
        .task(id: identity) {
            let requested = identity
            loading = true; source = .null; error = nil
            defer { if requested == identity { loading = false } }
            do {
                let result = try await model.client().request("v1/transcripts/\(part["source_id"].text)")
                guard !Task.isCancelled, requested == identity else { return }
                source = result
            } catch {
                guard !Task.isCancelled, requested == identity else { return }
                self.error = L10n.error(error)
            }
        }
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

private struct DayRecordingFilters: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var tagID: String
    @Binding var zoneID: String
    let tags: [ManualTag]
    let zones: [LocationZone]
    let tagError: String?
    let zoneError: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker(L10n.text("Tag"), selection: $tagID) {
                        Text(L10n.text("All tags")).tag("")
                        if !tagID.isEmpty && !tags.contains(where: { $0.id == tagID }) {
                            Text(L10n.text("Tag unavailable")).tag(tagID)
                        }
                        ForEach(tags) { tag in Text(tag.name).tag(tag.id) }
                    }.accessibilityIdentifier("day.tagFilter")
                    if let tagError { Text(tagError).font(.caption).foregroundStyle(.secondary) }
                    Picker(L10n.text("Location zone"), selection: $zoneID) {
                        Text(L10n.text("All locations")).tag("")
                        if !zoneID.isEmpty && !zones.contains(where: { $0.id == zoneID }) {
                            Text(L10n.text("Zone unavailable")).tag(zoneID)
                        }
                        ForEach(zones) { zone in Text(zone.name).tag(zone.id) }
                    }.accessibilityIdentifier("day.zoneFilter")
                    if let zoneError { Text(zoneError).font(.caption).foregroundStyle(.secondary) }
                    NavigationLink(L10n.text("Manage zones")) { LocationZonesView() }
                } footer: {
                    Text(L10n.text("Tag and location filters apply together to recordings. The day recap still covers the whole day."))
                }
                if !tagID.isEmpty || !zoneID.isEmpty {
                    Button(L10n.text("Clear filters")) { tagID = ""; zoneID = "" }
                        .accessibilityIdentifier("day.clearFilters")
                }
            }
            .navigationTitle(L10n.text("Filters")).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L10n.text("Done")) { dismiss() } } }
        }
    }
}

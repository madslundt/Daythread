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
    private var filters: DayFilterScope { DayFilterScope(tagID: tagID, zoneID: zoneID) }
    private var filterQuery: [String: String] { filters.query }
    @State private var error: String?
    @State private var recapError: String?
    @State private var timelineError: String?
    @State private var loading = false
    @State private var cached = false
    @State private var total = 0
    @State private var totalRecordings = 0
    @State private var requestID = UUID()
    @State private var loadedScope = ""
    @State private var loadedRecordingKey = ""
    @State private var membershipRevision = 0
    @State private var recapFocus: RecapTopicFocus?
    @State private var recapFocusScope = ""
    @State private var contentLanguage: String?
    @State private var contentLanguageScope = ""

    init(date: Date? = nil, filters: DayFilterScope = .init()) {
        _selectedDate = State(initialValue: date)
        _tagID = State(initialValue: filters.tagID)
        _zoneID = State(initialValue: filters.zoneID)
    }

    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var day: Date { selectedDate ?? model.now }
    private var dayKey: String { diary.dayKey(day) }
    private var scopeKey: String {
        let values = [model.archiveIdentity, model.archiveRevision, dayKey, filters.identity,
                      String(zones.first { $0.id == zoneID }?.version ?? 0), tagRevision, String(membershipRevision)]
        return try! JSONEncoder().encode(values).base64EncodedString()
    }
    private var cacheKey: String { model.archiveIdentity + ".day-content.v2." + scopeKey + "." + Data(query.utf8).base64EncodedString() }
    private var currentRecap: JSONValue { loadedScope == scopeKey ? recap : .null }
    private var currentGroups: [JSONValue] { loadedRecordingKey == cacheKey ? recordingGroups : [] }
    private var currentFocus: RecapTopicFocus? { recapFocusScope == scopeKey ? recapFocus : nil }
    private var currentContentLanguage: String? { contentLanguageScope == scopeKey ? contentLanguage : nil }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Section {
                DayQuickActions(importAudio: { sheet = .importAudio }, rewind: { destination = .rewind }, ask: { destination = .chat(dayKey) }, showsDayChat: !filters.isFiltered)
                    .editorialRow()
                CaptureGlance().editorialRow()
            }
            Section { DayCalendarPicker(selectedDate: $selectedDate) }
            DayFilterSection(scope: filters, tags: tags, zones: zones, open: { sheet = .filters }, clear: { tagID = ""; zoneID = "" })
            if cached && loadedRecordingKey == cacheKey {
                Label(L10n.text("Saved on this iPhone · pull to refresh"), systemImage: "internaldrive")
                    .font(.caption).foregroundStyle(.secondary).editorialRow()
            }
            recapSection
            DayAtGlanceSections(day: dayKey, topicSources: currentFocus?.sourceIDs, topicPassages: currentFocus?.passages ?? [],
                topicVersions: currentFocus?.versions ?? [:], filters: filters, topicTitle: currentFocus?.displayTitle ?? currentFocus?.title,
                contentLanguage: currentContentLanguage)
                .id(scopeKey + (currentFocus?.id ?? "day"))
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
                if currentGroups.isEmpty && !loading && error == nil && loadedRecordingKey == cacheKey {
                    VStack(alignment: .leading, spacing: 12) {
                        Label(query.isEmpty && zoneID.isEmpty && tagID.isEmpty ? L10n.text("No recordings yet") : L10n.text("No matching recordings"), systemImage: query.isEmpty ? "waveform" : "magnifyingglass").font(.headline)
                        Text(query.isEmpty && zoneID.isEmpty && tagID.isEmpty ? L10n.text("Add a recording from Files, or connect your Omi in Capture. Your recordings will appear here.") : L10n.text("Try another word, or choose a different date above."))
                            .font(.subheadline).foregroundStyle(.secondary)
                        if query.isEmpty && zoneID.isEmpty && tagID.isEmpty { Button(L10n.text("Add your first recording")) { sheet = .importAudio }.buttonStyle(.borderless) }
                    }.padding(.vertical, 12)
                }
                TimelineRecordingGroups(
                    groups: currentGroups, day: dayKey, query: query, zoneID: zoneID, tagID: tagID,
                    timeline: timeline,
                    onCorrect: { Task { await reloadMembership() } },
                    onSelect: { sheet = .source(SourceSelection(source: $0)) }
                ).id(scopeKey + query)
                if loadedRecordingKey == cacheKey && recordingGroups.count < total {
                    Button(L10n.text("Load more recordings")) { Task { await loadMore() } }.disabled(loading)
                }
            } header: {
                HStack {
                    Text(L10n.text("Recordings"))
                    if loadedRecordingKey == cacheKey && total > 0 { Text("\(total)").monospacedDigit() }
                }
            }
            Section {
                DisclosureGroup(L10n.text("Follow through")) {
                    InboxLink()
                    NavigationLink { LabelsView() } label: { Label(L10n.text("People & projects"), systemImage: "person.2") }
                }
            }
            if !filters.isFiltered && (!dayMeetings.isEmpty || !dayMarkers.isEmpty || timelineError != nil) {
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
        .sheet(item: $sheet, onDismiss: { Task { await reloadMembership() } }) { destination in
            switch destination {
            case .importAudio: AudioImportView(onImported: { Task { await load() } })
            case .filters:
                DayRecordingFilters(tagID: $tagID, zoneID: $zoneID, tags: tags, zones: zones, tagError: tagError, zoneError: zoneError,
                    onDirectoriesChanged: { Task { await loadTags(); await loadZones() } })
            case .source(let selection):
                SourceDetail(source: selection.source, onCorrect: { Task { await reloadMembership() } })
            }
        }
        .task(id: model.archiveIdentity + model.archiveRevision) {
            async let zones: Void = loadZones()
            async let tags: Void = loadTags()
            _ = await (zones, tags)
        }
        .onChange(of: model.archiveIdentity) { _, _ in zones = []; zoneID = ""; tags = []; tagID = ""; tagRevision = "" }
        .task(id: cacheKey) {
            if !query.isEmpty { do { try await Task.sleep(for: .milliseconds(300)) } catch { return } }
            await load()
        }
        .task(id: cacheKey + ".poll." + String(describing: scenePhase)) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                if !loading { await loadTags(); await loadZones(); await refresh() }
            }
        }
        .refreshable { await load() }
        .onDisappear { requestID = UUID() }
    }

    private var recapSection: some View {
        let recap = currentRecap
        let focusedScope = scopeKey
        let hasContent = !recap["summary"].text.isEmpty || recap["sources"].array.contains(where: { !$0["sections"].array.isEmpty })
        return Section(filters.isFiltered ? L10n.text("Filtered recap") : L10n.text("Day recap")) {
            if !filters.isFiltered {
                DayRecapOverview(day: dayKey, recap: recap, onCorrect: { Task { await reloadMembership() } })
                    .id(model.archiveIdentity + model.archiveRevision + dayKey)
            }
            ErrorNote(message: loadedScope == scopeKey ? recapError : nil)
            if loadedScope == scopeKey && recapError != nil { Button(L10n.text("Retry recap")) { Task { await retryRecap() } } }
            if recap["generation_state"].text == "failed" {
                if !hasContent { Text(L10n.text("The filtered recap could not be generated.")).font(.caption).foregroundStyle(.secondary) }
                ErrorNote(message: recap["generation_error"].text.isEmpty ? nil : L10n.key(recap["generation_error"].text))
                Button(L10n.text("Retry recap")) { Task { await retryRecap() } }
            }
            if loadedScope != scopeKey || (loading && recap == .null) {
                ProgressView(filters.isFiltered ? L10n.text("Loading filtered recap…") : L10n.text("Bringing your day together…"))
            }
            if !hasContent && ["queued", "running"].contains(recap["generation_state"].text) { ProgressView(L10n.text("Preparing a recap for the selected filters…")) }
            if hasContent {
                Text(L10n.text("Topics")).font(.headline)
                RecapContentView(recap: recap, day: dayKey, onCorrect: { Task { await reloadMembership() } }, emptyMessage: recapEmptyMessage, filters: filters,
                    onFocusChange: { value in if scopeKey == focusedScope { recapFocus = value; recapFocusScope = focusedScope } },
                    onContentLanguageChange: { value in if scopeKey == focusedScope { contentLanguage = value; contentLanguageScope = focusedScope } })
                    .id(scopeKey + recap["scope_fingerprint"].text + recap["output_id"].text + recap["summary"].text)
            } else if recap != .null && recapError == nil && !["queued", "running", "failed"].contains(recap["generation_state"].text) {
                Text(recapEmptyMessage).font(.subheadline).foregroundStyle(.secondary)
            }
            if recap != .null {
                DisclosureGroup(L10n.text("About this recap")) {
                    if !filters.isFiltered && !recap["summary"].text.isEmpty {
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
                    if !filters.isFiltered && !recap["summary"].text.isEmpty {
                        EvidenceFeedbackView(outputID: recap["output_id"].text, evidence: recap["evidence"], outputText: recap["summary"].text)
                    }
                }.font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }

    private var recapEmptyMessage: String {
        let recap = currentRecap
        if recap["generation_state"].text == "failed" { return L10n.text("The filtered recap could not be generated.") }
        if ["queued", "running"].contains(recap["generation_state"].text) { return L10n.text("Preparing a recap for the selected filters…") }
        if recap["no_matching_sources"] == .bool(true) { return L10n.text("No recordings match the selected filters for this day.") }
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

    private func reloadMembership() async {
        membershipRevision += 1
        requestID = UUID(); recap = .null; loadedScope = ""; recordingGroups = []; loadedRecordingKey = ""
        async let tags: Void = loadTags()
        async let zones: Void = loadZones()
        _ = await (tags, zones)
        await load()
    }

    private func load() async {
        let key = cacheKey, token = UUID()
        requestID = token; loading = true; error = nil; recapError = nil; timelineError = nil; cached = false
        if loadedScope != scopeKey { recap = .null; loadedScope = scopeKey }
        loadedRecordingKey = key; recordingGroups = []; timeline = .null; total = 0; totalRecordings = 0
        if !filters.isFiltered, let saved = LocalCache.load(key) {
            if !filters.isFiltered && recap == .null { recap = saved["recap"] }
            recordingGroups = saved["recording_groups"].array
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
            if current(key, token) { cached = !filters.isFiltered && (error != nil || recapError != nil); save(key) }
        } catch { if current(key, token) { self.error = L10n.error(error); recapError = L10n.error(error) } }
    }

    private func fetchRecap(_ client: BackendClient, day: String, key: String, token: UUID) async {
        do {
            let selectedFilters = filters
            let result = try await client.request("v1/recaps/\(day)", query: selectedFilters.query)
            guard current(key, token) else { return }
            let responseScope: [String: String]? = result["scope"] == .null ? nil : ["tag_id": result["scope"]["tag_id"].text, "zone_id": result["scope"]["zone_id"].text]
            guard selectedFilters.matchesResponseScope(responseScope), !selectedFilters.isFiltered || !result["scope_fingerprint"].text.isEmpty else {
                throw BackendFailure(message: L10n.text("The recap does not match the selected filters. Refresh and try again."))
            }
            recap = result; recapError = nil
        } catch { if current(key, token) { recapError = L10n.error(error) } }
    }

    private func retryRecap() async {
        guard filters.isFiltered else { await refresh(); return }
        let key = cacheKey, token = requestID, selectedFilters = filters
        do {
            _ = try await model.client().request("v1/jobs", method: "POST", body: ["id": UUID().uuidString.lowercased(), "kind": "recap", "parameters": ["day": dayKey].merging(selectedFilters.query) { _, new in new }])
            guard current(key, token) else { return }
            await refresh()
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
        guard !filters.isFiltered else { return }
        LocalCache.save(.object(["recap": recap, "recording_groups": .array(recordingGroups), "timeline": timeline, "total": .number(Double(total)), "total_recordings": .number(Double(totalRecordings))]), key: key)
    }
}

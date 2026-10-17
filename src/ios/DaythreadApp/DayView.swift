import SwiftUI
import OmiCore

struct DayView: View {
    /// Nil follows the current server day, including midnight while the app is open.
    var date: Date? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.goToToday) private var goToToday
    private enum Destination: Hashable { case rewind, chat(String) }
    @State private var destination: Destination?
    @State private var importing = false
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

    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var day: Date { date ?? model.now }
    private var dayKey: String { diary.dayKey(day) }
    private var cacheKey: String { model.archiveIdentity + ".day-recording-groups." + dayKey + "." + query + "." + zoneID + "." + String(zones.first { $0.id == zoneID }?.version ?? 0) + "." + tagID + "." + tagRevision }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    EditorialTitle(eyebrow: diary.label(day, template: "EEEE d MMMM"), title: date == nil ? L10n.text("Your day, together.") : L10n.text("A day, remembered."))
                    Text(L10n.text("The moments that matter, ready to return to."))
                        .font(.subheadline).foregroundStyle(.secondary)
                }.editorialRow()
                DayQuickActions(importAudio: { importing = true }, rewind: { destination = .rewind }, ask: { destination = .chat(dayKey) })
                    .listRowInsets(EdgeInsets(top: 12, leading: 0, bottom: 0, trailing: 0)).editorialRow()
            }
            if date == nil { Section { CaptureGlance() } }
            if cached {
                Label(L10n.text("Saved on this iPhone · pull to refresh"), systemImage: "internaldrive")
                    .font(.caption).foregroundStyle(.secondary).editorialRow()
            }
            recapSection
            Section {
                Picker(L10n.text("Tag"), selection: $tagID) {
                    Text(L10n.text("All tags")).tag("")
                    if !tagID.isEmpty && !tags.contains(where: { $0.id == tagID }) {
                        Text(L10n.text("Tag unavailable")).tag(tagID)
                    }
                    ForEach(tags) { tag in Text(tag.name).tag(tag.id) }
                }.accessibilityIdentifier("day.tagFilter")
                if let tagError { Text(tagError).font(.caption).foregroundStyle(.secondary) }
                if !tagID.isEmpty {
                    Text(L10n.text("Tag and location filters apply together to recordings. The day recap still covers the whole day."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Picker(L10n.text("Location zone"), selection: $zoneID) {
                    Text(L10n.text("All locations")).tag("")
                    if !zoneID.isEmpty && !zones.contains(where: { $0.id == zoneID }) {
                        Text(L10n.text("Zone unavailable")).tag(zoneID)
                    }
                    ForEach(zones) { zone in Text(zone.name).tag(zone.id) }
                }.accessibilityIdentifier("day.zoneFilter")
                NavigationLink(L10n.text("Manage zones")) { LocationZonesView() }
                if let zoneError { Text(zoneError).font(.caption).foregroundStyle(.secondary) }
                if !zoneID.isEmpty {
                    Text(L10n.text("Filters recordings with GPS inside this zone. The day recap still covers the whole day."))
                        .font(.caption).foregroundStyle(.secondary)
                }
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
                        Label(query.isEmpty && zoneID.isEmpty && tagID.isEmpty ? L10n.text("Every day starts somewhere.") : L10n.text("No matching recordings"), systemImage: query.isEmpty ? "waveform" : "magnifyingglass").font(.headline)
                        Text(query.isEmpty && zoneID.isEmpty && tagID.isEmpty ? L10n.text("Add a recording from Files, or connect your Omi in Capture. Your recordings will appear here.") : L10n.text("Try another word, or choose a different day in Diary."))
                            .font(.subheadline).foregroundStyle(.secondary)
                        if query.isEmpty && zoneID.isEmpty && tagID.isEmpty { Button(L10n.text("Add your first recording")) { importing = true }.buttonStyle(.borderless) }
                    }.padding(.vertical, 12)
                }
                TimelineRecordingGroups(groups: recordingGroups, day: dayKey, query: query, zoneID: zoneID, tagID: tagID, timeline: timeline, onCorrect: { Task { await loadTags(); await load() } })
                if recordingGroups.count < total {
                    Button(L10n.text("Load more recordings")) { Task { await loadMore() } }.disabled(loading)
                }
            } header: {
                HStack { Text(L10n.text("Recordings")); Spacer(); if totalRecordings > 0 { Text("\(totalRecordings)").monospacedDigit() } }
            }
            Section(L10n.text("Follow through")) {
                InboxLink()
                NavigationLink { DayDecisionsView(day: dayKey) } label: { Label(L10n.text("Decisions from this day"), systemImage: "arrow.triangle.branch") }
                NavigationLink { LabelsView() } label: { Label(L10n.text("People & projects"), systemImage: "person.2") }
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
        .navigationTitle(date == nil ? L10n.text("Today") : diary.label(day, template: "d MMM"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                if date == nil {
                    Image("DaythreadMark").resizable().scaledToFit().frame(width: 28, height: 28)
                        .clipShape(RoundedRectangle(cornerRadius: 8)).accessibilityLabel("Daythread")
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                if date != nil { Button(L10n.text("Today"), action: goToToday).accessibilityIdentifier("day.today") }
                else { NavigationLink { InboxView() } label: { Image(systemName: "tray").accessibilityLabel(L10n.text("Open inbox")) } }
            }
        }
        .navigationDestination(item: $destination) { value in
            switch value {
            case .rewind: RewindView()
            case .chat(let selectedDay): ChatView(initialScope: .day(selectedDay)).id(selectedDay)
            }
        }
        .sheet(isPresented: $importing) { AudioImportView(onImported: { Task { await load() } }) }
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
                NavigationLink { RecapHistoryView(day: dayKey) } label: { Label(L10n.text("Saved versions & export"), systemImage: "clock.arrow.circlepath") }
            } else if recapError == nil {
                VStack(alignment: .leading, spacing: 12) {
                    Image(systemName: "text.book.closed").font(.title2).foregroundStyle(Theme.accent).accessibilityHidden(true)
                    Text(L10n.text("A little perspective on your day.")).font(.system(.title3, design: .serif))
                    Text(recapEmptyMessage).font(.subheadline).foregroundStyle(.secondary)
                }.padding(.vertical, 12)
            }
            if recap != .null {
                DisclosureGroup(L10n.text("About this recap")) {
                    if recap["stale"] == .bool(true) { Text(L10n.text("New or changed evidence is available. This recap will update automatically.")).font(.caption).foregroundStyle(Theme.accent) }
                    if let included = AppModel.date(recap["included_through"].text) {
                        Text(L10n.text("Includes completed conversations through \(diary.label(included, template: "d MMM HHmm")).")).font(.caption)
                    }
                    if recap["deferred_conversations"].integer > 0 || recap["deferred_sources"].integer > 0 { Text(L10n.text("Current conversations are waiting to finish before being included.")).font(.caption) }
                    if !recap["boundary_note"].text.isEmpty { Text(L10n.key(recap["boundary_note"].text)).font(.caption) }
                    CompletenessView(value: recap["completeness"])
                    if !recap["summary"].text.isEmpty {
                        EvidenceFeedbackView(outputID: recap["output_id"].text, evidence: recap["evidence"], outputText: recap["summary"].text)
                    }
                }.font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }

    private var recapEmptyMessage: String {
        if recap["no_recap_content"] == .bool(true) { return L10n.text("No useful recap content in completed conversations yet. Your recordings remain available below.") }
        if recap["deferred_conversations"].integer > 0 || recap["deferred_sources"].integer > 0 { return L10n.text("Your current conversation is waiting to finish. Completed conversations will come together here automatically.") }
        if recap["pending_chunks"].integer > 0 { return L10n.text("Audio is being transcribed. Your recap will come together as completed conversations are ready.") }
        return L10n.text("Your recap comes together automatically from completed conversations. Add audio to start remembering.")
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

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        ForEach(Array(groups.enumerated()), id: \.element["id"].text) { _, group in
            if group["recording_count"].integer == 1 {
                SourceList(sources: sourcesWithHints(group["sources"].array, timeline: timeline), onCorrect: onCorrect)
            } else {
                TimelineRecordingSession(group: group, day: day, query: query, zoneID: zoneID, tagID: tagID, timeline: timeline, onCorrect: onCorrect)
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
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var expanded = false
    @State private var requestID = UUID()
    @State private var sources: [JSONValue] = []
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
            SourceList(sources: sourcesWithHints(sources, timeline: timeline), onCorrect: { Task { await load(reset: true) }; onCorrect?() })
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
                Text(L10n.text("\(time(group["started_at"].text))–\(time(group["ended_at"].text)) · \(group["recording_count"].integer) recordings"))
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
        } catch { if !Task.isCancelled, requestID == token { self.error = L10n.error(error) } }
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

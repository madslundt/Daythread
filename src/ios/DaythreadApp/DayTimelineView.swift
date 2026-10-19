import SwiftUI
import OmiCore

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


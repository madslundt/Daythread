import SwiftUI
import OmiCore

extension JSONValue {
    var originalTopicSource: RecapOriginalTopics.Source {
        .init(id: self["id"].text, sourceVersion: self["source_version"].text,
              transcriptVersion: self["transcript_version"].text, text: self["text"].text,
              sections: self["sections"].array.map {
                .init(title: $0["title"].text, chapterID: $0["chapter_id"].text,
                      start: $0["start_offset"].integer, end: $0["end_offset"].integer)
              })
    }
}

/// These are original section titles and passages, with no generated recap prose.
struct RecapOriginalTopicRow: View {
    let topic: RecapOriginalTopics.Topic
    let day: String
    let onCorrect: (() -> Void)?
    var filters = DayFilterScope()
    var scopeFingerprint = ""
    @Binding var expanded: Bool
    let onFocus: (RecapTopicFocus) -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @Environment(\.locale) private var interfaceLocale
    @State private var sources: [JSONValue] = []
    @State private var loading = false
    @State private var error: String?
    @State private var refresh = UUID()
    @State private var selected: OriginalTopicRecording?
    @State private var showingOriginals = false
    @State private var validatedKey = ""
    private var key: String {
        model.archiveIdentity + model.archiveRevision + filters.identity + scopeFingerprint + topic.id + topic.parts.map {
            $0.sourceVersion + $0.transcriptVersion + $0.id
        }.joined() + refresh.uuidString + String(describing: phase)
    }
    private var verifiedSources: [JSONValue] { validatedKey == key ? sources : [] }

    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            RecapTopicHeader(id: "original:" + topic.id, title: topic.title, preview: L10n.text("Original passages"), expanded: $expanded)
            if expanded {
                if loading { ProgressView(L10n.text("Checking original evidence…")) }
                ErrorNote(message: error)
                if error != nil { Button(L10n.text("Retry original evidence")) { refresh = UUID() } }
                if !verifiedSources.isEmpty {
                    DisclosureGroup(showingOriginals ? L10n.text("Hide original passages") : L10n.text("Show original passages"), isExpanded: $showingOriginals) {
                        ForEach(topic.parts) { part in
                            if let source = verifiedSources.first(where: { $0["id"].text == part.sourceID }) {
                                VStack(alignment: .leading, spacing: 12) {
                                    Text(time(source)).font(.caption).foregroundStyle(.secondary)
                                    SourceTranscriptView(source: source, editing: false, startOffset: part.start, endOffset: part.end)
                                    if source["audio_available"] != .bool(false),
                                       let section = source["sections"].array.first(where: { $0["start_offset"].integer == part.start && $0["end_offset"].integer == part.end }),
                                       case .number(let seconds) = section["start_seconds"], seconds.isFinite, seconds >= 0 {
                                        Button { selected = OriginalTopicRecording(source: source, startAt: seconds) } label: {
                                            Label(L10n.text("Listen from this topic"), systemImage: "gobackward")
                                        }
                                    }
                                    Button { selected = OriginalTopicRecording(source: source, startAt: nil) } label: {
                                        Label(L10n.text("Full recording & tools"), systemImage: "waveform")
                                    }
                                }.padding(.vertical, 8)
                            }
                        }
                    }.accessibilityIdentifier("recap.originalPassages.original:" + topic.id)
                    if !filters.isFiltered {
                        NavigationLink {
                            ChatView(initialScope: .day(day), initialQuestion: L10n.text("Recap this topic from this day: ") + topic.title)
                        } label: { Label(L10n.text("Ask about this topic"), systemImage: "bubble.left.and.bubble.right") }
                        Text(L10n.text("Chat searches this day, guided by your topic question.")).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .task(id: key + String(expanded)) { if expanded { await load() } }
        .onChange(of: expanded) { _, value in if !value { showingOriginals = false; sources = []; validatedKey = "" } }
        .sheet(item: $selected) { recording in
            SourceDetail(source: recording.source, onCorrect: { sources = []; refresh = UUID(); onCorrect?() }, startAt: recording.startAt)
        }
    }

    private func load() async {
        guard phase == .active else { sources = []; selected = nil; return }
        let requested = key
        loading = true; error = nil; sources = []; validatedKey = ""
        onFocus(.init(id: "original:" + topic.id, title: topic.title))
        defer { if key == requested { loading = false } }
        do {
            let client = try model.client()
            try await filters.validateSources(Set(topic.parts.map(\.sourceID)), day: day, fingerprint: scopeFingerprint, client: client)
            var incoming: [JSONValue] = []
            for id in Set(topic.parts.map(\.sourceID)).sorted() {
                incoming.append(try await client.request("v1/transcripts/\(id)"))
            }
            guard !Task.isCancelled, expanded, key == requested else { return }
            for part in topic.parts {
                guard let source = incoming.first(where: { $0["id"].text == part.sourceID }),
                      source["state"].text == "done",
                      RecapOriginalTopics.matchesCurrent(part, source: source.originalTopicSource) else {
                    throw BackendFailure(message: L10n.text("Recorded evidence changed or expired; reload and review it"))
                }
            }
            sources = incoming; validatedKey = requested
            onFocus(.init(id: "original:" + topic.id, title: topic.title,
                sourceIDs: Set(incoming.map { $0["id"].text }),
                passages: topic.parts.map { .init(sourceID: $0.sourceID, start: $0.start, end: $0.end) },
                versions: Dictionary(incoming.map { ($0["id"].text, $0["source_version"].text) }, uniquingKeysWith: { _, value in value })))
        } catch { if !Task.isCancelled, expanded, key == requested { self.error = L10n.error(error) } }
    }
    private func time(_ source: JSONValue) -> String {
        guard let date = AppModel.date(source["started_at"].text) else { return source["started_at"].text }
        return DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current).label(date, template: "d MMM yyyy HHmm")
    }
}

private struct OriginalTopicRecording: Identifiable {
    let id = UUID()
    let source: JSONValue
    let startAt: TimeInterval?
}

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
struct RecapOriginalTopicDetail: View {
    let topic: RecapOriginalTopics.Topic
    let day: String
    let onCorrect: (() -> Void)?
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @Environment(\.locale) private var interfaceLocale
    @State private var sources: [JSONValue] = []
    @State private var loading = false
    @State private var error: String?
    @State private var refresh = UUID()
    @State private var selected: OriginalTopicRecording?
    private var key: String {
        model.archiveIdentity + model.archiveRevision + topic.id + topic.parts.map {
            $0.sourceVersion + $0.transcriptVersion + $0.id
        }.joined() + refresh.uuidString + String(describing: phase)
    }

    var body: some View {
        let _ = interfaceLocale
        List {
            Section(L10n.text("Original passages")) {
                if loading { ProgressView(L10n.text("Checking original evidence…")) }
                ErrorNote(message: error)
                if error != nil { Button(L10n.text("Retry original evidence")) { refresh = UUID() } }
                ForEach(topic.parts) { part in
                    if let source = sources.first(where: { $0["id"].text == part.sourceID }) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(time(source)).font(.caption).foregroundStyle(.secondary)
                            SourceTranscriptView(source: source, editing: false, startOffset: part.start, endOffset: part.end)
                            if source["audio_available"] != .bool(false),
                               let section = source["sections"].array.first(where: { $0["start_offset"].integer == part.start && $0["end_offset"].integer == part.end }),
                               case .number(let seconds) = section["start_seconds"], seconds.isFinite, seconds >= 0 {
                                Button {
                                    selected = OriginalTopicRecording(source: source, startAt: seconds)
                                } label: { Label(L10n.text("Listen from this topic"), systemImage: "gobackward") }
                            }
                            Button { selected = OriginalTopicRecording(source: source, startAt: nil) } label: {
                                Label(L10n.text("Full recording & tools"), systemImage: "waveform")
                            }
                        }.padding(.vertical, 8)
                    }
                }
                if !sources.isEmpty {
                    NavigationLink {
                        ChatView(initialScope: .day(day), initialQuestion: L10n.text("Recap this topic from this day: ") + topic.title)
                    } label: { Label(L10n.text("Ask about this topic"), systemImage: "bubble.left.and.bubble.right") }
                    Text(L10n.text("Chat searches this day, guided by your topic question.")).font(.caption).foregroundStyle(.secondary)
                }
            }
            if !loading && error == nil && !sources.isEmpty {
                DayAtGlanceSections(day: day, topicSources: Set(sources.map { $0["id"].text }),
                    topicPassages: topic.parts.map { .init(sourceID: $0.sourceID, start: $0.start, end: $0.end) },
                    topicVersions: Dictionary(sources.map { ($0["id"].text, $0["source_version"].text) }, uniquingKeysWith: { _, latest in latest }))
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle(topic.title).navigationBarTitleDisplayMode(.inline)
            .task(id: key) { await load() }
            .refreshable { refresh = UUID() }
            .sheet(item: $selected) { recording in
                SourceDetail(source: recording.source, onCorrect: { refresh = UUID(); onCorrect?() }, startAt: recording.startAt)
            }
    }

    private func load() async {
        guard phase == .active else { sources = []; selected = nil; return }
        let requested = key
        loading = true; error = nil; sources = []
        defer { if key == requested { loading = false } }
        do {
            var incoming: [JSONValue] = []
            for id in Set(topic.parts.map(\.sourceID)).sorted() {
                incoming.append(try await model.client().request("v1/transcripts/\(id)"))
            }
            guard !Task.isCancelled, key == requested else { return }
            for part in topic.parts {
                guard let source = incoming.first(where: { $0["id"].text == part.sourceID }),
                      source["state"].text == "done",
                      RecapOriginalTopics.matchesCurrent(part, source: source.originalTopicSource) else {
                    throw BackendFailure(message: L10n.text("Recorded evidence changed or expired; reload and review it"))
                }
            }
            sources = incoming
        } catch { if !Task.isCancelled, key == requested { self.error = L10n.error(error) } }
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

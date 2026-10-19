import SwiftUI
import OmiCore

struct RecapTopicFocus: Equatable {
    let id: String
    let title: String
    var sourceIDs: Set<String> = []
    var passages: [InboxTopicPassage] = []
    var versions: [String: String] = [:]
}

struct RecapTopicHeader: View {
    let id: String
    let title: String
    let preview: String
    @Binding var expanded: Bool
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        Button { expanded.toggle() } label: {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(title).font(.headline).foregroundStyle(.primary)
                    if !expanded { Text(.init(preview)).font(.subheadline).foregroundStyle(.secondary).lineLimit(3) }
                }.frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    .rotationEffect(.degrees(expanded ? 90 : 0)).accessibilityHidden(true)
            }.contentShape(Rectangle()).padding(.vertical, 6)
        }.buttonStyle(.plain).accessibilityIdentifier("recap.topic." + id)
            .accessibilityValue(expanded ? L10n.text("Expanded") : L10n.text("Collapsed"))
    }
}

struct RecapTopicRow: View {
    let topic: RecapTranslationMapping.Topic
    let display: RecapPresentation.Group
    let sources: [JSONValue]
    let day: String
    let filters: DayFilterScope
    let scopeFingerprint: String
    @Binding var expanded: Bool
    var onCorrect: (() -> Void)?
    let onFocus: (RecapTopicFocus) -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @Environment(\.locale) private var interfaceLocale
    @State private var currentSources: [JSONValue] = []
    @State private var loading = false
    @State private var error: String?
    @State private var refresh = UUID()
    @State private var showingOriginals = false
    @State private var validatedKey = ""
    private var title: String { (topic.original.title ?? L10n.text("Day recap")).replacingOccurrences(of: "**", with: "") }
    private var displayTitle: String { (display.title ?? L10n.text("Day recap")).replacingOccurrences(of: "**", with: "") }
    private var point: String { topic.original.parts.map(\.text).joined(separator: "\n") }
    private var related: RecapPresentation.Matches {
        RecapPresentation.sources(for: "**\(title)**\n\(point)", in: sources.map { .init(id: $0["id"].text, text: $0["text"].text) })
    }
    private var key: String { model.archiveIdentity + model.archiveRevision + filters.identity + scopeFingerprint + refresh.uuidString + related.ids.joined() }
    private var verifiedSources: [JSONValue] { validatedKey == key ? currentSources : [] }

    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            RecapTopicHeader(id: topic.id, title: displayTitle, preview: RecapPresentation.topicSummary(display), expanded: $expanded)
            if expanded {
                if !filters.isFiltered || validatedKey == key {
                    Text(.init(RecapPresentation.topicSummary(display))).textSelection(.enabled)
                }
                if loading { ProgressView(L10n.text("Checking original evidence…")) }
                ErrorNote(message: error)
                if error != nil { Button(L10n.text("Retry original evidence")) { refresh = UUID() } }
                if related.ids.isEmpty { Text(L10n.text("No linked or matching recordings for this topic.")).font(.caption).foregroundStyle(.secondary) }
                if !verifiedSources.isEmpty {
                    DisclosureGroup(showingOriginals ? L10n.text("Hide original passages") : L10n.text("Show original passages"), isExpanded: $showingOriginals) {
                        Text(related.basis == .citation ? L10n.text("Cited recordings · check the original context.") : L10n.text("Possible matching recordings · relevance is inferred from shared words."))
                            .font(.caption).foregroundStyle(.secondary)
                        ForEach(verifiedSources, id: \.["id"].text) { source in
                            RecapEvidenceView(source: source, point: point, topic: title, onCorrect: { currentSources = []; refresh = UUID(); onCorrect?() })
                        }
                    }.accessibilityIdentifier("recap.originalPassages." + topic.id)
                }
                if !filters.isFiltered {
                    NavigationLink {
                        ChatView(initialScope: .day(day), initialQuestion: L10n.text("Recap this topic from this day: ") + title)
                    } label: { Label(L10n.text("Ask about this topic"), systemImage: "bubble.left.and.bubble.right") }
                    Text(L10n.text("Chat searches this day, guided by your topic question.")).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .task(id: key + String(expanded) + String(describing: phase)) { if expanded && phase == .active { await load() } }
        .onChange(of: expanded) { _, value in if !value { showingOriginals = false; currentSources = []; validatedKey = "" } }
    }

    private func load() async {
        let requested = key
        loading = true; error = nil; currentSources = []; validatedKey = ""
        onFocus(.init(id: topic.id, title: title))
        defer { if key == requested { loading = false } }
        do {
            let client = try model.client()
            try await filters.validateSources(Set(related.ids), day: day, fingerprint: scopeFingerprint, client: client)
            var incoming: [JSONValue] = []
            for id in related.ids { incoming.append(try await client.request("v1/transcripts/\(id)")) }
            guard !Task.isCancelled, expanded, key == requested else { return }
            currentSources = incoming; validatedKey = requested
            let passages = incoming.flatMap { source in
                RecapPresentation.sections(for: point, topic: title, text: source["text"].text,
                    sections: source["sections"].array.map { .init(title: $0["title"].text, start: $0["start_offset"].integer, end: $0["end_offset"].integer) })
                    .map { InboxTopicPassage(sourceID: source["id"].text, start: $0.start, end: $0.end) }
            }
            onFocus(.init(id: topic.id, title: title, sourceIDs: Set(incoming.map { $0["id"].text }), passages: passages,
                          versions: Dictionary(uniqueKeysWithValues: incoming.map { ($0["id"].text, $0["source_version"].text) })))
        } catch { if !Task.isCancelled, expanded, key == requested { self.error = L10n.error(error) } }
    }
}

private struct RecapEvidenceView: View {
    let source: JSONValue
    let point: String
    let topic: String
    let onCorrect: (() -> Void)?
    @Environment(AppModel.self) private var model
    @State private var selected: SourceSelection?
    @State private var selectedStart: TimeInterval?
    @State private var fresh: JSONValue?
    @State private var error: String?
    @State private var loading = false
    @State private var refresh = UUID()
    private var evidence: JSONValue { fresh ?? source }
    private var sections: [RecapPresentation.Section] {
        RecapPresentation.sections(for: point, topic: topic, text: evidence["text"].text,
            sections: evidence["sections"].array.map { .init(title: $0["title"].text, start: $0["start_offset"].integer, end: $0["end_offset"].integer) })
    }
    private var identity: String { [model.archiveIdentity, model.archiveRevision, source["id"].text, source["source_version"].text, refresh.uuidString].joined(separator: "|") }
    private var time: String {
        guard let date = AppModel.date(evidence["started_at"].text) else { return evidence["started_at"].text }
        return DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current).label(date, template: "d MMM HHmm")
    }
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            Text(time).font(.caption).foregroundStyle(.secondary)
            Text(sections.count == 1 ? sections[0].title : evidence["title"].text.isEmpty ? L10n.text("Recording") : evidence["title"].text).font(.headline)
            if loading { ProgressView(L10n.text("Checking original evidence…")) }
            ErrorNote(message: error)
            if error != nil { Button(L10n.text("Retry original evidence")) { refresh = UUID() } }
            if fresh != nil && evidence != .null {
                if sections.isEmpty {
                    Text(L10n.text("No specific topic passage identified. Open the full recording to inspect its context."))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    ForEach(sections, id: \.start) { section in
                        SourceTranscriptView(source: evidence, editing: false, startOffset: section.start, endOffset: section.end)
                            .padding(.leading, 12).overlay(alignment: .leading) { Rectangle().fill(Theme.accent.opacity(0.3)).frame(width: 3) }
                        if evidence["audio_available"] != .bool(false),
                           let chapter = evidence["sections"].array.first(where: { $0["start_offset"].integer == section.start }),
                           case .number(let seconds) = chapter["start_seconds"], seconds.isFinite, seconds >= 0 {
                            Button {
                                selectedStart = seconds; selected = SourceSelection(source: evidence)
                            } label: { Label(L10n.text("Listen from this topic"), systemImage: "gobackward") }
                        }
                    }
                }
                ImportanceButton(target: .init(kind: "source", id: evidence["id"].text), label: "recording")
                Button { selectedStart = nil; selected = SourceSelection(source: evidence) } label: { Label(L10n.text("Full recording & tools"), systemImage: "waveform") }
                    .buttonStyle(.borderless)
            }
        }.padding(.vertical, 10)
            .sheet(item: $selected) { selection in
                SourceDetail(source: selection.source, onCorrect: { refresh = UUID(); onCorrect?() }, startAt: selectedStart)
            }
            .task(id: identity) {
                let requested = identity
                loading = true; error = nil; fresh = nil
                defer { if requested == identity { loading = false } }
                do {
                    let result = try await model.client().request("v1/transcripts/\(source["id"].text)")
                    guard !Task.isCancelled, requested == identity else { return }
                    fresh = result
                } catch {
                    guard !Task.isCancelled, requested == identity else { return }
                    self.error = L10n.error(error)
                    if (error as? BackendFailure)?.status == 404 { fresh = .null }
                }
            }
    }
}


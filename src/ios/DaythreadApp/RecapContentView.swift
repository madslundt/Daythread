import SwiftUI
import OmiCore

struct RecapContentView: View {
    let recap: JSONValue
    let day: String
    var onCorrect: (() -> Void)? = nil
    var emptyMessage: String? = nil
    var filters = DayFilterScope()
    private var effectiveFilters: DayFilterScope {
        filters.isFiltered ? filters : .init(tagID: recap["scope"]["tag_id"].text, zoneID: recap["scope"]["zone_id"].text)
    }
    @State private var exploring = false
    @State private var query = ""
    @Environment(\.locale) private var interfaceLocale
    private var groups: [RecapPresentation.Group] {
        RecapPresentation.topics(recap["summary"].text, day: day) +
        RecapPresentation.groups(recap["summary"].text, day: day).filter(\.hasActions)
    }
    private var topics: [RecapPresentation.Group] { RecapPresentation.topics(recap["summary"].text, day: day, query: query) }
    private var originalTopics: [RecapOriginalTopics.Topic] {
        let sources = recap["sources"].array
        let sourceText = sources.map { RecapPresentation.Source(id: $0["id"].text, text: $0["text"].text) }
        var covered: [InboxTopicPassage] = []
        for group in RecapPresentation.topics(recap["summary"].text, day: day) {
            let title = (group.title ?? "").replacingOccurrences(of: "**", with: "")
            let point = group.parts.map(\.text).joined(separator: "\n")
            let related = RecapPresentation.sources(for: "**\(title)**\n\(point)", in: sourceText)
            for source in sources where related.ids.contains(source["id"].text) {
                covered += RecapPresentation.sections(for: point, topic: title, text: source["text"].text,
                    sections: source["sections"].array.map { .init(title: $0["title"].text, start: $0["start_offset"].integer, end: $0["end_offset"].integer) })
                    .map { InboxTopicPassage(sourceID: source["id"].text, start: $0.start, end: $0.end) }
            }
        }
        return RecapOriginalTopics.topics(sources: sources.map(\.originalTopicSource), excluding: covered, query: query)
    }

    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 18) {
            Picker(L10n.text("Recap view"), selection: $exploring) {
                Text(L10n.text("Day overview")).tag(false)
                Text(L10n.text("Explore topics")).tag(true)
            }.pickerStyle(.segmented).accessibilityIdentifier("recap.mode")
            if exploring { topicBrowser }
            else {
                if recap["summary"].text.isEmpty {
                    Text(emptyMessage ?? L10n.text("No useful recap content in transcribed recordings yet. Your recordings remain available.")).font(.subheadline).foregroundStyle(.secondary)
                } else {
                    if effectiveFilters.isFiltered { RecapGroupSummaries(groups: groups) }
                    else { SummaryTranslationView(output: recap) { RecapGroupSummaries(groups: groups) } }
                }
                if !effectiveFilters.isFiltered {
                    NavigationLink { ChatView(initialScope: .day(day)) } label: {
                        Label(L10n.text("Ask about this day"), systemImage: "bubble.left.and.bubble.right")
                    }
                }
                Button { exploring = true } label: {
                    Label(L10n.text("Explore topics"), systemImage: "text.magnifyingglass")
                }
            }
        }.buttonStyle(.borderless).disclosureGroupStyle(RecapDisclosureStyle())
    }

    private var topicBrowser: some View {
        let originals = originalTopics
        return VStack(alignment: .leading, spacing: 14) {
            Text(L10n.text("Choose a topic to revisit its recap and original conversation.")).font(.subheadline).foregroundStyle(.secondary)
            TextField(L10n.text("Search recap topics"), text: $query).textFieldStyle(.roundedBorder)
                .accessibilityIdentifier("recap.topicSearch")
            if topics.isEmpty && originals.isEmpty {
                Text(L10n.text("No matching recap topics. Try another word or browse the day’s recordings.")).font(.subheadline).foregroundStyle(.secondary)
            }
            ForEach(Array(topics.enumerated()), id: \.offset) { _, group in
                NavigationLink {
                    RecapTopicDetail(group: group, sources: recap["sources"].array, day: day, onCorrect: onCorrect, filters: effectiveFilters, scopeFingerprint: recap["scope_fingerprint"].text)
                } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        Text((group.title ?? L10n.text("Day recap")).replacingOccurrences(of: "**", with: "")).font(.headline).foregroundStyle(.primary)
                        Text(RecapPresentation.topicSummary(group).replacingOccurrences(of: "**", with: "")).font(.subheadline).foregroundStyle(.secondary).lineLimit(3)
                    }.padding(.vertical, 6)
                }
            }
            ForEach(originals) { topic in
                NavigationLink {
                    RecapOriginalTopicDetail(topic: topic, day: day, onCorrect: onCorrect, filters: effectiveFilters, scopeFingerprint: recap["scope_fingerprint"].text)
                } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(topic.title).font(.headline).foregroundStyle(.primary)
                        Text(L10n.text("Original passages")).font(.caption).foregroundStyle(.secondary)
                    }.padding(.vertical, 6)
                }
            }
        }
    }
}

private struct RecapGroupSummaries: View {
    let groups: [RecapPresentation.Group]
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            ForEach(Array(groups.enumerated()), id: \.offset) { _, group in
                VStack(alignment: .leading, spacing: 8) {
                    if group.hasActions { Text(L10n.text("Follow-ups mentioned in the recap")).font(.headline) }
                    else if let title = group.title {
                        Text(RecapPresentation.readingText(title).replacingOccurrences(of: "**", with: "")).font(.headline)
                    }
                    RecapSummaryText(text: RecapPresentation.topicSummary(group))
                    if group.hasActions { Text(L10n.text("Review and manage these in the overview below.")).font(.caption).foregroundStyle(.secondary) }
                }
            }
        }
    }
}

private struct RecapTopicDetail: View {
    let group: RecapPresentation.Group
    let sources: [JSONValue]
    let day: String
    let onCorrect: (() -> Void)?
    let filters: DayFilterScope
    let scopeFingerprint: String
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @Environment(\.locale) private var interfaceLocale
    @State private var currentSources: [JSONValue] = []
    @State private var loading = false
    @State private var error: String?
    @State private var refresh = UUID()
    @State private var showingOriginals = false
    @State private var validatedKey = ""
    private var title: String { (group.title ?? L10n.text("Day recap")).replacingOccurrences(of: "**", with: "") }
    private var point: String { group.parts.map(\.text).joined(separator: "\n") }
    private var related: RecapPresentation.Matches {
        RecapPresentation.sources(for: "**\(title)**\n\(point)", in: sources.map { .init(id: $0["id"].text, text: $0["text"].text) })
    }
    private var verifiedSources: [JSONValue] { validatedKey == loadKey ? currentSources : [] }
    private var passages: [InboxTopicPassage] {
        verifiedSources.flatMap { source in
            RecapPresentation.sections(for: point, topic: title, text: source["text"].text,
                sections: source["sections"].array.map { .init(title: $0["title"].text, start: $0["start_offset"].integer, end: $0["end_offset"].integer) })
                .map { InboxTopicPassage(sourceID: source["id"].text, start: $0.start, end: $0.end) }
        }
    }
    private var loadKey: String { model.archiveIdentity + model.archiveRevision + filters.identity + scopeFingerprint + refresh.uuidString + related.ids.joined() }

    var body: some View {
        let _ = interfaceLocale
        List {
            Section(L10n.text("Topic recap")) {
                if !filters.isFiltered || validatedKey == loadKey {
                    RecapSummaryText(text: RecapPresentation.topicSummary(group))
                }
                if !filters.isFiltered {
                NavigationLink {
                    ChatView(initialScope: .day(day), initialQuestion: L10n.text("Recap this topic from this day: ") + title)
                } label: { Label(L10n.text("Ask about this topic"), systemImage: "bubble.left.and.bubble.right") }
                Text(L10n.text("Chat searches this day, guided by your topic question.")).font(.caption).foregroundStyle(.secondary)
                }
            }
            Section(L10n.text("Revisit the conversation")) {
                if loading { ProgressView(L10n.text("Checking original evidence…")) }
                ErrorNote(message: error)
                if error != nil { Button(L10n.text("Retry original evidence")) { refresh = UUID() } }
                if related.ids.isEmpty { Text(L10n.text("No linked or matching recordings for this topic.")).foregroundStyle(.secondary) }
                if !verifiedSources.isEmpty {
                    DisclosureGroup(showingOriginals ? L10n.text("Hide original passages") : L10n.text("Show original passages"), isExpanded: $showingOriginals) {
                        Text(related.basis == .citation ? L10n.text("Cited recordings · check the original context.") : L10n.text("Possible matching recordings · relevance is inferred from shared words."))
                            .font(.caption).foregroundStyle(.secondary)
                        ForEach(verifiedSources, id: \.["id"].text) { source in
                            RecapEvidenceView(source: source, point: point, topic: title, onCorrect: { currentSources = []; refresh = UUID(); onCorrect?() })
                        }
                    }.accessibilityIdentifier("recap.originalPassages")
                }
            }
            if !loading && error == nil && validatedKey == loadKey && !related.ids.isEmpty {
                DayAtGlanceSections(day: day, topicSources: Set(verifiedSources.map { $0["id"].text }), topicPassages: passages, topicVersions: Dictionary(uniqueKeysWithValues: verifiedSources.map { ($0["id"].text, $0["source_version"].text) }), filters: filters)
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle(title).navigationBarTitleDisplayMode(.inline)
            .disclosureGroupStyle(RecapDisclosureStyle())
            .task(id: loadKey) { await loadSources() }
            .onChange(of: phase) { _, value in if value == .active { refresh = UUID() } }
            .refreshable { refresh = UUID() }
    }

    private func loadSources() async {
        let key = loadKey
        loading = true; error = nil; currentSources = []; validatedKey = ""
        defer { if key == loadKey { loading = false } }
        do {
            let client = try model.client()
            try await filters.validateSources(Set(related.ids), day: day, fingerprint: scopeFingerprint, client: client)
            var incoming: [JSONValue] = []
            for id in related.ids { incoming.append(try await client.request("v1/transcripts/\(id)")) }
            guard !Task.isCancelled, key == loadKey else { return }
            currentSources = incoming; validatedKey = key
        } catch { if !Task.isCancelled, key == loadKey { self.error = L10n.error(error) } }
    }
}

/// Summary expansion never changes the independent original-passage disclosure.
private struct RecapSummaryText: View {
    let text: String
    @State private var expanded = false
    @State private var fullHeight: CGFloat = 0
    @State private var previewHeight: CGFloat = 0
    private var summary: Text { Text(.init(text)) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            summary.lineLimit(expanded ? nil : 3).textSelection(.enabled)
                .background(alignment: .topLeading) {
                    summary.lineLimit(3).fixedSize(horizontal: false, vertical: true).hidden()
                        .background(GeometryReader { proxy in Color.clear.preference(key: RecapPreviewHeight.self, value: proxy.size.height) })
                }
                .overlay(alignment: .topLeading) {
                    summary.fixedSize(horizontal: false, vertical: true).hidden()
                        .background(GeometryReader { proxy in Color.clear.preference(key: RecapFullHeight.self, value: proxy.size.height) })
                        .accessibilityHidden(true).allowsHitTesting(false)
                }
            if expanded || fullHeight > previewHeight + 1 {
                Button(expanded ? L10n.text("Show less") : L10n.text("Read more")) { expanded.toggle() }
                    .font(.subheadline.weight(.semibold)).accessibilityIdentifier("recap.summaryExpansion")
            }
        }
        .onPreferenceChange(RecapFullHeight.self) { fullHeight = $0 }
        .onPreferenceChange(RecapPreviewHeight.self) { previewHeight = $0 }
        .onChange(of: text) { _, _ in expanded = false }
    }
}

private struct RecapFullHeight: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

private struct RecapPreviewHeight: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
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

struct RecapDisclosureStyle: DisclosureGroupStyle {
    func makeBody(configuration: Configuration) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.18)) { configuration.isExpanded.toggle() }
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    configuration.label.frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                        .rotationEffect(.degrees(configuration.isExpanded ? 90 : 0))
                        .accessibilityHidden(true)
                }.contentShape(Rectangle())
            }.buttonStyle(.plain)
                .accessibilityValue(configuration.isExpanded ? L10n.text("Expanded") : L10n.text("Collapsed"))
            if configuration.isExpanded { configuration.content }
        }
    }
}

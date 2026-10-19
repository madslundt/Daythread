import SwiftUI
import OmiCore

struct RecapContentView: View {
    let recap: JSONValue
    let day: String
    var onCorrect: (() -> Void)? = nil
    var emptyMessage: String? = nil
    var filters = DayFilterScope()
    var onFocusChange: ((RecapTopicFocus?) -> Void)? = nil
    var onContentLanguageChange: ((String?) -> Void)? = nil
    @State private var query = ""
    @State private var expandedID: String?
    @Environment(\.locale) private var interfaceLocale
    private var effectiveFilters: DayFilterScope {
        filters.isFiltered ? filters : .init(tagID: recap["scope"]["tag_id"].text, zoneID: recap["scope"]["zone_id"].text)
    }
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
        return RecapOriginalTopics.topics(sources: sources.map(\.originalTopicSource), excluding: covered)
    }


    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 18) {
            if !effectiveFilters.isFiltered && !recap["summary"].text.isEmpty {
                SummaryTranslationView(output: recap, translationIsCompatible: { translatedMapping($0) != nil }, onContentLanguageChange: onContentLanguageChange, renderedContent: { translation, showing in
                    topicList(translation: translation, showingTranslation: showing)
                })
            } else { topicList(translation: nil, showingTranslation: false) }
            if !effectiveFilters.isFiltered {
                NavigationLink { ChatView(initialScope: .day(day)) } label: {
                    Label(L10n.text("Ask about this day"), systemImage: "bubble.left.and.bubble.right")
                }
            }
        }.buttonStyle(.borderless).disclosureGroupStyle(RecapDisclosureStyle())
            .onChange(of: query) { _, _ in select(nil, title: "") }
            .onChange(of: recap["summary"]) { _, _ in query = ""; select(nil, title: "") }
            .onDisappear { onFocusChange?(nil); onContentLanguageChange?(nil) }
    }

    private func topicList(translation: JSONValue?, showingTranslation: Bool) -> RecapTopicsContent {
        let original = RecapTranslationMapping.original(recap["summary"].text, day: day)
        let translated = translation.flatMap(translatedMapping)
        return RecapTopicsContent(original: original, translated: translated, showingTranslation: showingTranslation,
            mappingUnavailable: translation != nil && translated == nil, originals: originalTopics,
            contentLanguage: showingTranslation ? translation?["language"].text : nil,
            recap: recap, day: day, filters: effectiveFilters, emptyMessage: emptyMessage,
            query: $query, expandedID: expandedID, onSelect: select, onCorrect: onCorrect,
            onFocus: { focus in if expandedID == focus.id { onFocusChange?(focus) } })
    }

    private func translatedMapping(_ value: JSONValue) -> RecapTranslationMapping.Mapping? {
        guard let structure = try? JSONDecoder().decode(RecapTranslationMapping.Structure.self,
            from: JSONEncoder().encode(value["structure"])) else { return nil }
        return RecapTranslationMapping.map(original: recap["summary"].text, translated: value["text"].text,
            structure: structure, summaryVersion: value["summary_version"].text, day: day)
    }

    private func select(_ id: String?, title: String) {
        expandedID = id
        onFocusChange?(id.map { RecapTopicFocus(id: $0, title: title) })
    }
}

private struct RecapTopicsContent: View {
    let original: RecapTranslationMapping.Mapping
    let translated: RecapTranslationMapping.Mapping?
    let showingTranslation: Bool
    let mappingUnavailable: Bool
    let originals: [RecapOriginalTopics.Topic]
    let contentLanguage: String?
    let recap: JSONValue
    let day: String
    let filters: DayFilterScope
    let emptyMessage: String?
    @Binding var query: String
    let expandedID: String?
    let onSelect: (String?, String) -> Void
    let onCorrect: (() -> Void)?
    let onFocus: (RecapTopicFocus) -> Void
    @State private var showingFollowUps = false
    @State private var labelState = DisplayLabelTranslationState()
    @State private var labelsRefresh = UUID()
    @State private var canonicalFocus: RecapTopicFocus?
    @Environment(AppModel.self) private var model
    @Environment(\.locale) private var interfaceLocale
    private var visibleTopics: [RecapTranslationMapping.Topic] {
        let search = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return original.topics.filter { topic in
            let display = translated?.topics.first { $0.id == topic.id }?.display
            let text = (topic.original.title ?? "") + " " + topic.original.text + " " + (display?.title ?? "") + " " + (display?.text ?? "")
            return topic.id == expandedID || search.isEmpty || text.range(of: search, options: [.caseInsensitive, .diacriticInsensitive]) != nil
        }
    }
    private var followUps: [RecapPresentation.Group] { showingTranslation ? translated?.followUps ?? original.followUps : original.followUps }
    private var labels: [DisplayTranslations.Label] { originals.compactMap(\.displayLabel) }
    private var labelsKey: String {
        model.archiveIdentity + model.archiveRevision + filters.identity + recap["scope_fingerprint"].text + labelsRefresh.uuidString + DisplayTranslations.identity(language: contentLanguage, labels: labels)
    }
    private var labelValues: [DisplayTranslations.Reference: String] { labelState.values(for: labelsKey) }
    private func originalTitle(_ topic: RecapOriginalTopics.Topic) -> String {
        topic.displayLabel.flatMap { labelValues[$0.reference] } ?? topic.title
    }
    private var visibleOriginals: [RecapOriginalTopics.Topic] {
        let search = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return originals.filter { topic in
            let text = topic.title + " " + originalTitle(topic) + " " + topic.parts.map(\.quote).joined(separator: " ")
            return "original:" + topic.id == expandedID || search.isEmpty || text.range(of: search, options: [.caseInsensitive, .diacriticInsensitive]) != nil
        }
    }
    private var projectedFocus: RecapTopicFocus? {
        guard var focus = canonicalFocus, expandedID == focus.id else { return nil }
        if showingTranslation, let topic = translated?.topics.first(where: { $0.id == focus.id }) {
            focus.displayTitle = topic.display.title?.replacingOccurrences(of: "**", with: "")
        } else if let topic = originals.first(where: { "original:" + $0.id == focus.id }) {
            focus.displayTitle = originalTitle(topic)
        }
        return focus
    }
    private func select(_ id: String?, title: String) { canonicalFocus = nil; onSelect(id, title) }
    private func acceptFocus(_ focus: RecapTopicFocus) { if focus.id == expandedID { canonicalFocus = focus } }

    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 14) {
            TextField(L10n.text("Search recap topics"), text: $query).textFieldStyle(.roundedBorder)
                .accessibilityIdentifier("recap.topicSearch")
            if labelState.isLoading(key: labelsKey, language: contentLanguage, labels: labels) { ProgressView(L10n.text("Translating…")) }
            if let error = labelState.error(for: labelsKey) {
                ErrorNote(message: error)
                Button(L10n.text("Retry")) { labelsRefresh = UUID() }
            }
            if mappingUnavailable {
                Text(L10n.text("Update this translation to keep the recap’s topics and layout.")).font(.caption).foregroundStyle(.secondary)
            }
            if recap["summary"].text.isEmpty {
                Text(emptyMessage ?? L10n.text("No useful recap content in transcribed recordings yet. Your recordings remain available.")).font(.subheadline).foregroundStyle(.secondary)
            }
            if visibleTopics.isEmpty && visibleOriginals.isEmpty && !query.isEmpty {
                Text(L10n.text("No matching recap topics. Try another word or browse the day’s recordings.")).font(.subheadline).foregroundStyle(.secondary)
            }
            ForEach(visibleTopics) { topic in
                let title = (topic.original.title ?? L10n.text("Day recap")).replacingOccurrences(of: "**", with: "")
                let display = showingTranslation ? translated?.topics.first { $0.id == topic.id }?.display ?? topic.original : topic.original
                RecapTopicRow(topic: topic, display: display, sources: recap["sources"].array, day: day,
                    filters: filters, scopeFingerprint: recap["scope_fingerprint"].text,
                    expanded: Binding(get: { expandedID == topic.id }, set: { select($0 ? topic.id : nil, title: title) }),
                    onCorrect: onCorrect, onFocus: acceptFocus)
                Divider()
            }
            ForEach(visibleOriginals) { topic in
                RecapOriginalTopicRow(topic: topic, day: day, onCorrect: onCorrect, displayTitle: originalTitle(topic), filters: filters,
                    scopeFingerprint: recap["scope_fingerprint"].text,
                    expanded: Binding(get: { expandedID == "original:" + topic.id }, set: { select($0 ? "original:" + topic.id : nil, title: topic.title) }),
                    onFocus: acceptFocus)
                Divider()
            }
            if !followUps.isEmpty {
                DisclosureGroup(L10n.text("Follow-ups mentioned in the recap"), isExpanded: $showingFollowUps) {
                    ForEach(Array(followUps.enumerated()), id: \.offset) { _, group in Text(.init(group.text)).textSelection(.enabled) }
                    Text(L10n.text("Review and manage these in the overview below.")).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .task(id: labelsKey) { await labelState.load(key: labelsKey, language: contentLanguage, labels: labels, client: model.client) }
        .onChange(of: projectedFocus, initial: true) { _, focus in if let focus { onFocus(focus) } }
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

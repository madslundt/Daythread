import SwiftUI
import OmiCore

struct RecapTopicRequest: Equatable {
    let id = UUID()
    let title: String
}

private struct RecapTopicScrollPreference: PreferenceKey {
    static let defaultValue: [UUID: String] = [:]
    static func reduce(value: inout [UUID: String], nextValue: () -> [UUID: String]) {
        value.merge(nextValue()) { _, latest in latest }
    }
}

struct RecapContentView: View {
    let recap: JSONValue
    let day: String
    var onCorrect: (() -> Void)? = nil
    var emptyMessage: String? = nil
    var filters = DayFilterScope()
    var onFocusChange: ((RecapTopicFocus?) -> Void)? = nil
    var onContentLanguageChange: ((String?) -> Void)? = nil
    var requestedTopic: RecapTopicRequest? = nil
    var onTopicOpened: ((String) -> Void)? = nil
    @State private var openedRequestID: UUID?
    @State private var scrolledRequestID: UUID?
    @State private var query = ""
    @State private var browsingRecordings = false
    @State private var expandedID: String?
    @Environment(\.locale) private var interfaceLocale
    private var effectiveFilters: DayFilterScope {
        filters.isFiltered ? filters : .init(tagID: recap["scope"]["tag_id"].text, zoneID: recap["scope"]["zone_id"].text)
    }
    private var originalTopics: [RecapOriginalTopics.Topic] {
        RecapOriginalTopics.topics(sources: recap["sources"].array.map(\.originalTopicSource))
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
            .onChange(of: recap["summary"]) { _, _ in query = ""; select(nil, title: "") }
            .onDisappear { onFocusChange?(nil); onContentLanguageChange?(nil) }
            .task(id: (requestedTopic?.id.uuidString ?? "") + recap["summary"].text) {
                openRequestedTopic()
            }
            .onPreferenceChange(RecapTopicScrollPreference.self) { anchors in
                guard let request = requestedTopic, request.id == openedRequestID,
                      request.id != scrolledRequestID, let id = anchors[request.id] else { return }
                scrolledRequestID = request.id
                onTopicOpened?(id)
            }
    }

    private func topicList(translation: JSONValue?, showingTranslation: Bool) -> RecapTopicsContent {
        let original = RecapTranslationMapping.original(recap["summary"].text, day: day)
        let translated = translation.flatMap(translatedMapping)
        return RecapTopicsContent(original: original, translated: translated, showingTranslation: showingTranslation,
            mappingUnavailable: translation != nil && translated == nil, originals: originalTopics,
            contentLanguage: showingTranslation ? translation?["language"].text : nil,
            recap: recap, day: day, filters: effectiveFilters, emptyMessage: emptyMessage,
            query: Binding(get: { query }, set: { query = $0; select(nil, title: "") }), browsingRecordings: $browsingRecordings, expandedID: expandedID, requestedTopic: requestedTopic, onSelect: select, onCorrect: onCorrect,
            onFocus: { focus in if expandedID == focus.id { onFocusChange?(focus) } })
    }

    private func translatedMapping(_ value: JSONValue) -> RecapTranslationMapping.Mapping? {
        guard let structure = try? JSONDecoder().decode(RecapTranslationMapping.Structure.self,
            from: JSONEncoder().encode(value["structure"])) else { return nil }
        return RecapTranslationMapping.map(original: recap["summary"].text, translated: value["text"].text,
            structure: structure, summaryVersion: value["summary_version"].text, day: day)
    }

    private func openRequestedTopic() {
        guard let request = requestedTopic, request.id != openedRequestID,
              let id = RecapTranslationMapping.topicID(for: request.title,
                in: RecapTranslationMapping.original(recap["summary"].text, day: day)) else { return }
        query = ""; browsingRecordings = false
        select(id, title: request.title)
        openedRequestID = request.id
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
    @Binding var browsingRecordings: Bool
    let expandedID: String?
    let requestedTopic: RecapTopicRequest?
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
        guard !browsingRecordings else { return [] }
        let search = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return original.topics.filter { topic in
            let display = translated?.topics.first { $0.id == topic.id }?.display
            let text = (topic.original.title ?? "") + " " + topic.original.text + " " + (display?.title ?? "") + " " + (display?.text ?? "")
            return topic.id == expandedID || search.isEmpty || text.range(of: search, options: [.caseInsensitive, .diacriticInsensitive]) != nil
        }
    }
    private var followUps: [RecapPresentation.Group] { showingTranslation ? translated?.followUps ?? original.followUps : original.followUps }
    private var labels: [DisplayTranslations.Label] { browsingRecordings ? originals.compactMap(\.displayLabel) : [] }
    private var labelsKey: String {
        model.archiveIdentity + model.archiveRevision + filters.identity + recap["scope_fingerprint"].text + labelsRefresh.uuidString + DisplayTranslations.identity(language: contentLanguage, labels: labels)
    }
    private var labelValues: [DisplayTranslations.Reference: String] { labelState.values(for: labelsKey) }
    private func originalTitle(_ topic: RecapOriginalTopics.Topic) -> String {
        topic.displayLabel.flatMap { labelValues[$0.reference] } ?? topic.title
    }
    private var visibleOriginals: [RecapOriginalTopics.Topic] {
        guard browsingRecordings else { return [] }
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
            if !originals.isEmpty {
                Button {
                    browsingRecordings.toggle(); query = ""; select(nil, title: "")
                } label: {
                    Label(browsingRecordings ? L10n.text("Back to daily recap") : L10n.text("Browse all topics"),
                          systemImage: browsingRecordings ? "chevron.left" : "magnifyingglass")
                }.accessibilityIdentifier("recap.browseTopics")
            }
            TextField(browsingRecordings ? L10n.text("Find a topic in this day") : L10n.text("Search recap topics"), text: $query).textFieldStyle(.roundedBorder)
                .accessibilityIdentifier("recap.topicSearch")
            if labelState.isLoading(key: labelsKey, language: contentLanguage, labels: labels) { ProgressView(L10n.text("Translating…")) }
            if let error = labelState.error(for: labelsKey) {
                ErrorNote(message: error)
                Button(L10n.text("Retry")) { labelsRefresh = UUID() }
            }
            if mappingUnavailable {
                Text(L10n.text("Update this translation to keep the recap’s topics and layout.")).font(.caption).foregroundStyle(.secondary)
            }
            if recap["summary"].text.isEmpty && !browsingRecordings {
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
                    .id(topic.id)
                    .background {
                        if expandedID == topic.id, let request = requestedTopic {
                            GeometryReader { _ in
                                Color.clear.preference(key: RecapTopicScrollPreference.self,
                                    value: [request.id: topic.id])
                            }
                        }
                    }
                Divider()
            }
            ForEach(visibleOriginals) { topic in
                RecapOriginalTopicRow(topic: topic, day: day, onCorrect: onCorrect, displayTitle: originalTitle(topic), filters: filters,
                    scopeFingerprint: recap["scope_fingerprint"].text,
                    expanded: Binding(get: { expandedID == "original:" + topic.id }, set: { select($0 ? "original:" + topic.id : nil, title: topic.title) }),
                    onFocus: acceptFocus)
                Divider()
            }
            if !browsingRecordings && !followUps.isEmpty {
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

/// Reading a saved overview never starts inference; only the Recap button does.
struct DayRecapOverview: View {
    let day: String
    let onTopic: (String) -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @Environment(\.locale) private var interfaceLocale
    @State private var overview: JSONValue = .null
    @State private var error: String?
    @State private var generating = false
    @State private var operation: Task<Void, Never>?
    @State private var requestID = UUID()
    @State private var expanded = false
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            if !overview["summary"].text.isEmpty {
                SummaryTranslationView(output: overview, languageLabel: L10n.source("Recap language"), renderedContent: { translation, showing in
                    let text = showing ? translation?["text"].text ?? overview["summary"].text : overview["summary"].text
                    RecapOverviewDisclosure(text: text, expanded: $expanded, identifier: "recap.overview") {
                        overviewText(text)
                    }
                })
                Text(L10n.text("\(overview["reading_minutes"].integer) min read"))
                    .font(.caption).foregroundStyle(.secondary)
                if overview["can_generate"] == .bool(true) {
                    Text(L10n.text("New recordings are available. Generate again to include them."))
                        .font(.caption).foregroundStyle(Theme.accent)
                }
            } else if overview["can_generate"] == .bool(true) {
                Text(L10n.text("Get a recap of this day in up to 5 minutes of reading. It is saved for later."))
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            ErrorNote(message: error)
            if error != nil && overview == .null {
                Button(L10n.text("Retry recap")) { Task { await load() } }
            }
            if generating {
                ProgressView(L10n.text("Generating day recap…"))
                    .accessibilityIdentifier("recap.overview.generating")
            } else if overview["can_generate"] == .bool(true) {
                Button(overview["summary"].text.isEmpty ? L10n.text("Recap") : L10n.text("Generate again")) {
                    operation = Task { await generate() }
                }.accessibilityIdentifier("recap.overview.generate")
            }
        }
        .task(id: String(describing: phase)) {
            guard phase == .active else { return }
            while !Task.isCancelled {
                if !generating { await load() }
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
            }
        }
        .onChange(of: overview["output_id"]) { _, _ in expanded = false }
        .onDisappear { operation?.cancel(); requestID = UUID(); generating = false }
    }

    private func markdown(_ value: String) -> AttributedString {
        (try? AttributedString(markdown: value, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(value)
    }

    private func overviewText(_ text: String) -> some View {
        Text(markdown(text))
            .textSelection(.enabled)
            .environment(\.openURL, OpenURLAction { url in
                guard url.scheme == "topic" else { return .discarded }
                let encoded = String(url.absoluteString.dropFirst("topic:".count))
                guard let id = encoded.removingPercentEncoding,
                      let topic = overview["topics"].array.first(where: { $0["id"].text == id }) else { return .discarded }
                onTopic(topic["title"].text)
                return .handled
            })
            .accessibilityIdentifier("recap.overview.summary")
    }

    private func load() async {
        let token = UUID()
        let identity = model.archiveIdentity
        requestID = token
        do {
            let result = try await model.client().request("v1/recaps/\(day)/overview")
            guard !Task.isCancelled, requestID == token, identity == model.archiveIdentity else { return }
            overview = result; error = nil
        } catch { if !Task.isCancelled, requestID == token, identity == model.archiveIdentity { self.error = L10n.error(error) } }
    }

    private func generate() async {
        guard !generating, overview["can_generate"] == .bool(true) else { return }
        let token = UUID()
        let identity = model.archiveIdentity
        requestID = token; generating = true; error = nil
        defer { if requestID == token { generating = false } }
        do {
            let result = try await model.client().request("v1/recaps/\(day)/overview", method: "POST")
            guard !Task.isCancelled, requestID == token, identity == model.archiveIdentity else { return }
            overview = result
        } catch { if !Task.isCancelled, requestID == token, identity == model.archiveIdentity { self.error = L10n.error(error) } }
    }
}

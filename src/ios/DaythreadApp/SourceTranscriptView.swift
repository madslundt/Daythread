import SwiftUI

/// Translation is a separate, on-demand reading of the original evidence.
struct SourceTranscriptView: View {
    let source: JSONValue
    let editing: Bool
    var startOffset: Int? = nil
    var endOffset: Int? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var translation: JSONValue = .null
    @AppStorage("lastTranslationLanguage") private var savedTranslationLanguage = ""
    @State private var language = ""
    @State private var availableLanguages: [String] = []
    @State private var languageAnalysis: JSONValue = .null
    @State private var recommendedLanguage = ""
    @State private var showingTranslation = false
    @State private var alreadyInTargetLanguage = false
    @State private var busy = false
    @State private var loaded = false
    @State private var error: String?
    @State private var requestID = UUID()
    @State private var refresh = UUID()
    @State private var generation: Task<Void, Never>?
    @State private var loadedScope = ""

    private var sourceID: String { source["id"].text }
    private var languageName: String { L10n.locale.localizedString(forLanguageCode: language) ?? language }
    private var hasTranslation: Bool {
        source["state"].text == "done" && !source["text"].text.isEmpty &&
            !alreadyInTargetLanguage && !translation["text"].text.isEmpty && translation["language"].text == language &&
            translation["transcript_version"] == source["transcript_version"]
    }
    private var scopeKey: String {
        [model.archiveIdentity, model.archiveRevision, sourceID, source["transcript_version"].text,
         source["source_version"].text, source["state"].text, String(editing), startOffset.map(String.init) ?? "full", endOffset.map(String.init) ?? "full"].joined(separator: "|")
    }
    private var loadKey: String { scopeKey + "|" + refresh.uuidString }
    private var translationQuery: [String: String] {
        var query: [String: String] = language.isEmpty ? [:] : ["language": language]
        if let startOffset, let endOffset {
            query["start_offset"] = String(startOffset)
            query["end_offset"] = String(endOffset)
            query["transcript_version"] = source["transcript_version"].text
        }
        return query
    }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            if hasTranslation {
                Picker(L10n.text("Transcript language"), selection: $showingTranslation) {
                    Text(L10n.text("Original")).tag(false)
                    Text(L10n.text("\(languageName) translation")).tag(true)
                }.pickerStyle(.segmented).accessibilityIdentifier("source.transcriptLanguage")
                Text(showingTranslation ? L10n.text("Showing the \(languageName) translation. The original transcription is preserved.") : L10n.text("Showing the original transcription. A \(languageName) translation is available."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(showingTranslation && hasTranslation ? translation["text"].text : originalText)
                .textSelection(.enabled).accessibilityIdentifier("source.transcriptText")
            if !source["text"].text.isEmpty && !originalText.isEmpty {
                DisclosureGroup(L10n.text("Translate")) {
                    if loaded {
                        MixedLanguageTranslationNotice(analysis: languageAnalysis, recommendedLanguage: recommendedLanguage,
                            selectedLanguage: language, availableLanguages: availableLanguages, disabled: busy || editing) { generate(targetLanguage: $0) }
                    }
                    if !availableLanguages.isEmpty {
                        Picker(L10n.text("Translate to"), selection: Binding(get: { language }, set: selectLanguage)) {
                            ForEach(availableLanguages, id: \.self) { code in
                                Text(L10n.locale.localizedString(forLanguageCode: code) ?? code).tag(code)
                            }
                        }.disabled(editing).accessibilityIdentifier("source.translationTarget")
                    }
                    Button(language.isEmpty ? L10n.text("Translate") : L10n.text("Translate to \(languageName)"), systemImage: "translate") { generate() }
                        .disabled(busy || editing || !loaded || !availableLanguages.contains(language) || alreadyInTargetLanguage || hasTranslation)
                        .accessibilityIdentifier("source.translate")
                    if busy {
                        ProgressView(generation == nil ? L10n.text("Checking translation…") : L10n.text("Translating…"))
                    } else if loaded && availableLanguages.isEmpty {
                        Text(L10n.text("Choose translation languages in Settings to translate this transcript."))
                            .font(.caption).foregroundStyle(.secondary)
                        NavigationLink(L10n.text("Translation settings")) { SettingsView() }
                    } else if loaded && alreadyInTargetLanguage {
                        Text(L10n.text("Transcript is already in \(languageName). Choose another translation language above."))
                            .font(.caption).foregroundStyle(.secondary)
                    } else if loaded && !hasTranslation {
                        Text(L10n.text("Translation is generated when requested and preserves the original transcription."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if error != nil {
                        ErrorNote(message: error)
                        if !loaded { Button(L10n.text("Retry translation lookup")) { refresh = UUID() }.disabled(busy || editing) }
                    }
                }
            }
        }
        .task(id: loadKey) { await load() }
        .onChange(of: scenePhase) { _, phase in
            // A temporary loss of focus must not discard a translation request or
            // switch the reader back to the original when the request completes.
            if phase == .active && !busy { refresh = UUID() }
        }
        .onDisappear { invalidate(); loadedScope = "" }
    }

    private var originalText: String {
        if source == .null { return L10n.text("Source removed") }
        if source["state"].text == "expired" { return L10n.text("Transcript expired under your retention policy.") }
        if let startOffset, let endOffset {
            let scalars = source["text"].text.unicodeScalars
            guard startOffset >= 0, endOffset > startOffset, endOffset <= scalars.count else { return "" }
            return String(String.UnicodeScalarView(scalars.dropFirst(startOffset).prefix(endOffset - startOffset)))
        }
        if !source["text"].text.isEmpty { return source["text"].text }
        return L10n.text("No transcript text · \(source["transcription_outcome"].text) · \(source["state"].text)")
    }

    private func invalidate() {
        requestID = UUID()
        generation?.cancel(); generation = nil
        translation = .null; languageAnalysis = .null; recommendedLanguage = ""
        showingTranslation = false; alreadyInTargetLanguage = false; loaded = false; busy = false
    }

    private func load() async {
        if loadedScope != scopeKey {
            invalidate(); language = savedTranslationLanguage; availableLanguages = []; loadedScope = scopeKey
        }
        error = nil
        guard !sourceID.isEmpty, !source["text"].text.isEmpty, !originalText.isEmpty, !editing else { return }
        let request = UUID(), identity = model.archiveIdentity, scope = scopeKey
        requestID = request; busy = true
        defer { if requestID == request { busy = false } }
        do {
            var query = translationQuery
            let result: JSONValue
            do {
                result = try await model.client().request("v1/transcripts/\(sourceID)/translation", query: query)
            } catch let failure as BackendFailure where failure.status == 422 && query["language"] != nil {
                // Keep the passage bounds if an older server rejects the requested target.
                try Task.checkCancellation()
                guard requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                query.removeValue(forKey: "language")
                result = try await model.client().request("v1/transcripts/\(sourceID)/translation", query: query)
            }
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
            apply(result); loaded = true
        } catch {
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
            self.error = L10n.error(error)
        }
    }

    private func apply(_ result: JSONValue) {
        let previousTranslation = translation
        language = result["language"].text
        availableLanguages = result["available_languages"].array.map(\.text)
        languageAnalysis = result["language_analysis"]
        recommendedLanguage = result["recommended_language"].text
        alreadyInTargetLanguage = result["already_in_target_language"] == .bool(true)
        translation = alreadyInTargetLanguage ? .null : result["translation"]
        if alreadyInTargetLanguage { showingTranslation = false }
        else if previousTranslation != translation { showingTranslation = hasTranslation }
    }

    private func selectLanguage(_ code: String) {
        guard code != language else { return }
        invalidate()
        language = code
        savedTranslationLanguage = code
        refresh = UUID()
    }

    private func generate(targetLanguage: String? = nil) {
        let target = targetLanguage ?? language
        guard !busy, loaded, !editing, !target.isEmpty, availableLanguages.contains(target),
              target != language || !alreadyInTargetLanguage else { return }
        if target != language {
            translation = .null; showingTranslation = false; alreadyInTargetLanguage = false; language = target
        }
        savedTranslationLanguage = target
        let request = UUID(), identity = model.archiveIdentity, scope = scopeKey
        requestID = request; busy = true; error = nil
        generation = Task {
            defer { if requestID == request { busy = false; generation = nil } }
            do {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                let result = try await model.client().request("v1/transcripts/\(sourceID)/translation", method: "POST", query: translationQuery)
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                apply(result)
                showingTranslation = hasTranslation
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                self.error = L10n.error(error)
            }
        }
    }
}

/// Topic passages and the full transcript share translation behavior and cache scope.
struct SourceTranscriptTopicsView: View {
    let source: JSONValue
    let editing: Bool
    var onPlayTopic: ((Double) -> Void)? = nil
    @State private var expandedTopics: Set<Int> = []

    private var sections: [JSONValue] {
        let length = source["text"].text.unicodeScalars.count
        return source["sections"].array.filter {
            $0["start_offset"].integer >= 0 && $0["end_offset"].integer > $0["start_offset"].integer &&
                $0["end_offset"].integer <= length
        }
    }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        SourceTranscriptView(source: source, editing: editing)
        if !sections.isEmpty {
            DisclosureGroup(L10n.text("Topics")) {
                ForEach(Array(sections.enumerated()), id: \.element["start_offset"].integer) { _, section in
                    let start = section["start_offset"].integer
                    DisclosureGroup(isExpanded: Binding(
                        get: { expandedTopics.contains(start) },
                        set: { expanded in
                            if expanded { expandedTopics.insert(start) }
                            else { expandedTopics.remove(start) }
                        }
                    )) {
                        if expandedTopics.contains(start) {
                            if let onPlayTopic, case .number(let seconds) = section["start_seconds"] {
                                Button { onPlayTopic(seconds) } label: { Label(L10n.text("Listen"), systemImage: "play.fill") }
                                    .buttonStyle(.borderless)
                            }
                            SourceTranscriptView(source: source, editing: editing,
                                                 startOffset: start, endOffset: section["end_offset"].integer)
                        }
                    } label: {
                        Text(section["title"].text.isEmpty ? L10n.text("Topic") : section["title"].text)
                    }
                }
            }
        }
    }
}

/// A saved summary has a separate translation while its original actions and evidence retain their identity.
struct SummaryTranslationView<Original: View>: View {
    let output: JSONValue
    @ViewBuilder let original: () -> Original
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.locale) private var interfaceLocale
    @State private var translation: JSONValue = .null
    @State private var summaryVersion: JSONValue = .null
    @AppStorage("lastTranslationLanguage") private var savedTranslationLanguage = ""
    @State private var language = ""
    @State private var availableLanguages: [String] = []
    @State private var languageAnalysis: JSONValue = .null
    @State private var recommendedLanguage = ""
    @State private var showingTranslation = false
    @State private var alreadyInTargetLanguage = false
    @State private var busy = false
    @State private var loaded = false
    @State private var copied = false
    @State private var error: String?
    @State private var requestID = UUID()
    @State private var refresh = UUID()
    @State private var generation: Task<Void, Never>?
    @State private var loadedScope = ""

    private var outputID: String { output["output_id"].text }
    private var languageName: String { L10n.locale.localizedString(forLanguageCode: language) ?? language }
    private var hasTranslation: Bool {
        !alreadyInTargetLanguage && !translation["text"].text.isEmpty &&
            translation["language"].text == language && summaryVersion != .null &&
            translation["summary_version"] == summaryVersion
    }
    private var scopeKey: String {
        [model.archiveIdentity, model.archiveRevision, outputID, output["summary"].text].joined(separator: "|")
    }
    private var loadKey: String { scopeKey + "|" + refresh.uuidString }
    private var path: String { "v1/outputs/\(outputID)/translation" }
    private var translationQuery: [String: String] { language.isEmpty ? [:] : ["language": language] }

    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            if hasTranslation {
                Picker(L10n.text("Summary language"), selection: $showingTranslation) {
                    Text(L10n.text("Original")).tag(false)
                    Text(L10n.text("\(languageName) translation")).tag(true)
                }.pickerStyle(.segmented).accessibilityIdentifier("summary.language")
                Text(showingTranslation ? L10n.text("Showing the \(languageName) translation. The original summary is preserved.") : L10n.text("Showing the original summary. A \(languageName) translation is available."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if showingTranslation && hasTranslation {
                Text(.init(translation["text"].text)).textSelection(.enabled).accessibilityIdentifier("summary.translationText")
                Button {
                    UIPasteboard.general.string = translation["text"].text
                    copied = true
                } label: { Label(copied ? L10n.text("Copied") : L10n.text("Copy"), systemImage: copied ? "checkmark" : "doc.on.doc") }
                    .buttonStyle(.borderless)
            } else { original() }
            if !outputID.isEmpty && !output["summary"].text.isEmpty {
                DisclosureGroup(L10n.text("Translate")) {
                    if loaded {
                        MixedLanguageTranslationNotice(analysis: languageAnalysis, recommendedLanguage: recommendedLanguage,
                            selectedLanguage: language, availableLanguages: availableLanguages, disabled: busy) { generate(targetLanguage: $0) }
                    }
                    if !availableLanguages.isEmpty {
                        Picker(L10n.text("Translate to"), selection: Binding(get: { language }, set: selectLanguage)) {
                            ForEach(availableLanguages, id: \.self) { code in
                                Text(L10n.locale.localizedString(forLanguageCode: code) ?? code).tag(code)
                            }
                        }.accessibilityIdentifier("summary.translationTarget")
                    }
                    Button(language.isEmpty ? L10n.text("Translate") : L10n.text("Translate to \(languageName)"), systemImage: "translate") { generate() }
                        .disabled(busy || !loaded || !availableLanguages.contains(language) || alreadyInTargetLanguage || hasTranslation)
                        .accessibilityIdentifier("summary.translate")
                    if busy {
                        ProgressView(generation == nil ? L10n.text("Checking translation…") : L10n.text("Translating…"))
                    } else if loaded && availableLanguages.isEmpty {
                        Text(L10n.text("Choose translation languages in Settings to translate this summary."))
                            .font(.caption).foregroundStyle(.secondary)
                        NavigationLink(L10n.text("Translation settings")) { SettingsView() }
                    } else if loaded && alreadyInTargetLanguage {
                        Text(L10n.text("Summary is already in \(languageName). Choose another translation language above."))
                            .font(.caption).foregroundStyle(.secondary)
                    } else if loaded && !hasTranslation {
                        Text(L10n.text("Translation is generated when requested and preserves the original summary."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if error != nil {
                        ErrorNote(message: error)
                        if !loaded { Button(L10n.text("Retry translation lookup")) { refresh = UUID() }.disabled(busy) }
                    }
                }
            }
        }
        .task(id: loadKey) { await load() }
        .onChange(of: scenePhase) { _, phase in if phase == .active && !busy { refresh = UUID() } }
        .onDisappear { invalidate(); loadedScope = "" }
    }

    private func invalidate() {
        requestID = UUID()
        generation?.cancel(); generation = nil
        translation = .null; summaryVersion = .null; languageAnalysis = .null; recommendedLanguage = ""; showingTranslation = false
        alreadyInTargetLanguage = false; loaded = false; busy = false; copied = false
    }

    private func load() async {
        if loadedScope != scopeKey {
            invalidate(); language = savedTranslationLanguage; availableLanguages = []; loadedScope = scopeKey
        }
        error = nil
        guard !outputID.isEmpty, !output["summary"].text.isEmpty else { return }
        let request = UUID(), identity = model.archiveIdentity, scope = scopeKey
        requestID = request; busy = true
        defer { if requestID == request { busy = false } }
        do {
            let result: JSONValue
            do {
                result = try await model.client().request(path, query: translationQuery)
            } catch let failure as BackendFailure where failure.status == 422 && !language.isEmpty {
                try Task.checkCancellation()
                guard requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                result = try await model.client().request(path)
            }
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
            apply(result); loaded = true
        } catch {
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
            self.error = L10n.error(error)
        }
    }

    private func apply(_ result: JSONValue) {
        let previousTranslation = translation
        language = result["language"].text
        availableLanguages = result["available_languages"].array.map(\.text)
        languageAnalysis = result["language_analysis"]
        recommendedLanguage = result["recommended_language"].text
        summaryVersion = result["summary_version"]
        alreadyInTargetLanguage = result["already_in_target_language"] == .bool(true)
        translation = alreadyInTargetLanguage ? .null : result["translation"]
        if alreadyInTargetLanguage { showingTranslation = false }
        else if previousTranslation != translation { showingTranslation = hasTranslation; copied = false }
    }

    private func selectLanguage(_ code: String) {
        guard code != language else { return }
        invalidate(); language = code; savedTranslationLanguage = code; refresh = UUID()
    }

    private func generate(targetLanguage: String? = nil) {
        let target = targetLanguage ?? language
        guard !busy, loaded, !target.isEmpty, availableLanguages.contains(target),
              target != language || !alreadyInTargetLanguage else { return }
        if target != language {
            translation = .null; showingTranslation = false; alreadyInTargetLanguage = false; copied = false; language = target
        }
        savedTranslationLanguage = target
        let request = UUID(), identity = model.archiveIdentity, scope = scopeKey
        requestID = request; busy = true; error = nil
        generation = Task {
            defer { if requestID == request { busy = false; generation = nil } }
            do {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                let result = try await model.client().request(path, method: "POST", query: translationQuery)
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                apply(result); showingTranslation = hasTranslation
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                self.error = L10n.error(error)
            }
        }
    }
}

/// Recommendations never start generation until the reader requests it.
private struct MixedLanguageTranslationNotice: View {
    let analysis: JSONValue
    let recommendedLanguage: String
    let selectedLanguage: String
    let availableLanguages: [String]
    let disabled: Bool
    let translate: (String) -> Void

    private var dominantLanguage: String { analysis["dominant_language"].text }
    private func baseLanguage(_ code: String) -> String {
        code.lowercased().split(whereSeparator: { $0 == "-" || $0 == "_" }).first.map(String.init) ?? ""
    }
    private var mixedShare: Double? {
        guard !dominantLanguage.isEmpty, !recommendedLanguage.isEmpty,
              baseLanguage(recommendedLanguage) == baseLanguage(dominantLanguage), availableLanguages.contains(recommendedLanguage),
              case .number(let share) = analysis["dominant_language_share"], share > 0, share < 0.9,
              case .object(let languages) = analysis["language_shares"],
              languages.values.filter({ if case .number(let value) = $0 { return value > 0 }; return false }).count > 1 else { return nil }
        return share
    }

    var body: some View {
        if let share = mixedShare {
            let name = L10n.locale.localizedString(forLanguageCode: dominantLanguage) ?? dominantLanguage
            let percent = ((share * 1000).rounded(.down) / 1000)
                .formatted(.percent.precision(.fractionLength(0...1)).locale(L10n.locale))
            Text(L10n.text("Mixed languages · \(name) is dominant (\(percent))."))
                .font(.caption).foregroundStyle(.secondary)
                .accessibilityIdentifier("translation.mixedLanguages")
            if selectedLanguage != recommendedLanguage {
                Button(L10n.text("Translate to \(name)"), systemImage: "translate") { translate(recommendedLanguage) }
                    .disabled(disabled).accessibilityIdentifier("translation.translateToDominant")
            }
        }
    }
}

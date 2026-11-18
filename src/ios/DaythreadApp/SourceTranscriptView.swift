import SwiftUI

/// Translation is a separate, on-demand reading of the original evidence.
struct SourceTranscriptView: View {
    let source: JSONValue
    let editing: Bool
    var startOffset: Int? = nil
    var endOffset: Int? = nil
    var onSeek: ((Double) -> Void)? = nil
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
            if showingTranslation && hasTranslation {
                Text(translation["text"].text).textSelection(.enabled).accessibilityIdentifier("source.transcriptText")
            } else {
                RecordingTranscriptTurns(text: originalText, turns: originalTurns, onSeek: onSeek)
                    .accessibilityIdentifier("source.transcriptText")
            }
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

    private var originalTurns: [JSONValue] {
        let turns = source["transcript_turns"].array
        guard let startOffset, let endOffset else { return turns }
        let scalars = Array(source["text"].text.unicodeScalars)
        return turns.compactMap { turn in
            let start = turn["start_offset"].integer, end = turn["end_offset"].integer
            let left = max(startOffset, start), right = min(endOffset, end)
            guard left >= 0, right <= scalars.count, left < right,
                  case .object(var values) = turn else { return nil }
            if left != start || right != end {
                values["text"] = .string(String(String.UnicodeScalarView(scalars[left..<right])))
                if left != start { values["start"] = nil }
            }
            return .object(values)
        }
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
    @State private var transcriptOpen = false

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
        if !source["recording_summary"].text.isEmpty {
            Text(source["recording_summary"].text).textSelection(.enabled)
        }
        ForEach(Array(sections.enumerated()), id: \.offset) { _, section in
            VStack(alignment: .leading, spacing: 6) {
                Text(section["title"].text.isEmpty ? L10n.text("Topic") : section["title"].text)
                    .font(.headline).accessibilityAddTraits(.isHeader)
                if !section["summary"].text.isEmpty { Text(section["summary"].text).textSelection(.enabled) }
                if let onPlayTopic, case .number(let seconds) = section["start_seconds"] {
                    Button { onPlayTopic(seconds) } label: { Label(L10n.text("Listen"), systemImage: "play.fill") }
                        .buttonStyle(.borderless)
                }
            }
        }
        if source["text"].text.isEmpty {
            SourceTranscriptView(source: source, editing: editing)
        } else {
            DisclosureGroup(L10n.text("Full transcript"), isExpanded: $transcriptOpen) {
                if transcriptOpen { SourceTranscriptView(source: source, editing: editing, onSeek: onPlayTopic) }
            }
        }
    }
}

struct RecordingTranscriptTurns: View {
    let text: String
    let turns: [JSONValue]
    var onSeek: ((Double) -> Void)? = nil
    @State private var showTimes = false

    private func label(_ turn: JSONValue) -> String {
        if turn["attribution"].text == "overlap" { return L10n.text("Overlapping speech") }
        for key in ["speaker_name", "speaker_label"] where !turn[key].text.isEmpty { return turn[key].text }
        if case .number = turn["speaker_number"] { return L10n.text("Speaker \(turn["speaker_number"].integer)") }
        return L10n.text("Unknown speaker")
    }

    var body: some View {
        if turns.isEmpty {
            Text(text).textSelection(.enabled)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                if turns.contains(where: { if case .number = $0["start"] { true } else { false } }) {
                    Toggle(L10n.text("Show timestamps"), isOn: $showTimes)
                }
                ForEach(Array(turns.enumerated()), id: \.offset) { _, turn in
                    if !turn["text"].text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Text(label(turn)).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                                if showTimes, case .number(let start) = turn["start"] {
                                    let seconds = Int(start)
                                    let time = String(format: "%02d:%02d", seconds / 60, seconds % 60)
                                    if let onSeek {
                                        Button(time) { onSeek(start) }.buttonStyle(.borderless).font(.caption.monospacedDigit())
                                    } else { Text(time).font(.caption.monospacedDigit()) }
                                }
                            }
                            Text(turn["text"].text).textSelection(.enabled)
                        }
                    }
                }
            }
        }
    }
}

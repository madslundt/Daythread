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
    @State private var language = ""
    @State private var availableLanguages: [String] = []
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
    private var languageName: String { Locale.current.localizedString(forLanguageCode: language) ?? language }
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

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if hasTranslation {
                Picker("Transcript language", selection: $showingTranslation) {
                    Text("Original").tag(false)
                    Text("\(languageName) translation").tag(true)
                }.pickerStyle(.segmented).accessibilityIdentifier("source.transcriptLanguage")
                Text(showingTranslation ? "Showing the \(languageName) translation. The original transcription is preserved." : "Showing the original transcription. A \(languageName) translation is available.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(showingTranslation && hasTranslation ? translation["text"].text : originalText)
                .textSelection(.enabled).accessibilityIdentifier("source.transcriptText")
            if !source["text"].text.isEmpty && !originalText.isEmpty {
                if !availableLanguages.isEmpty {
                    Picker("Translate to", selection: Binding(get: { language }, set: selectLanguage)) {
                        ForEach(availableLanguages, id: \.self) { code in
                            Text(Locale.current.localizedString(forLanguageCode: code) ?? code).tag(code)
                        }
                    }.disabled(editing).accessibilityIdentifier("source.translationTarget")
                }
                Button(language.isEmpty ? "Translate" : "Translate to \(languageName)", systemImage: "translate") { generate() }
                    .disabled(busy || editing || !loaded || language.isEmpty || alreadyInTargetLanguage || hasTranslation)
                    .accessibilityIdentifier("source.translate")
                if busy {
                    ProgressView(generation == nil ? "Checking translation…" : "Translating…")
                } else if loaded && availableLanguages.isEmpty {
                    Text("Choose translation languages in Settings to translate this transcript.")
                        .font(.caption).foregroundStyle(.secondary)
                    NavigationLink("Translation settings") { SettingsView() }
                } else if loaded && alreadyInTargetLanguage {
                    Text("Transcript is already in \(languageName). Choose another translation language above.")
                        .font(.caption).foregroundStyle(.secondary)
                } else if loaded && !hasTranslation {
                    Text("Translation is generated when requested and preserves the original transcription.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if error != nil {
                    ErrorNote(message: error)
                    if !loaded { Button("Retry translation lookup") { refresh = UUID() }.disabled(busy || editing) }
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
        if source == .null { return "Source removed" }
        if source["state"].text == "expired" { return "Transcript expired under your retention policy." }
        if let startOffset, let endOffset {
            let scalars = source["text"].text.unicodeScalars
            guard startOffset >= 0, endOffset > startOffset, endOffset <= scalars.count else { return "" }
            return String(String.UnicodeScalarView(scalars.dropFirst(startOffset).prefix(endOffset - startOffset)))
        }
        if !source["text"].text.isEmpty { return source["text"].text }
        return "No transcript text · \(source["transcription_outcome"].text) · \(source["state"].text)"
    }

    private func invalidate() {
        requestID = UUID()
        generation?.cancel(); generation = nil
        translation = .null; showingTranslation = false; alreadyInTargetLanguage = false; loaded = false; busy = false
    }

    private func load() async {
        if loadedScope != scopeKey {
            invalidate(); language = ""; availableLanguages = []; loadedScope = scopeKey
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
                // The whitelist may have changed in Settings or on another client.
                // Keep the passage bounds while asking for the new default target.
                try Task.checkCancellation()
                guard requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                query.removeValue(forKey: "language")
                result = try await model.client().request("v1/transcripts/\(sourceID)/translation", query: query)
            }
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
            apply(result); loaded = true
        } catch {
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
            self.error = error.localizedDescription
        }
    }

    private func apply(_ result: JSONValue) {
        let previousTranslation = translation
        language = result["language"].text
        availableLanguages = result["available_languages"].array.map(\.text)
        alreadyInTargetLanguage = result["already_in_target_language"] == .bool(true)
        translation = alreadyInTargetLanguage ? .null : result["translation"]
        if alreadyInTargetLanguage { showingTranslation = false }
        else if previousTranslation != translation { showingTranslation = hasTranslation }
    }

    private func selectLanguage(_ code: String) {
        guard code != language else { return }
        invalidate()
        language = code
        refresh = UUID()
    }

    private func generate() {
        guard !busy, loaded, !editing, !language.isEmpty, availableLanguages.contains(language), !alreadyInTargetLanguage else { return }
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
                self.error = error.localizedDescription
            }
        }
    }
}

/// Topic passages and the full transcript share translation behavior and cache scope.
struct SourceTranscriptTopicsView: View {
    let source: JSONValue
    let editing: Bool
    @State private var expandedTopics: Set<Int> = []
    @State private var fullTranscriptExpanded = false

    private var sections: [JSONValue] {
        let length = source["text"].text.unicodeScalars.count
        return source["sections"].array.filter {
            $0["start_offset"].integer >= 0 && $0["end_offset"].integer > $0["start_offset"].integer &&
                $0["end_offset"].integer <= length
        }
    }

    var body: some View {
        if sections.isEmpty {
            SourceTranscriptView(source: source, editing: editing)
        } else {
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
                        SourceTranscriptView(source: source, editing: editing,
                                             startOffset: start, endOffset: section["end_offset"].integer)
                    }
                } label: {
                    Text(section["title"].text.isEmpty ? "Topic" : section["title"].text)
                }
            }
            DisclosureGroup("Full transcript", isExpanded: $fullTranscriptExpanded) {
                if fullTranscriptExpanded { SourceTranscriptView(source: source, editing: editing) }
            }
        }
    }
}

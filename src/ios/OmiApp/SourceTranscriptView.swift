import SwiftUI

/// Translation is a separate, on-demand reading of the original evidence.
struct SourceTranscriptView: View {
    let source: JSONValue
    let editing: Bool
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var translation: JSONValue = .null
    @State private var language = ""
    @State private var showingTranslation = false
    @State private var alreadyInTargetLanguage = false
    @State private var busy = false
    @State private var loaded = false
    @State private var error: String?
    @State private var requestID = UUID()
    @State private var refresh = UUID()
    @State private var generation: Task<Void, Never>?

    private var sourceID: String { source["id"].text }
    private var languageName: String { Locale.current.localizedString(forLanguageCode: language) ?? language }
    private var hasTranslation: Bool {
        !alreadyInTargetLanguage && !translation["text"].text.isEmpty && translation["language"].text == language &&
            translation["transcript_version"] == source["transcript_version"]
    }
    private var loadKey: String {
        [model.archiveIdentity, model.archiveRevision, sourceID, source["transcript_version"].text,
         String(editing), refresh.uuidString].joined(separator: "|")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if hasTranslation {
                Picker("Transcript language", selection: $showingTranslation) {
                    Text("Original").tag(false)
                    Text(languageName).tag(true)
                }.pickerStyle(.segmented).accessibilityIdentifier("source.transcriptLanguage")
                Text(showingTranslation ? "Showing the \(languageName) translation. The original transcription is preserved." : "Showing the original transcription. A \(languageName) translation is available.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(showingTranslation && hasTranslation ? translation["text"].text : originalText)
                .textSelection(.enabled).accessibilityIdentifier("source.transcriptText")
            if !source["text"].text.isEmpty {
                if busy {
                    ProgressView(generation == nil ? "Checking translation…" : "Translating…")
                } else if loaded && alreadyInTargetLanguage {
                    Text("Transcript is already in \(languageName).")
                        .font(.caption).foregroundStyle(.secondary)
                } else if loaded && !hasTranslation && language != "auto" && !language.isEmpty {
                    Button("Translate to \(languageName)", systemImage: "translate") { generate() }
                        .disabled(editing).accessibilityIdentifier("source.translate")
                    Text("Translation is generated when requested and preserves the original transcription.")
                        .font(.caption).foregroundStyle(.secondary)
                } else if loaded && language == "auto" {
                    Text("Choose a summary language in Settings to translate this transcript.")
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
            if phase == .active { refresh = UUID() }
            else { invalidate() }
        }
        .onDisappear { invalidate() }
    }

    private var originalText: String {
        if source == .null { return "Source removed" }
        if source["state"].text == "expired" { return "Transcript expired under your retention policy." }
        if !source["text"].text.isEmpty { return source["text"].text }
        return "No transcript text · \(source["transcription_outcome"].text) · \(source["state"].text)"
    }

    private func invalidate() {
        requestID = UUID()
        generation?.cancel(); generation = nil
        translation = .null; showingTranslation = false; alreadyInTargetLanguage = false; loaded = false; busy = false
    }

    private func load() async {
        invalidate(); error = nil; language = ""
        guard !sourceID.isEmpty, !source["text"].text.isEmpty, !editing else { return }
        let request = requestID, identity = model.archiveIdentity
        busy = true
        defer { if requestID == request { busy = false } }
        do {
            let result = try await model.client().request("v1/transcripts/\(sourceID)/translation")
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
            apply(result); loaded = true
        } catch {
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
            self.error = error.localizedDescription
        }
    }

    private func apply(_ result: JSONValue) {
        language = result["language"].text
        alreadyInTargetLanguage = result["already_in_target_language"] == .bool(true)
        translation = alreadyInTargetLanguage ? .null : result["translation"]
        if alreadyInTargetLanguage { showingTranslation = false }
    }

    private func generate() {
        guard !busy, loaded, !editing, !alreadyInTargetLanguage else { return }
        let request = UUID(), identity = model.archiveIdentity
        requestID = request; busy = true; error = nil
        generation = Task {
            defer { if requestID == request { busy = false; generation = nil } }
            do {
                let result = try await model.client().request("v1/transcripts/\(sourceID)/translation", method: "POST")
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                apply(result)
                showingTranslation = hasTranslation
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                self.error = error.localizedDescription
            }
        }
    }
}

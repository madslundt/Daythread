import SwiftUI
import OmiCore

extension AppModel {
    func currentTranscriptionPreview() -> Data? { store?.transcriptionPreview() }
}

/// Explicitly requested, in-memory text. It never updates the archive's canonical transcript.
struct TranscriptionPreviewControl: View {
    @Environment(AppModel.self) private var model
    @Environment(\.locale) private var interfaceLocale
    @Environment(\.scenePhase) private var phase
    @State private var expanded = false
    @State private var enabled = false
    @State private var loading = false
    @State private var busy = false
    @State private var text = ""
    @State private var error: String?
    @State private var operation: Task<Void, Never>?
    private var context: String { model.archiveIdentity }

    var body: some View {
        let _ = interfaceLocale
        DisclosureGroup(L10n.text("Provisional transcription preview"), isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 10) {
                Text(L10n.text("Preview up to 15 seconds of current pendant audio. This text is temporary; the final transcript is processed separately."))
                    .font(.caption).foregroundStyle(.secondary)
                if loading { ProgressView() }
                else if enabled {
                    Button(L10n.text("Preview current audio")) { preview() }
                        .disabled(busy).accessibilityIdentifier("capture.transcriptionPreview")
                } else {
                    Text(L10n.text("Enable provisional transcription preview in transcription settings to use this feature."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                if busy { ProgressView(L10n.text("Transcribing preview…")) }
                if !text.isEmpty { Text(text).font(.subheadline).textSelection(.enabled) }
                ErrorNote(message: error)
            }.padding(.vertical, 6)
        }
        .task(id: expanded ? context : "") {
            guard expanded else { clear(); return }
            let key = context
            loading = true; error = nil
            defer { if key == context { loading = false } }
            do {
                let result = try await model.client().request("v1/transcription-preview")
                guard !Task.isCancelled, key == context else { return }
                enabled = result["enabled"] == .bool(true)
            } catch { if !Task.isCancelled, key == context { self.error = L10n.error(error) } }
        }
        .onDisappear { clear() }
        .onChange(of: phase) { _, value in if value != .active { clear() } }
        .onChange(of: context) { _, _ in clear(); enabled = false }
    }

    private func clear() {
        operation?.cancel(); operation = nil; busy = false; text = ""; error = nil
    }

    private func preview() {
        guard !busy, phase == .active else { return }
        guard let audio = model.currentTranscriptionPreview() else {
            error = L10n.text("No current pendant audio is available yet."); return
        }
        let key = context
        busy = true; text = ""; error = nil
        operation = Task {
            defer { if key == context { busy = false; operation = nil } }
            do {
                let client = try model.client()
                let data = try await client.data(path: "v1/transcription-preview", method: "POST",
                    body: audio, profileHeader: client.profileID, timeout: 12,
                    ephemeral: true, contentType: "audio/wav")
                let result = try JSONDecoder().decode(JSONValue.self, from: data)
                guard !Task.isCancelled, key == context, expanded, phase == .active else { return }
                guard result["provisional"] == .bool(true), result["saved"] == .bool(false) else {
                    throw BackendFailure(message: L10n.source("The preview response was invalid."))
                }
                text = result["text"].text
                if text.isEmpty { error = L10n.text("No recognized speech in this preview.") }
            } catch { if !Task.isCancelled, key == context { self.error = L10n.error(error) } }
        }
    }
}

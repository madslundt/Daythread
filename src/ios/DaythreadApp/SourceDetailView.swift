import SwiftUI

struct SourceDetail: View {
    let source: JSONValue
    var onCorrect: (() -> Void)? = nil
    var startAt: TimeInterval? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @State private var fresh: JSONValue?
    @State private var error: String?
    @State private var loading = false
    @State private var editing = false
    @State private var draft = ""
    @State private var audio = RecordingPlayback()
    @State private var audioRetry = UUID()
    @Environment(\.locale) private var interfaceLocale
    private var evidence: JSONValue {
        guard let fresh else { return source }
        guard case .object(var values) = fresh else { return fresh }
        // Timeline grouping supplies alternate uploads; a transcript refresh omits them.
        if values["alternate_sources"] == nil { values["alternate_sources"] = source["alternate_sources"] }
        return .object(values)
    }
    private var canLoadAudio: Bool {
        evidence != .null && !evidence["id"].text.isEmpty && evidence["audio_available"] != .bool(false)
            && evidence["audio_availability"].text != "missing"
    }
    private var durationHint: TimeInterval {
        guard let start = AppModel.date(evidence["started_at"].text), let end = AppModel.date(evidence["ended_at"].text) else { return 0 }
        return max(0, end.timeIntervalSince(start))
    }

    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            List {
                Section(L10n.text("Listen")) {
                    playback
                }
                Section(L10n.text("Transcript")) {
                    SourceTranscriptTopicsView(source: evidence, editing: editing, onPlayTopic: canLoadAudio ? { seconds in
                        audio.seek(to: seconds)
                        if !audio.playing { audio.togglePlayback() }
                    } : nil)
                }
                Section {
                    DisclosureGroup(L10n.text("Transcript correction")) {
                        correction
                        if evidence != .null {
                            TranscriptionCleanupReviewView(source: evidence) { draft = evidence["text"].text; editing = true }
                        }
                    }
                    NavigationLink {
                        List { details }.omiScreen()
                            .navigationTitle(L10n.text("Recording details & tools"))
                            .navigationBarTitleDisplayMode(.inline)
                    } label: {
                        Label(L10n.text("Recording details & tools"), systemImage: "info.circle")
                    }.accessibilityIdentifier("source.details")
                }
                ErrorNote(message: error)
            }.task(id: model.archiveRevision + model.archiveIdentity + String(describing: scenePhase)) {
                guard scenePhase == .active else { return }
                let identity = model.archiveIdentity
                do {
                    let result = try await model.client().request("v1/transcripts/\(source["id"].text)")
                    guard !Task.isCancelled, model.archiveIdentity == identity else { return }
                    fresh = result; error = nil
                } catch {
                    guard !Task.isCancelled, model.archiveIdentity == identity else { return }
                    self.error = L10n.error(error)
                    if (error as? BackendFailure)?.status == 404 { fresh = .null }
                }
            }.omiScreen().navigationTitle(L10n.text("Recording")).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L10n.text("Done")) { dismiss() } } }
        }
        .task(id: model.archiveIdentity + source["id"].text + String(canLoadAudio) + audioRetry.uuidString) {
            if canLoadAudio {
                await audio.load(sourceID: source["id"].text, durationHint: durationHint, model: model)
                if let startAt { audio.seek(to: startAt) }
            }
            else { audio.close() }
        }
        .onDisappear { audio.close() }
    }

    @ViewBuilder private var playback: some View {
        if evidence == .null {
            Text(L10n.text("Source removed")).foregroundStyle(.secondary)
        } else if evidence["audio_available"] == .bool(false) {
            Text(SourceProvenanceView.isTranscriptImport(evidence)
                 ? L10n.text("Imported from \(SourceProvenanceView.vendorLabel(for: evidence)) · transcript only; original audio unavailable.")
                 : L10n.text("Audio expired under retention; transcript remains available.")).font(.caption).foregroundStyle(.secondary)
        } else if evidence["audio_availability"].text == "missing" {
            Text(L10n.text("The archive expects audio, but the file is missing.")).font(.caption).foregroundStyle(.secondary)
        } else {
            RecordingAudioPlayer(playback: audio, retry: { audioRetry = UUID() })
        }
    }

    @ViewBuilder private var correction: some View {
        if editing {
            TextEditor(text: $draft).frame(minHeight: 160).accessibilityLabel(L10n.text("Correct transcript"))
            Button(L10n.text("Save correction")) {
                let address = model.archiveIdentity
                loading = true
                Task {
                    defer { loading = false }
                    do {
                        guard model.archiveIdentity == address else { return }
                        let corrected = try await model.client().request("v1/transcripts/\(evidence["id"].text)", method: "PATCH", body: ["text": draft])
                        guard !Task.isCancelled, model.archiveIdentity == address else { return }
                        fresh = corrected
                        onCorrect?(); dismiss()
                    } catch { self.error = L10n.error(error) }
                }
            }.disabled(loading || draft.unicodeScalars.count > 200000)
            Button(L10n.text("Cancel edit")) { editing = false }
        } else {
            Button(L10n.text("Correct transcript")) { draft = evidence["text"].text; editing = true }.disabled(evidence == .null)
        }
    }

    @ViewBuilder private var details: some View {
        Section(L10n.text("Recorded evidence")) {
            Text(sourceDate).font(.caption).textSelection(.enabled)
            ImportanceButton(target: ImportanceTarget(kind: "source", id: source["id"].text), label: L10n.key("recording")).disabled(evidence == .null)
            NavigationLink(L10n.text("Report incorrect or missing information")) { FeedbackEditor(sourceID: evidence["id"].text) }.disabled(evidence == .null)
        }
        Section(L10n.text("Tags")) {
            if evidence["tags"].array.isEmpty { Text(L10n.text("No tags yet")).foregroundStyle(.secondary) }
            else { Text(evidence["tags"].array.map { $0["name"].text }.joined(separator: ", ")) }
            NavigationLink(evidence["tags"].array.isEmpty ? L10n.text("Add tag") : L10n.text("Edit tags")) {
                ConversationTagsView(sourceID: evidence["id"].text, onChanged: {
                    onCorrect?(); Task { await refreshMetadata() }
                })
            }.disabled(evidence == .null || evidence["state"].text == "expired")
        }
        ConversationLocationView(location: evidence["source_metadata"]["location"],
            manualLocation: evidence["manual_location"], sourceID: evidence["id"].text,
            editable: evidence != .null && evidence["state"].text != "expired", onChanged: {
                onCorrect?(); Task { await refreshMetadata() }
            })
        NavigationLink { SourceLabelsView(sourceID: evidence["id"].text) } label: { Label(L10n.text("People & projects"), systemImage: "person.2") }.disabled(evidence == .null)
        NavigationLink {
            SpeakersView(kind: "source", conversationID: evidence["id"].text)
        } label: { Label(L10n.text("Speakers · listen & name"), systemImage: "person.wave.2") }
            .disabled(evidence == .null).accessibilityIdentifier("source.speakers")
        NavigationLink(L10n.text("Create decision from this source")) {
            SourceDecisionCreation(sourceID: evidence["id"].text)
        }.disabled(evidence["text"].text.isEmpty)
        if evidence != .null {
            SourceActionsView(source: evidence) { onCorrect?(); dismiss() }
        }
        if evidence["state"].text == "blocked" || !evidence["error_code"].text.isEmpty ||
            (evidence["state"].text == "done" && evidence["text"].text.isEmpty && evidence["audio_available"] != .bool(false) &&
             ["silence_gate", "empty_stt"].contains(evidence["transcription_outcome"].text)) {
            Button(L10n.text("Retry transcription after resolving the problem")) { Task {
                do { _ = try await model.client().request("v1/audio/\(evidence["id"].text)/retry", method: "POST"); error = nil }
                catch { self.error = L10n.error(error) }
            } }
        }
        ErrorNote(message: error)
    }

    private func refreshMetadata() async {
        let identity = model.archiveIdentity
        do {
            let result = try await model.client().request("v1/sources/\(source["id"].text)")
            guard !Task.isCancelled, identity == model.archiveIdentity else { return }
            fresh = result
        } catch { if identity == model.archiveIdentity { self.error = L10n.error(error) } }
    }

    private var sourceDate: String {
        guard let date = AppModel.date(evidence["started_at"].text) else { return evidence["started_at"].text }
        let formatter = DateFormatter(); formatter.locale = L10n.locale
        formatter.timeZone = TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current
        formatter.dateStyle = .medium; formatter.timeStyle = .short
        return formatter.string(from: date) + " · " + formatter.timeZone.identifier
    }
}

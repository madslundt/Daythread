import SwiftUI
import OmiCore

/// Secondary source tools share the web archive's deletion and correction semantics.
struct SourceActionsView: View {
    let source: JSONValue
    let onDelete: () -> Void
    @Environment(AppModel.self) private var model
    @State private var confirmingDelete = false
    @State private var busy = false
    @State private var error: String?
    @State private var exportedAudio: URL?
    @State private var operation: Task<Void, Never>?
    @State private var requestID = UUID()
    private var sourceID: String { source["id"].text }
    private var canPlay: Bool { source["audio_available"] != .bool(false) && source["audio_availability"].text != "missing" }

    var body: some View {
        Section("Recording details & tools") {
            SourceProvenanceView(source: source)
            if !source["alternate_sources"].array.isEmpty {
                DisclosureGroup("Other uploads of this recording (\(source["alternate_sources"].array.count))") {
                    Text("Identical audio is shown once in the timeline. Each upload keeps its supplied time and transcript.")
                        .font(.caption).foregroundStyle(.secondary)
                    SourceList(sources: source["alternate_sources"].array)
                }
            }
            NavigationLink {
                SourceCorrectionHistoryView(sourceID: sourceID)
            } label: {
                Label("Correction history", systemImage: "clock.arrow.circlepath")
            }.accessibilityIdentifier("source.correctionHistory")
            if canPlay {
                if let exportedAudio {
                    ShareLink(item: exportedAudio) { Label("Share audio", systemImage: "square.and.arrow.up") }
                } else {
                    Button("Export audio", systemImage: "square.and.arrow.up") { exportAudio() }
                        .disabled(busy).accessibilityIdentifier("source.exportAudio")
                }
            }
            Button("Delete recording", systemImage: "trash", role: .destructive) { confirmingDelete = true }
                .disabled(busy).accessibilityIdentifier("source.delete")
            if busy { ProgressView("Updating recording…") }
            ErrorNote(message: error)
        }
        .confirmationDialog("Delete this recording and transcript?", isPresented: $confirmingDelete, titleVisibility: .visible) {
            Button("Delete recording", role: .destructive) { delete() }
        } message: {
            Text("Derived diary entries, weekly reviews and chat history will also be cleared. Related decisions and inbox items, including manual edits and review history, will be removed because they used this recording. This cannot be undone.")
        }
        .onDisappear {
            requestID = UUID(); operation?.cancel(); operation = nil; busy = false
            if let exportedAudio { try? FileManager.default.removeItem(at: exportedAudio) }
            exportedAudio = nil
        }
    }

    private func exportAudio() {
        let identity = model.archiveIdentity, request = UUID()
        requestID = request; busy = true; error = nil
        operation = Task {
            defer { if requestID == request { busy = false; operation = nil } }
            do {
                let file = try await model.client().download("v1/audio/\(sourceID)", query: [:])
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else {
                    try? FileManager.default.removeItem(at: file); return
                }
                exportedAudio = file
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                self.error = error.localizedDescription
            }
        }
    }

    private func delete() {
        let identity = model.archiveIdentity, request = UUID()
        requestID = request; busy = true; error = nil
        operation = Task {
            defer { if requestID == request { busy = false; operation = nil } }
            do {
                _ = try await model.client().request("v1/audio/\(sourceID)", method: "DELETE")
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                model.player?.stop(); model.playbackStatus = ""
                // Dismiss before a revision refresh removes this source's tools.
                // The refresh must survive the sheet cancelling its own operation.
                Task { await model.refreshStatus() }
                onDelete()
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                self.error = error.localizedDescription
            }
        }
    }
}

struct SourceProvenanceView: View {
    let source: JSONValue
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if source["source_kind"].text == "manual_upload" {
                Text("Manually uploaded recording\(source["source_id"].text.isEmpty ? "" : " · " + source["source_id"].text)")
                Text("Capture time was supplied when importing. Archived audio is converted to mono PCM16 at 16 kHz.")
            } else if source["source_kind"].text == "omi_audio_webhook" {
                Text("Forwarded from the official Omi integration")
                Text("Times estimate server receipt, not capture time. Each delivery is separate; overlap, gaps and order are uncertain. Account identity is supplied by the sender; pendant identity is unknown.")
            } else if source["source_kind"].text == "omi_api" {
                Text("Imported Omi transcript · original audio unavailable")
            }
            if !source["device_id"].text.isEmpty { Text("Device: \(source["device_id"].text)") }
            Text("Source: \(source["id"].text)").textSelection(.enabled)
        }.font(.caption).foregroundStyle(.secondary)
    }
}

struct TranscriptionCleanupReviewView: View {
    let source: JSONValue
    let onReview: () -> Void
    private var chunks: [JSONValue] { source["transcription_details"]["chunks"].array }
    private var alternatives: [JSONValue] {
        chunks.filter { !$0["candidate"]["text"].text.isEmpty && $0["candidate"]["differs"] == .bool(true) }
    }
    private var unavailable: Bool {
        chunks.contains { ["failed", "timeout", "unavailable", "budget_exhausted"].contains($0["enhancement_status"].text) }
    }
    var body: some View {
        if unavailable || !alternatives.isEmpty {
            Section("Transcription cleanup review") {
                if unavailable {
                    Text("Audio cleanup was unavailable or did not finish. The original transcription was kept.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if !alternatives.isEmpty {
                    DisclosureGroup("Alternative cleanup reading · needs review") {
                        Text("Cleanup can miss quiet voices or mistake noise for words. These readings are excluded from search and summaries. Listen to the original audio, compare the readings, then save a correction if needed. Simultaneous voices may still be missing.")
                            .font(.caption).foregroundStyle(.secondary)
                        ForEach(Array(alternatives.enumerated()), id: \.offset) { _, chunk in
                            Text("\(seconds(chunk["start"]))–\(seconds(chunk["end"])) seconds into this recording")
                                .font(.caption).foregroundStyle(.secondary)
                            Text(chunk["candidate"]["text"].text).textSelection(.enabled)
                        }
                        Button("Review and correct transcript", action: onReview)
                            .accessibilityIdentifier("source.reviewCleanup")
                    }
                }
            }
        }
    }
    private func seconds(_ value: JSONValue) -> String {
        if case .number(let seconds) = value { return String(format: "%.1f", seconds) }
        return "0.0"
    }
}

private struct SourceCorrectionHistoryView: View {
    let sourceID: String
    @Environment(AppModel.self) private var model
    @State private var revisions: [JSONValue] = []
    @State private var loading = true
    @State private var error: String?
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    var body: some View {
        List {
            if loading { ProgressView("Loading corrections…") }
            ErrorNote(message: error)
            if error != nil { Button("Try again") { Task { await load() } } }
            if !loading && error == nil && revisions.isEmpty {
                ContentUnavailableView("No corrections yet", systemImage: "clock.arrow.circlepath", description: Text("Saved transcript corrections will appear here."))
            }
            ForEach(Array(revisions.enumerated()), id: \.element["id"].text) { _, revision in
                DisclosureGroup(AppModel.date(revision["created_at"].text).map { diary.label($0, template: "d MMM yyyy HHmm") } ?? revision["created_at"].text) {
                    Text("Previous transcript").font(.caption).foregroundStyle(.secondary)
                    Text(revision["previous_text"].text.isEmpty ? "No transcript text" : revision["previous_text"].text).textSelection(.enabled)
                    Text("Corrected transcript").font(.caption).foregroundStyle(.secondary)
                    Text(revision["text"].text.isEmpty ? "No transcript text" : revision["text"].text).textSelection(.enabled)
                }
            }
        }.omiScreen().navigationTitle("Correction history").navigationBarTitleDisplayMode(.inline)
            .task(id: model.archiveIdentity + model.archiveRevision + sourceID) { await load() }
            .refreshable { await load() }
    }
    private func load() async {
        let identity = model.archiveIdentity
        loading = true; error = nil; defer { loading = false }
        do {
            let result = try await model.client().request("v1/transcripts/\(sourceID)/revisions")
            guard !Task.isCancelled, model.archiveIdentity == identity else { return }
            revisions = result["items"].array
        } catch {
            guard !Task.isCancelled, model.archiveIdentity == identity else { return }
            self.error = error.localizedDescription
        }
    }
}

import SwiftUI

private struct ConversationSpeakers: Decodable {
    struct Scope: Decodable { let kind: String; let id: String }
    struct Speaker: Decodable, Identifiable {
        let id: String
        let label: String
        let name: String?
        let seconds: Double
        var displayName: String { name?.isEmpty == false ? name! : label }
    }
    struct Turn: Decodable, Identifiable {
        let id: String
        let source_id: String
        let start: Double
        let end: Double
        let speaker_id: String
        let text: String?
    }
    let state: String
    let configured: Bool
    let version: String?
    let speakers: [Speaker]
    let turns: [Turn]
    let audio_available: Bool
    let scope: Scope
    let source_count: Int
}

/// Speaker names belong to this recording or meeting, never to a global voice identity.
struct SpeakersView: View {
    let kind: String
    let conversationID: String
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var result: ConversationSpeakers?
    @State private var error: String?
    @State private var busy = false
    @State private var activity = "Loading speakers…"
    @State private var operation: Task<Void, Never>?
    @State private var requestID = UUID()
    @State private var renameID: String?
    @State private var name = ""
    @State private var mergeSource: ConversationSpeakers.Speaker?
    @State private var mergeTarget: ConversationSpeakers.Speaker?
    @State private var confirmingDetection = false
    @State private var knownCount = 0
    @State private var playingSpeakerID: String?

    private var path: String { "v1/speakers/\(kind)/\(conversationID)" }
    private var editable: Bool { result?.state == "ready" && !busy }
    private var loadKey: String { model.archiveIdentity + model.archiveRevision + path + String(describing: scenePhase) }

    var body: some View {
        List {
            Section {
                Text("Listen to each speaker, then add a name. Names apply only to this \(result?.scope.kind == "meeting" ? "meeting" : "conversation").")
                if let result, result.source_count > 1 {
                    Text("Includes \(result.source_count) recordings with shared speaker names.").font(.caption).foregroundStyle(.secondary)
                }
                Text("Playback skips other speakers’ turns. Overlapping voices can still be audible.")
                    .font(.caption).foregroundStyle(.secondary)
                if busy { ProgressView(activity) }
                ErrorNote(message: error)
                if result == nil && !busy { Button("Retry") { Task { await load() } } }
            }
            if let result {
                detectionSection(result)
                if !result.speakers.isEmpty {
                    Section("\(result.speakers.count) speakers detected") {
                        ForEach(result.speakers) { speaker in speakerRow(speaker, result: result) }
                    }
                    Section("Speaking turns") {
                        let sources = result.turns.reduce(into: [String]()) { if !$0.contains($1.source_id) { $0.append($1.source_id) } }
                        ForEach(result.turns) { turn in
                            turnRow(turn, result: result, recordingNumber: (sources.firstIndex(of: turn.source_id) ?? 0) + 1)
                        }
                    }
                } else if result.state == "ready" {
                    Text("No speakers detected. Try detecting again with the expected number of speakers.")
                        .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("Speakers").navigationBarTitleDisplayMode(.inline)
        .task(id: loadKey) {
            invalidate()
            guard scenePhase == .active else { return }
            await load()
        }
        .onDisappear { invalidate() }
        .alert("Name speaker", isPresented: Binding(get: { renameID != nil }, set: { if !$0 { renameID = nil } })) {
            TextField("Name", text: $name).textInputAutocapitalization(.words)
            Button("Cancel", role: .cancel) { renameID = nil }
            Button("Save") {
                guard let id = renameID else { return }
                let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
                renameID = nil
                mutate(path + "/" + id, method: "PATCH", body: ["name": trimmed.isEmpty ? NSNull() : trimmed as Any])
            }
        } message: { Text("Leave blank to restore the letter label.") }
        .confirmationDialog("Detect speakers again?", isPresented: $confirmingDetection, titleVisibility: .visible) {
            Button("Detect again") { detect() }
        } message: { Text("This replaces speaker assignments. You may need to name speakers again.") }
        .confirmationDialog("Merge speakers?", isPresented: Binding(get: { mergeTarget != nil }, set: { if !$0 { mergeTarget = nil; mergeSource = nil } }), titleVisibility: .visible) {
            Button("Merge") {
                guard let source = mergeSource, let target = mergeTarget else { return }
                mergeSource = nil; mergeTarget = nil
                mutate(path + "/merge", method: "POST", body: ["source_id": source.id, "target_id": target.id])
            }
        } message: { Text("All turns from \(mergeSource?.displayName ?? "this speaker") will use \(mergeTarget?.displayName ?? "the selected speaker").") }
    }

    private func detectionSection(_ result: ConversationSpeakers) -> some View {
        Section("Speaker detection") {
            if result.state == "stale" {
                Text("Recordings or transcripts changed. Detect speakers again to update their turns.").foregroundStyle(.orange)
            }
            if !result.configured {
                Text("Enable speaker detection and set its service address in Settings → Models, keys & all server settings.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            if !result.audio_available {
                Text("Original audio is unavailable. Speaker detection and playback need retained audio.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Picker("Expected speakers", selection: $knownCount) {
                Text("Detect automatically").tag(0)
                ForEach(1...20, id: \.self) { Text("\($0)").tag($0) }
            }.disabled(busy)
            Button(result.state == "idle" ? "Detect speakers" : "Detect speakers again", systemImage: "waveform.badge.magnifyingglass") {
                if result.speakers.isEmpty { detect() } else { confirmingDetection = true }
            }.disabled(busy || !result.configured || !result.audio_available)
                .accessibilityIdentifier("speakers.detect")
        }
    }

    private func speakerRow(_ speaker: ConversationSpeakers.Speaker, result: ConversationSpeakers) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(speaker.displayName).font(.headline)
                Spacer()
                Text("\(Int(speaker.seconds.rounded()))s").font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button("Listen to \(speaker.displayName)", systemImage: "play.circle") { listen(speaker) }
                    .disabled(!editable || !result.audio_available)
                Spacer()
                Button("Name", systemImage: "pencil") { name = speaker.name ?? ""; renameID = speaker.id }
                    .disabled(!editable).accessibilityLabel("Name speaker \(speaker.displayName)")
            }.buttonStyle(.borderless)
            if playingSpeakerID == speaker.id {
                Button("Stop playback", systemImage: "stop.circle") { stopPlayback() }.buttonStyle(.borderless)
            }
            if result.speakers.count > 1 {
                Menu("Merge with another speaker") {
                    ForEach(result.speakers.filter { $0.id != speaker.id }) { target in
                        Button("Merge into \(target.displayName)") { mergeSource = speaker; mergeTarget = target }
                    }
                }.disabled(!editable).font(.caption)
            }
        }.padding(.vertical, 4)
    }

    private func turnRow(_ turn: ConversationSpeakers.Turn, result: ConversationSpeakers, recordingNumber: Int) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(result.speakers.first { $0.id == turn.speaker_id }?.displayName ?? "Speaker").font(.subheadline.bold())
                Spacer()
                Text("\(timestamp(turn.start))–\(timestamp(turn.end))").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            if result.source_count > 1 {
                Text("Recording \(recordingNumber)").font(.caption).foregroundStyle(.secondary)
            }
            if let text = turn.text, !text.isEmpty { Text(text).textSelection(.enabled) }
            Menu("Change speaker") {
                ForEach(result.speakers) { speaker in
                    Button {
                        mutate(path + "/turns/" + turn.id, method: "PATCH", body: ["speaker_id": speaker.id])
                    } label: {
                        if turn.speaker_id == speaker.id { Label(speaker.displayName, systemImage: "checkmark") }
                        else { Text(speaker.displayName) }
                    }
                }
            }.font(.caption).disabled(!editable)
        }.padding(.vertical, 4)
    }

    private func timestamp(_ seconds: Double) -> String {
        let value = max(0, Int(seconds))
        return String(format: "%d:%02d", value / 60, value % 60)
    }

    private func stopPlayback() {
        if playingSpeakerID != nil { model.player?.stop(); model.playbackStatus = ""; playingSpeakerID = nil }
    }

    private func invalidate() {
        requestID = UUID(); operation?.cancel(); operation = nil; busy = false
        renameID = nil; mergeSource = nil; mergeTarget = nil; confirmingDetection = false
        result = nil; stopPlayback()
    }

    private func load() async {
        guard !busy else { return }
        let identity = model.archiveIdentity, request = UUID()
        requestID = request; busy = true; activity = "Loading speakers…"; error = nil
        defer { if requestID == request { busy = false } }
        do {
            let loaded = try await model.client().decoded(path, as: ConversationSpeakers.self)
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
            result = loaded
        } catch {
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
            self.error = error.localizedDescription
        }
    }

    private func detect() {
        var body: [String: Any] = knownCount == 0 ? [:] : ["num_speakers": knownCount]
        if result?.state != "idle" { body["reset"] = true }
        mutate(path, method: "POST", body: body, versioned: false)
    }

    private func mutate(_ endpoint: String, method: String, body: [String: Any], versioned: Bool = true) {
        guard !busy, let result, !versioned || (result.state == "ready" && result.version != nil) else { return }
        let identity = model.archiveIdentity, request = UUID()
        let client: BackendClient
        do { client = try model.client() }
        catch { self.error = error.localizedDescription; return }
        var payload = body
        if versioned { payload["version"] = result.version! }
        requestID = request; busy = true; activity = versioned ? "Updating speakers…" : "Detecting speakers…"; error = nil; stopPlayback()
        operation = Task {
            defer { if requestID == request { busy = false; operation = nil } }
            do {
                try Task.checkCancellation()
                let updated = try await client.decoded(endpoint, method: method, body: payload, as: ConversationSpeakers.self)
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                self.result = updated
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                self.error = error.localizedDescription
                if (error as? BackendFailure)?.status == 409 {
                    let refreshed = try? await client.decoded(path, as: ConversationSpeakers.self)
                    guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                    self.result = refreshed
                }
            }
        }
    }

    private func listen(_ speaker: ConversationSpeakers.Speaker) {
        guard editable, result?.audio_available == true, let version = result?.version else { return }
        let identity = model.archiveIdentity, request = UUID()
        let client: BackendClient
        do { client = try model.client() }
        catch { self.error = error.localizedDescription; return }
        requestID = request; busy = true; activity = "Loading speaker audio…"; error = nil; stopPlayback()
        operation = Task {
            defer { if requestID == request { busy = false; operation = nil } }
            do {
                try Task.checkCancellation()
                let audio = try await client.download(path + "/" + speaker.id + "/audio", query: ["version": version])
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                model.play(audio); playingSpeakerID = speaker.id
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                self.error = error.localizedDescription
            }
        }
    }
}

import SwiftUI

private struct VoiceRecognitionReadiness: Decodable {
    let state: String
    let message: String
}

private struct SavedVoiceDirectory: Decodable {
    struct Profile: Decodable, Identifiable {
        struct Example: Decodable, Identifiable {
            let id: String
            let scope: ConversationSpeakers.Scope
            let speaker_id: String
            let seconds: Double
            let created_at: String
        }
        let id: String
        let name: String
        let example_count: Int
        let conversation_count: Int
        let examples: [Example]
    }
    let revision: String
    let profiles: [Profile]
    let recognition: VoiceRecognitionReadiness
}

private struct ConversationSpeakers: Decodable {
    struct Scope: Decodable { let kind: String; let id: String }
    struct Speaker: Decodable, Identifiable {
        struct Suggestion: Decodable { let profile_id: String; let name: String }
        let id: String
        let label: String
        let name: String?
        let seconds: Double
        let voice_profile_id: String?
        let remembered_example_id: String?
        let suggestion: Suggestion?
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
    let recognition: VoiceRecognitionReadiness?
}

/// Conversation names remain local; saved voice examples require explicit confirmation.
struct SpeakersView: View {
    let kind: String
    let conversationID: String
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var result: ConversationSpeakers?
    @State private var voices: SavedVoiceDirectory?
    @State private var voiceError: String?
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
    @State private var rememberSpeaker: ConversationSpeakers.Speaker?
    @State private var dismissedSuggestions: Set<String> = []
    @State private var forgettingVoice: SavedVoiceDirectory.Profile?
    @State private var forgettingExample: SavedVoiceDirectory.Profile.Example?

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
                Text("Saved voice profiles stay in the selected archive. Suggestions never change a name until you confirm them.")
                    .font(.caption).foregroundStyle(.secondary)
                if let readiness = result?.recognition ?? voices?.recognition {
                    Text(readiness.message).font(.callout).foregroundStyle(.secondary)
                }
                if busy { ProgressView(activity) }
                ErrorNote(message: error)
                ErrorNote(message: voiceError)
                if !busy && (result == nil || error != nil || voiceError != nil) { Button("Refresh speakers and saved voices") { Task { await load() } } }
            }
            savedVoicesSection
            if let result {
                detectionSection(result)
                if !result.speakers.isEmpty {
                    Section("\(result.speakers.count) speakers detected") {
                        if result.speakers.contains(where: { $0.remembered_example_id != nil }) {
                            Text("Editing names or turns removes saved voice examples from this conversation. Confirm them again after correcting the speakers.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
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
        .refreshable { invalidate(); await load() }
        .sheet(item: $rememberSpeaker) { speaker in
            RememberVoiceSheet(speaker: speaker, profiles: voices?.profiles ?? []) { choice in
                mutate(path + "/" + speaker.id + "/remember", method: "POST", body: choice)
            }
        }
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
        .confirmationDialog(forgettingExample == nil ? "Forget \(forgettingVoice?.name ?? "saved voice")?" : "Remove saved example for \(forgettingVoice?.name ?? "this voice")?", isPresented: Binding(get: { forgettingVoice != nil }, set: { if !$0 { forgettingVoice = nil; forgettingExample = nil } }), titleVisibility: .visible) {
            Button(forgettingExample == nil ? "Forget voice" : "Remove example", role: .destructive) {
                guard let profile = forgettingVoice else { return }
                let example = forgettingExample
                forgettingVoice = nil; forgettingExample = nil
                forget(profile, example: example)
            }
        } message: { Text("This removes \(forgettingExample == nil ? "all saved examples for this voice" : "this example") from future recognition. Confirmed names in conversations stay unchanged.") }
    }

    private var savedVoicesSection: some View {
        Section {
            DisclosureGroup("Saved voices\(voices.map { " (\($0.profiles.count))" } ?? "")") {
                Text("Only examples you explicitly confirm are saved. Removing examples stops using them for recognition. Conversation names stay unchanged.")
                    .font(.caption).foregroundStyle(.secondary)
                if let voices {
                    if voices.profiles.isEmpty { Text("No saved voices in this archive.").foregroundStyle(.secondary) }
                    ForEach(voices.profiles) { profile in
                        DisclosureGroup {
                            Text("\(profile.example_count) examples · \(profile.conversation_count) conversations")
                                .font(.caption).foregroundStyle(.secondary)
                            ForEach(profile.examples) { example in
                                VStack(alignment: .leading, spacing: 6) {
                                    Text("\(Int(example.seconds.rounded()))s · \(example.scope.kind == "meeting" ? "Meeting" : "Conversation") \(String(example.scope.id.prefix(8)))")
                                        .font(.caption)
                                    Text(String(example.created_at.prefix(10))).font(.caption).foregroundStyle(.secondary)
                                    Button("Remove example", role: .destructive) { forgettingVoice = profile; forgettingExample = example }
                                        .buttonStyle(.borderless).disabled(busy)
                                }
                            }
                            Button("Forget voice", role: .destructive) { forgettingVoice = profile; forgettingExample = nil }
                                .buttonStyle(.borderless).disabled(busy)
                        } label: { Text(profile.name) }
                    }
                } else { Text(voiceError == nil ? "Loading saved voices…" : "Saved voices could not be loaded.").foregroundStyle(.secondary) }
            }
        }
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
            if let suggestion = speaker.suggestion, !dismissedSuggestions.contains(speaker.id) {
                Text("Suggested: \(suggestion.name). Listen first, then confirm the person.")
                    .font(.callout).foregroundStyle(.secondary)
                HStack {
                    Button("Confirm \(suggestion.name)") {
                        mutate(path + "/" + speaker.id + "/confirm", method: "POST", body: ["profile_id": suggestion.profile_id])
                    }.disabled(!editable || !result.audio_available || (result.recognition?.state ?? voices?.recognition.state) != "ready")
                    Button("Leave unchanged") { dismissedSuggestions.insert(speaker.id) }
                }.buttonStyle(.borderless)
            }
            if speaker.remembered_example_id != nil {
                Text("Saved as a voice example for future conversations.").font(.caption).foregroundStyle(.secondary)
            }
            Button("Remember speaker for future conversations", systemImage: "person.crop.circle.badge.checkmark") {
                rememberSpeaker = speaker
            }.buttonStyle(.borderless)
                .disabled(!editable || !result.audio_available || voices == nil || (result.recognition?.state ?? voices?.recognition.state ?? "disabled") == "disabled")
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
        rememberSpeaker = nil; forgettingVoice = nil; forgettingExample = nil; dismissedSuggestions = []
        result = nil; voices = nil; voiceError = nil; stopPlayback()
    }

    private func load() async {
        guard !busy else { return }
        let identity = model.archiveIdentity, request = UUID()
        requestID = request; busy = true; activity = "Loading speakers…"; error = nil
        defer { if requestID == request { busy = false } }
        do {
            let client = try model.client()
            async let scopeRequest = client.decoded(path, as: ConversationSpeakers.self)
            async let directoryRequest = client.decoded("v1/voice-profiles", as: SavedVoiceDirectory.self)
            let loaded = try await scopeRequest
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
            result = loaded
            do {
                let directory = try await directoryRequest
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                voices = directory; voiceError = nil
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                voiceError = error.localizedDescription
            }
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
                do {
                    let directory = try await client.decoded("v1/voice-profiles", as: SavedVoiceDirectory.self)
                    guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                    voices = directory; voiceError = nil
                } catch {
                    guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                    voiceError = error.localizedDescription
                }
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                self.error = error.localizedDescription
                if (error as? BackendFailure)?.status == 409 {
                    let refreshed = try? await client.decoded(path, as: ConversationSpeakers.self)
                    let directory = try? await client.decoded("v1/voice-profiles", as: SavedVoiceDirectory.self)
                    guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                    self.result = refreshed; voices = directory
                }
            }
        }
    }

    private func forget(_ profile: SavedVoiceDirectory.Profile, example: SavedVoiceDirectory.Profile.Example?) {
        guard !busy, let voices else { return }
        let identity = model.archiveIdentity, request = UUID()
        let client: BackendClient
        do { client = try model.client() }
        catch { voiceError = error.localizedDescription; return }
        let endpoint = "v1/voice-profiles/" + profile.id + (example.map { "/examples/" + $0.id } ?? "")
        requestID = request; busy = true; activity = "Removing saved voice…"; voiceError = nil; stopPlayback()
        operation = Task {
            defer { if requestID == request { busy = false; operation = nil } }
            do {
                try Task.checkCancellation()
                let directory = try await client.decoded(endpoint, method: "DELETE", query: ["revision": voices.revision], as: SavedVoiceDirectory.self)
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                self.voices = directory
                let scope = try await client.decoded(path, as: ConversationSpeakers.self)
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                result = scope
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                voiceError = error.localizedDescription
                if (error as? BackendFailure)?.status == 409 {
                    let directory = try? await client.decoded("v1/voice-profiles", as: SavedVoiceDirectory.self)
                    guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity else { return }
                    self.voices = directory
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

private struct RememberVoiceSheet: View {
    let speaker: ConversationSpeakers.Speaker
    let profiles: [SavedVoiceDirectory.Profile]
    let onConfirm: ([String: Any]) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var selectedProfile = ""
    @State private var newName: String

    init(speaker: ConversationSpeakers.Speaker, profiles: [SavedVoiceDirectory.Profile], onConfirm: @escaping ([String: Any]) -> Void) {
        self.speaker = speaker; self.profiles = profiles; self.onConfirm = onConfirm
        _newName = State(initialValue: speaker.name ?? "")
        _selectedProfile = State(initialValue: speaker.voice_profile_id ?? "")
    }

    private var trimmedName: String { newName.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("After listening, explicitly choose whose voice \(speaker.displayName) is. This saves a voice example and names the speaker in this conversation.")
                    Text("Future conversations may suggest this voice; you confirm every suggestion. A new profile remains separate even when its name matches another profile.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("Save voice to") {
                    Picker("Voice profile", selection: $selectedProfile) {
                        Text("Create a new voice profile").tag("")
                        ForEach(profiles) { profile in
                            Text("\(profile.name) · \(profile.example_count) examples").tag(profile.id)
                        }
                    }
                    if selectedProfile.isEmpty {
                        TextField("New voice profile name", text: $newName).textInputAutocapitalization(.words)
                        if trimmedName.count > 120 { Text("Use a name up to 120 characters.").foregroundStyle(.red) }
                    }
                }
                Section {
                    Button("Confirm and remember voice") {
                        onConfirm(selectedProfile.isEmpty ? ["name": trimmedName] : ["profile_id": selectedProfile])
                        dismiss()
                    }.disabled(selectedProfile.isEmpty && (trimmedName.isEmpty || trimmedName.count > 120))
                }
            }
            .navigationTitle("Remember voice").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }
}

import SwiftUI
import OmiCore

struct RewindView: View {
    @Environment(AppModel.self) private var model
    @State private var minutes = 10
    @State private var summarize = true
    @State private var result: JSONValue = .null
    @State private var windowEnd: Date?
    @State private var error: String?
    @State private var loading = false
    @State private var localMessage: String?
    @State private var requestID = UUID()
    @State private var operation: Task<Void, Never>?
    @State private var playing = false
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var sources: [JSONValue] { result["sources"].array }
    private var hasAudio: Bool {
        sources.contains { $0["audio_available"] != .bool(false) && $0["audio_availability"].text != "missing" }
    }
    var body: some View {
        List {
            Section {
                EditorialTitle(eyebrow: "Catch up", title: "What did I miss?").editorialRow()
            }
            Section {
                Stepper("Last \(minutes) \(minutes == 1 ? "minute" : "minutes")", value: $minutes, in: 1...60)
                    .disabled(loading).accessibilityIdentifier("rewind.minutes")
                Toggle("Include a quick recap", isOn: $summarize).disabled(loading)
                    .accessibilityIdentifier("rewind.includeSummary")
                Button { operation = Task { await rewind() } } label: { Label("Rewind", systemImage: "gobackward") }
                    .buttonStyle(PrimaryActionStyle()).disabled(loading).accessibilityIdentifier("rewind.generate")
                Button { playLocal() } label: { Label("Play phone buffer", systemImage: "play.circle") }
                    .disabled(loading)
                if loading { ProgressView("Asking home-core…") }
                ErrorNote(message: error)
                if let localMessage { Text(localMessage).font(.callout).foregroundStyle(.secondary) }
                if let end = windowEnd { Text("Window ends \(diary.label(end, template: "d MMM yyyy HHmmss")) · \(diary.calendar.timeZone.identifier)").font(.caption).foregroundStyle(.secondary) }
                if playing {
                    Button("Stop playback", systemImage: "stop.circle") { stopPlayback() }
                    Text(model.playbackStatus).font(.caption).foregroundStyle(.secondary)
                }
            } footer: { Text("The phone buffer works while home-core is offline. Quick recaps need your local language model.") }
            if case .object = result {
                if sources.isEmpty {
                    Section {
                        ContentUnavailableView("No recordings in this window", systemImage: "gobackward", description: Text("Try a longer window, or connect your iPhone to send queued recordings."))
                    }
                } else {
                    Section("Listen back") {
                        if hasAudio {
                            Button("Play home-core audio", systemImage: "play.circle") { operation = Task { await playServer() } }
                                .disabled(loading).accessibilityIdentifier("rewind.playServer")
                            Text("Audio is clipped to this window. Gaps remain silent.").font(.caption).foregroundStyle(.secondary)
                        } else {
                            Text("Original audio is unavailable for these sources. Their transcripts are shown below.")
                                .foregroundStyle(.secondary)
                        }
                        if sources.contains(where: { $0["source_kind"].text == "omi_audio_webhook" }) {
                            Text("Official audio times estimate server receipt. Bursts or multiple senders can overlap in combined playback. Play individual sources to hear every accepted chunk; this does not establish continuous capture.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if !result["summary"].text.isEmpty {
                        Section("What you heard") {
                            Text(.init(result["summary"].text)).textSelection(.enabled)
                            EvidenceFeedbackView(outputID: result["output_id"].text, evidence: result["evidence"], outputText: result["summary"].text)
                        }
                    } else if summarize {
                        Section { Text("No transcribed speech is available to summarize yet.").foregroundStyle(.secondary) }
                    }
                    Section("Recording coverage") {
                        if result["pending_chunks"].integer > 0 {
                            Text("\(result["pending_chunks"].integer) recordings were waiting for transcription at this snapshot. Run Rewind again to update.")
                                .font(.callout)
                        }
                        if !result["audio_gaps"].array.isEmpty {
                            Text("\(result["audio_gaps"].array.count) unavailable playback intervals").font(.callout).foregroundStyle(.secondary)
                        }
                        CompletenessView(value: result["completeness"])
                    }
                    Section {
                        SourceList(sources: sources)
                    } header: { Text("Recorded evidence") } footer: {
                        Text("Transcripts include whole overlapping recordings. Markers and meeting gestures are approximate context hints.")
                    }
                }
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle("Rewind").navigationBarTitleDisplayMode(.inline)
            .onChange(of: minutes) { _, _ in clear() }
            .onChange(of: summarize) { _, _ in clear() }
            .onChange(of: model.archiveIdentity + model.archiveRevision) { _, _ in clear() }
            .onDisappear { requestID = UUID(); operation?.cancel(); operation = nil; loading = false; stopPlayback() }
    }
    private func stopPlayback() {
        if playing { model.player?.stop(); model.playbackStatus = "Stopped"; playing = false }
    }
    private func clear() {
        requestID = UUID(); operation?.cancel(); operation = nil; loading = false
        result = .null; windowEnd = nil; localMessage = nil; error = nil; stopPlayback()
    }
    private func rewind() async {
        let identity = model.archiveIdentity, request = UUID()
        requestID = request; stopPlayback(); model.checkpoint()
        let end = Date(); windowEnd = end; result = .null
        error = nil; localMessage = nil; loading = true
        defer { if requestID == request { loading = false; operation = nil } }
        do {
            let response = try await model.client().request("v1/rewind", method: "POST", body: ["minutes": minutes, "end": DurableStore.stamp(end), "summarize": summarize])
            guard model.archiveIdentity == identity, !Task.isCancelled, requestID == request else { return }
            result = response
        } catch {
            guard model.archiveIdentity == identity, !Task.isCancelled, requestID == request else { return }
            self.error = error.localizedDescription
        }
    }
    private func playLocal() {
        let end = windowEnd ?? Date(); windowEnd = end; error = nil
        do {
            let gaps = try model.localRewind(minutes: minutes, end: end)
            playing = true; localMessage = "Playing the phone buffer. \(gaps) gaps are represented by silence."
        } catch { self.error = error.localizedDescription }
    }
    private func playServer() async {
        guard let end = windowEnd else { return }
        let identity = model.archiveIdentity, request = UUID()
        requestID = request; loading = true; error = nil
        defer { if requestID == request { loading = false; operation = nil } }
        do {
            let url = try await model.client().download("v1/rewind/audio", query: ["minutes": "\(minutes)", "end": DurableStore.stamp(end)])
            guard model.archiveIdentity == identity, !Task.isCancelled, requestID == request else { return }
            model.play(url); playing = true
        } catch {
            guard model.archiveIdentity == identity, !Task.isCancelled, requestID == request else { return }
            self.error = error.localizedDescription
        }
    }
}

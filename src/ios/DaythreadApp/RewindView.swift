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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Section {
                Stepper(minutes == 1 ? L10n.text("Last \(minutes) minute") : L10n.text("Last \(minutes) minutes"), value: $minutes, in: 1...60)
                    .disabled(loading).accessibilityIdentifier("rewind.minutes")
                Toggle(L10n.text("Include a quick recap"), isOn: $summarize).disabled(loading)
                    .accessibilityIdentifier("rewind.includeSummary")
                Button { operation = Task { await rewind() } } label: { Label(L10n.text("Rewind"), systemImage: "gobackward") }
                    .buttonStyle(PrimaryActionStyle()).disabled(loading).accessibilityIdentifier("rewind.generate")
                Button { playLocal() } label: { Label(L10n.text("Play phone buffer"), systemImage: "play.circle") }
                    .disabled(loading)
                if loading { ProgressView(L10n.text("Asking home-core…")) }
                ErrorNote(message: error)
                if let localMessage { Text(localMessage).font(.callout).foregroundStyle(.secondary) }
                if let end = windowEnd { Text(L10n.text("Window ends \(diary.label(end, template: "d MMM yyyy HHmmss")) · \(diary.calendar.timeZone.identifier)")).font(.caption).foregroundStyle(.secondary) }
                if playing {
                    Button(L10n.text("Stop playback"), systemImage: "stop.circle") { stopPlayback() }
                    Text(L10n.key(model.playbackStatus)).font(.caption).foregroundStyle(.secondary)
                }
            } footer: { Text(L10n.text("The phone buffer works while home-core is offline. Quick recaps need your local language model.")) }
            if case .object = result {
                if sources.isEmpty {
                    Section {
                        ContentUnavailableView(L10n.text("No recordings in this window"), systemImage: "gobackward", description: Text(L10n.text("Try a longer window, or connect your iPhone to send queued recordings.")))
                    }
                } else {
                    Section(L10n.text("Listen back")) {
                        if hasAudio {
                            Button(L10n.text("Play home-core audio"), systemImage: "play.circle") { operation = Task { await playServer() } }
                                .disabled(loading).accessibilityIdentifier("rewind.playServer")
                            Text(L10n.text("Audio is clipped to this window. Gaps remain silent.")).font(.caption).foregroundStyle(.secondary)
                        } else {
                            Text(L10n.text("Original audio is unavailable for these sources. Their transcripts are shown below."))
                                .foregroundStyle(.secondary)
                        }
                        if sources.contains(where: { $0["source_kind"].text == "omi_audio_webhook" }) {
                            Text(L10n.text("Official audio times estimate server receipt. Bursts or multiple senders can overlap in combined playback. Play individual sources to hear every accepted chunk; this does not establish continuous capture."))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if !result["summary"].text.isEmpty {
                        Section(L10n.text("What you heard")) {
                            SummaryTranslationView(output: result) {
                                Text(.init(result["summary"].text)).textSelection(.enabled)
                            }
                            EvidenceFeedbackView(outputID: result["output_id"].text, evidence: result["evidence"], outputText: result["summary"].text)
                        }
                    } else if summarize {
                        Section { Text(L10n.text("No transcribed speech is available to summarize yet.")).foregroundStyle(.secondary) }
                    }
                    Section(L10n.text("Recording coverage")) {
                        if result["pending_chunks"].integer > 0 {
                            Text(L10n.text("\(result["pending_chunks"].integer) recordings were waiting for transcription at this snapshot. Run Rewind again to update."))
                                .font(.callout)
                        }
                        if !result["audio_gaps"].array.isEmpty {
                            Text(L10n.text("\(result["audio_gaps"].array.count) unavailable playback intervals")).font(.callout).foregroundStyle(.secondary)
                        }
                        CompletenessView(value: result["completeness"])
                    }
                    Section {
                        SourceList(sources: sources)
                    } header: { Text(L10n.text("Recorded evidence")) } footer: {
                        Text(L10n.text("Transcripts include whole overlapping recordings. Markers and meeting gestures are approximate context hints."))
                    }
                }
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle(L10n.text("Rewind")).navigationBarTitleDisplayMode(.large)
            .onChange(of: minutes) { _, _ in clear() }
            .onChange(of: summarize) { _, _ in clear() }
            .onChange(of: model.archiveIdentity + model.archiveRevision) { _, _ in clear() }
            .onDisappear { requestID = UUID(); operation?.cancel(); operation = nil; loading = false; stopPlayback() }
    }
    private func stopPlayback() {
        if playing { model.stopPlayback(); model.playbackStatus = L10n.text("Stopped"); playing = false }
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
            self.error = L10n.error(error)
        }
    }
    private func playLocal() {
        let end = windowEnd ?? Date(); windowEnd = end; error = nil
        do {
            let gaps = try model.localRewind(minutes: minutes, end: end)
            playing = true; localMessage = L10n.text("Playing the phone buffer. \(gaps) gaps are represented by silence.")
        } catch { self.error = L10n.error(error) }
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
            self.error = L10n.error(error)
        }
    }
}

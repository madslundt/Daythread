import SwiftUI
import OmiCore

struct RewindView: View {
    @Environment(AppModel.self) private var model
    @State private var minutes = 10
    @State private var result: JSONValue = .null
    @State private var windowEnd: Date?
    @State private var error: String?
    @State private var loading = false
    @State private var localMessage: String?
    var body: some View {
        List {
            Section {
                EditorialTitle(eyebrow: "Catch up", title: "What did I miss?", subtitle: "Return to the last few minutes, at your own pace.")
                    .editorialRow()
            }
            Section {
                Stepper("Last \(minutes) minutes", value: $minutes, in: 1...60).disabled(loading)
                Button { Task { await rewind() } } label: { Label("Rewind & summarize", systemImage: "gobackward") }
                    .buttonStyle(PrimaryActionStyle()).disabled(loading).accessibilityIdentifier("rewind.generate")
                Button { playLocal() } label: { Label("Play phone buffer", systemImage: "play.circle") }
                if loading { ProgressView("Asking home-core…") }
                ErrorNote(message: error)
                if let localMessage { Text(localMessage).font(.callout).foregroundStyle(.secondary) }
                if let end = windowEnd { Text("Window ends \(end.formatted(date: .abbreviated, time: .standard))").font(.caption).foregroundStyle(.secondary) }
            } footer: { Text("The phone buffer works while home-core is offline. Summaries need your local STT and language model.") }
            if case .object = result {
                Section("Rewind") {
                    if !result["summary"].text.isEmpty { Text(.init(result["summary"].text)).textSelection(.enabled) }
                    else { Text("No summary available yet.").foregroundStyle(.secondary) }
                    if result["pending_chunks"].integer > 0 { Text("\(result["pending_chunks"].integer) chunks still transcribing").font(.callout) }
                    Text("\(result["audio_gaps"].array.count) unavailable playback intervals").font(.callout).foregroundStyle(.secondary)
                    CompletenessView(value: result["completeness"])
                    EvidenceFeedbackView(outputID: result["output_id"].text, evidence: result["evidence"], outputText: result["summary"].text)
                    Button("Play home-core audio") { Task { await playServer() } }
                    if model.player != nil {
                        Button("Stop playback") { model.player?.stop(); model.playbackStatus = "Stopped" }
                        Text(model.playbackStatus).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Section("Recorded evidence") { SourceList(sources: result["sources"].array) }
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle("Rewind").navigationBarTitleDisplayMode(.inline)
            .onChange(of: minutes) { _, _ in result = .null; windowEnd = nil; localMessage = nil; error = nil }
    }
    private func rewind() async {
        let identity = model.archiveIdentity
        model.checkpoint(); let end = Date(); windowEnd = end; result = .null
        error = nil; localMessage = nil; loading = true; defer { loading = false }
        do {
            let response = try await model.client().request("v1/rewind", method: "POST", body: ["minutes": minutes, "end": DurableStore.stamp(end), "summarize": true])
            guard model.archiveIdentity == identity, !Task.isCancelled else { return }
            result = response
        } catch { self.error = error.localizedDescription }
    }
    private func playLocal() {
        let end = windowEnd ?? Date(); windowEnd = end
        do {
            let gaps = try model.localRewind(minutes: minutes, end: end)
            localMessage = "Playing the phone buffer. \(gaps) gaps are represented by silence."
        } catch { self.error = error.localizedDescription }
    }
    private func playServer() async {
        guard let end = windowEnd else { return }
        let identity = model.archiveIdentity
        do {
            let url = try await model.client().download("v1/rewind/audio", query: ["minutes": "\(minutes)", "end": DurableStore.stamp(end)])
            guard model.archiveIdentity == identity, !Task.isCancelled else { return }
            model.play(url)
        } catch { self.error = error.localizedDescription }
    }
}

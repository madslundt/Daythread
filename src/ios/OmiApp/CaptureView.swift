import SwiftUI
import OmiCore

struct CaptureView: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        List {
            Section {
                EditorialTitle(eyebrow: "Omi Local", title: "Keep the day.")
                Label(model.bluetooth.status, systemImage: model.bluetooth.connected ? "circle.fill" : "antenna.radiowaves.left.and.right")
                    .foregroundStyle(model.bluetooth.connected && !model.paused ? .green : .secondary)
                if model.bluetooth.connected {
                    LabeledContent("Device", value: model.bluetooth.name)
                    LabeledContent("Battery", value: model.bluetooth.battery.map { "\($0)%" } ?? "Reading…")
                    LabeledContent("Format", value: codecName)
                    Button { model.setPaused(!model.paused) } label: {
                        Label(model.paused ? "Resume capture" : "Pause capture", systemImage: model.paused ? "record.circle" : "pause.circle")
                    }.accessibilityIdentifier("capture.toggle")
                } else {
                    Button { model.bluetooth.scan() } label: { Label("Find Omi", systemImage: "dot.radiowaves.left.and.right") }
                        .accessibilityIdentifier("pair.scan")
                    ForEach(model.bluetooth.devices, id: \.identifier) { device in
                        Button(device.name ?? "Omi · \(device.identifier.uuidString.prefix(8))") { model.bluetooth.connect(device) }
                    }
                    if model.bluetooth.scanning { ProgressView("Scanning for nearby pendants") }
                }
            }
            Section {
                Button { model.event(.mark) } label: {
                    Label(markLabel, systemImage: "bookmark")
                }.disabled(model.nextMarkAt.map { model.now < $0 } ?? false).accessibilityIdentifier("hint.mark")
                Button { model.event(.meetingToggle) } label: {
                    Label(model.meetingStart == nil ? "Start meeting hint" : "End meeting hint", systemImage: "person.2")
                }.accessibilityIdentifier("hint.meeting")
                if model.meetingStart != nil, let deadline = model.meetingDeadline {
                    Text(model.meetingQualifiesAt.map { model.now < $0 } == true ? "Provisional meeting · short meetings are ignored" : "Meeting hint active")
                        .font(.callout).foregroundStyle(Theme.accent)
                    LabeledContent("Auto-stop", value: deadline.formatted(date: .omitted, time: .shortened))
                }
                Text(model.notice).font(.callout).foregroundStyle(.secondary)
            } header: { Text("Nearby speech") } footer: {
                Text("Flags and meeting controls hint at surrounding speech. A single click adds a flag once per minute. \(model.gesture == .doubleClick ? "Double-click" : "Verified hold") toggles a meeting.")
            }
            Section {
                Text(model.serverStatus).font(.callout).textSelection(.enabled)
                if statusStale { Text("Current backend state unknown; showing the last successful observation.").font(.caption).foregroundStyle(.orange) }
                Text("Backend observed: \(model.completenessStatus["as_of"].text)").font(.caption)
                Text("Last server receipt: \(model.completenessStatus["ingestion"]["last_received_at"].text.isEmpty ? "unknown" : model.completenessStatus["ingestion"]["last_received_at"].text)").font(.caption)
                Text("\(model.completenessStatus["processing"]["queued"].integer) queued (\(model.completenessStatus["processing"]["retrying"].integer) retrying) · \(model.completenessStatus["processing"]["transcribing"].integer) transcribing · \(model.completenessStatus["processing"]["blocked"].integer) blocked").font(.caption)
                LabeledContent("Last decoded audio", value: model.lastDecodedAt?.formatted() ?? "Unknown this session")
                LabeledContent("Last persisted audio", value: model.lastPersistedAt?.formatted() ?? "Unknown this session")
                if let failure = model.captureFailure { Text("Capture stopped: \(failure). Resume explicitly after resolving the failure.").font(.caption).foregroundStyle(.red) }
                Text("A saved archive or connection does not establish continuous capture. Backend values show the timestamp of the last successful check.").font(.caption).foregroundStyle(.secondary)
                NavigationLink("Feedback and repairs") { FeedbackReviewView() }
                LabeledContent("Waiting to sync", value: "\(model.queueCount) items · \(model.unsyncedMinutes.formatted(.number.precision(.fractionLength(1)))) min")
                LabeledContent("Phone storage", value: "\(model.storageMB.formatted(.number.precision(.fractionLength(1)))) MB / \(model.storageBudgetMB) MB")
                Button("Sync now") { model.checkpoint(); model.retryUploads(); Task { await model.refreshStatus() } }
                ForEach(Array(model.queueErrors.enumerated()), id: \.offset) { _, message in Text(message).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
            } header: { Text("Home-core") } footer: { Text("Unsent audio stays on this iPhone. Synced audio remains available for local rewind for one hour. Capture stops if the storage budget fills.") }
            if !model.recentGaps.isEmpty {
                Section("Recent capture gaps") {
                    ForEach(model.recentGaps) { gap in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(gap.capturedAt.formatted(date: .abbreviated, time: .shortened)).font(.caption)
                            Text(gap.reason ?? "Capture interrupted").font(.callout)
                        }.foregroundStyle(.secondary)
                    }
                }
            }
        }.listStyle(.insetGrouped).navigationTitle("Capture").navigationBarTitleDisplayMode(.inline)
            .refreshable { await model.refreshStatus() }
    }
    private var statusStale: Bool {
        guard let at = AppModel.date(model.completenessStatus["as_of"].text) else { return true }
        return model.now.timeIntervalSince(at) > 120 || !model.serverStatus.hasPrefix("Home-core connected")
    }
    private var markLabel: String {
        if let next = model.nextMarkAt, next > model.now { return "Flag added · wait \(Int(ceil(next.timeIntervalSince(model.now))))s" }
        return "Mark important"
    }
    private var codecName: String {
        switch model.bluetooth.codec {
        case 20: "Opus · 16 kHz · 10 ms"
        case 21: "Opus · 16 kHz · 20 ms"
        case 0: "PCM16 · 16 kHz"
        case .some(let id): "Unsupported codec \(id)"
        case .none: "Reading…"
        }
    }
}

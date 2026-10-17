import SwiftUI
import OmiCore

struct CaptureView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Section {
                EditorialTitle(eyebrow: "Daythread", title: L10n.text("Keep the day."))
                    .editorialRow()
            }
            PhoneRecordingSection()
            Section {
                HStack(alignment: .top, spacing: 14) {
                    Image(systemName: model.bluetooth.connected ? "waveform" : "antenna.radiowaves.left.and.right")
                        .font(.title2).foregroundStyle(Theme.accent)
                        .frame(width: 48, height: 48).background(Theme.accent.opacity(0.10), in: RoundedRectangle(cornerRadius: 14))
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 5) {
                        Text(L10n.key(model.bluetooth.status)).font(.headline)
                        Text(model.bluetooth.connected ? (model.paused ? L10n.text("Capture is paused. Resume when you’re ready.") : L10n.text("Audio is saved on this iPhone before uploading.")) : L10n.text("Keep your Omi nearby and powered on."))
                            .font(.subheadline).foregroundStyle(.secondary)
                    }.fixedSize(horizontal: false, vertical: true)
                }.padding(.vertical, 6)
                if model.bluetooth.connected {
                    DisclosureGroup(L10n.text("Pendant details")) {
                        LabeledContent(L10n.text("Device"), value: model.bluetooth.name)
                        LabeledContent(L10n.text("Battery"), value: model.bluetooth.battery.map { "\($0)%" } ?? L10n.text("Reading…"))
                        LabeledContent(L10n.text("Format"), value: codecName)
                    }
                    Button { model.setPaused(!model.paused) } label: {
                        Label(model.paused ? L10n.text("Resume capture") : L10n.text("Pause capture"), systemImage: model.paused ? "record.circle" : "pause.circle")
                    }.buttonStyle(PrimaryActionStyle()).accessibilityIdentifier("capture.toggle")
                } else {
                    Button { model.bluetooth.scan() } label: { Label(L10n.text("Find Omi"), systemImage: "dot.radiowaves.left.and.right") }
                        .buttonStyle(PrimaryActionStyle())
                        .accessibilityIdentifier("pair.scan")
                    ForEach(model.bluetooth.devices, id: \.identifier) { device in
                        Button(device.name ?? L10n.text("Omi · \(device.identifier.uuidString.prefix(8))")) { model.bluetooth.connect(device) }
                    }
                    if model.bluetooth.scanning { ProgressView(L10n.text("Scanning for nearby pendants")) }
                }
            }
            Section {
                Button { model.event(.mark) } label: {
                    Label(markLabel, systemImage: "bookmark")
                }.disabled(model.nextMarkAt.map { model.now < $0 } ?? false).accessibilityIdentifier("hint.mark")
                Button { model.event(.meetingToggle) } label: {
                    Label(model.meetingStart == nil ? L10n.text("Start meeting hint") : L10n.text("End meeting hint"), systemImage: "person.2")
                }.accessibilityIdentifier("hint.meeting")
                if model.meetingStart != nil, let deadline = model.meetingDeadline {
                    Text(model.meetingQualifiesAt.map { model.now < $0 } == true ? L10n.text("Provisional meeting · short meetings are ignored") : L10n.text("Meeting hint active"))
                        .font(.callout).foregroundStyle(Theme.accent)
                    LabeledContent(L10n.text("Auto-stop"), value: deadline.formatted(.dateTime.locale(L10n.locale).hour().minute()))
                }
                if !model.notice.isEmpty { Text(L10n.key(model.notice)).font(.callout).foregroundStyle(.secondary) }
            } header: { Text(L10n.text("Nearby speech")) } footer: {
                Text(L10n.text("Flags and meeting controls hint at surrounding speech. A single click adds a flag once per minute. \(model.gesture == .doubleClick ? L10n.text("Double-click") : L10n.text("Verified hold")) toggles a meeting."))
            }
            Section {
                Text(L10n.key(model.serverStatus)).font(.callout).textSelection(.enabled)
                if statusStale { Text(L10n.text("Current backend state unknown; showing the last successful observation.")).font(.caption).foregroundStyle(.orange) }
                if let failure = model.captureFailure { Text(L10n.text("Capture stopped: \(L10n.key(failure)). Resume explicitly after resolving the failure.")).font(.caption).foregroundStyle(.red) }
                LabeledContent(L10n.text("Waiting to sync"), value: L10n.text("\(model.queueCount) items · \(model.unsyncedMinutes.formatted(.number.locale(L10n.locale).precision(.fractionLength(1)))) min"))
                if let upload = model.uploadFeedback {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(L10n.key(upload.title)).font(.subheadline.weight(.medium))
                        if let fraction = upload.fraction {
                            ProgressView(value: fraction)
                                .accessibilityLabel(L10n.text("Current upload"))
                            Text(L10n.text("Current item: \(Int(fraction * 100))% · \(bytes(upload.sentBytes)) of \(bytes(upload.totalBytes))"))
                                .font(.caption).monospacedDigit()
                        }
                        Text(L10n.text("\(bytes(upload.remainingBytes)) left in upload queue"))
                            .font(.caption).foregroundStyle(.secondary)
                        Text(upload.estimatedSeconds.map { L10n.text("About \(duration($0)) to upload queued items") } ?? L10n.text("Upload time estimate unavailable until transfer speed is measured."))
                            .font(.caption).foregroundStyle(.secondary)
                    }.accessibilityElement(children: .combine)
                }
                processingFeedback
                LabeledContent(L10n.text("Phone storage"), value: "\(model.storageMB.formatted(.number.locale(L10n.locale).precision(.fractionLength(1)))) MB / \(model.storageBudgetMB) MB")
                ProgressView(value: min(model.storageMB, Double(model.storageBudgetMB)), total: Double(model.storageBudgetMB))
                    .accessibilityLabel(L10n.text("Phone storage used")).accessibilityValue(L10n.text("\(Int(model.storageMB)) of \(model.storageBudgetMB) megabytes"))
                Button { model.checkpoint(); model.retryUploads(); Task { await model.refreshStatus() } } label: {
                    Label(L10n.text("Sync now"), systemImage: "arrow.triangle.2.circlepath")
                }
                ForEach(Array(model.queueErrors.enumerated()), id: \.offset) { _, message in Text(L10n.key(message)).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                DisclosureGroup(L10n.text("Capture & sync details")) {
                    Text(L10n.text("Backend observed: \(model.completenessStatus["as_of"].text.isEmpty ? L10n.text("not yet checked") : model.completenessStatus["as_of"].text)")).font(.caption)
                    Text(L10n.text("Last server receipt: \(model.completenessStatus["ingestion"]["last_received_at"].text.isEmpty ? L10n.text("unknown") : model.completenessStatus["ingestion"]["last_received_at"].text)")).font(.caption)
                    Text(L10n.text("\(model.completenessStatus["processing"]["queued"].integer) queued (\(model.completenessStatus["processing"]["retrying"].integer) retrying) · \(model.completenessStatus["processing"]["transcribing"].integer) transcribing · \(model.completenessStatus["processing"]["blocked"].integer) blocked")).font(.caption)
                    LabeledContent(L10n.text("Last decoded audio"), value: model.lastDecodedAt?.formatted(.dateTime.locale(L10n.locale)) ?? L10n.text("Unknown this session"))
                    LabeledContent(L10n.text("Last persisted audio"), value: model.lastPersistedAt?.formatted(.dateTime.locale(L10n.locale)) ?? L10n.text("Unknown this session"))
                    Text(L10n.text("A saved archive or connection does not establish continuous capture. Backend values show the timestamp of the last successful check.")).font(.caption).foregroundStyle(.secondary)
                }
                NavigationLink(L10n.text("Feedback and repairs")) { FeedbackReviewView() }
            } header: { Text(L10n.text("Home-core")) } footer: { Text(L10n.text("Unsent audio stays on this iPhone. Synced audio remains available for local rewind for one hour. Capture stops if the storage budget fills.")) }
            if !model.recentGaps.isEmpty {
                Section(L10n.text("Recent capture gaps")) {
                    ForEach(model.recentGaps) { gap in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(gap.capturedAt.formatted(.dateTime.locale(L10n.locale).day().month(.abbreviated).year().hour().minute())).font(.caption)
                            Text(L10n.key(gap.reason ?? L10n.text("Capture interrupted"))).font(.callout)
                        }.foregroundStyle(.secondary)
                    }
                }
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle(L10n.text("Capture")).navigationBarTitleDisplayMode(.inline)
            .refreshable { await model.refreshStatus() }
            .task(id: model.archiveIdentity) { model.microphone.selectArchive(model.archiveIdentity) }
    }
    private var statusStale: Bool {
        guard let at = AppModel.date(model.completenessStatus["as_of"].text) else { return true }
        return model.now.timeIntervalSince(at) > 120 || !model.serverStatus.hasPrefix(L10n.text("Home-core connected"))
    }
    @ViewBuilder
    private var processingFeedback: some View {
        let processing = model.completenessStatus["processing"]
        let active = processing["active"].array
        let waiting = processing["queued"].integer
        if !active.isEmpty || waiting > 0 || processing["blocked"].integer > 0 {
            VStack(alignment: .leading, spacing: 8) {
                Text(L10n.text("Processing · \(active.count) active · \(waiting) queued")).font(.subheadline.weight(.medium))
                if model.completenessStatus["worker_running"] == .bool(false) {
                    Text(L10n.text("Processing is waiting for the worker to start.")).font(.caption).foregroundStyle(.secondary)
                }
                ForEach(Array(active.enumerated()), id: \.offset) { _, item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(stageName(item["stage"].text)).font(.caption.weight(.medium))
                        let total = item["total_chunks"].integer
                        let completed = max(0, min(total, item["completed_chunks"].integer))
                        if total > 0 {
                            ProgressView(value: Double(completed), total: Double(total))
                                .accessibilityLabel(stageName(item["stage"].text))
                            Text(L10n.text("\(completed) of \(total) parts complete")).font(.caption).monospacedDigit()
                        } else {
                            ProgressView().controlSize(.small)
                        }
                        if case .number(let elapsed) = item["elapsed_seconds"], elapsed > 0 {
                            Text(L10n.text("Running for \(duration(elapsed))")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if !statusStale, model.completenessStatus["worker_running"] == .bool(true),
                   case .number(let seconds) = processing["estimated_remaining_seconds"], seconds >= 0, seconds.isFinite {
                    Text(L10n.text("About \(duration(seconds)) to finish the processing queue"))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(L10n.text("Processing time estimate unavailable yet.")).font(.caption).foregroundStyle(.secondary)
                }
                if processing["blocked"].integer > 0 {
                    Text(L10n.text("\(processing["blocked"].integer) items need attention.")).font(.caption).foregroundStyle(.orange)
                }
            }
        }
    }
    private func bytes(_ count: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: count, countStyle: .file)
    }
    private func duration(_ seconds: Double) -> String {
        let rounded = max(1, Int(ceil(seconds)))
        if rounded < 60 { return L10n.text("\(rounded) sec") }
        if rounded < 3600 { return L10n.text("\(Int(ceil(Double(rounded) / 60))) min") }
        let hours = rounded / 3600, minutes = Int(ceil(Double(rounded % 3600) / 60))
        return minutes == 0 ? L10n.text("\(hours) hr") : L10n.text("\(hours) hr \(minutes) min")
    }
    private func stageName(_ stage: String) -> String {
        switch stage {
        case "analyzing": L10n.text("Analyzing audio")
        case "transcribing": L10n.text("Transcribing audio")
        case "enhancing": L10n.text("Improving transcript")
        case "saving": L10n.text("Saving transcript")
        default: L10n.text("Processing audio")
        }
    }
    private var markLabel: String {
        if let next = model.nextMarkAt, next > model.now { return L10n.text("Flag added · wait \(Int(ceil(next.timeIntervalSince(model.now))))s") }
        return L10n.text("Mark important")
    }
    private var codecName: String {
        switch model.bluetooth.codec {
        case 20: L10n.text("Opus · 16 kHz · 10 ms")
        case 21: L10n.text("Opus · 16 kHz · 20 ms")
        case 0: L10n.text("PCM16 · 16 kHz")
        case .some(let id): L10n.text("Unsupported codec \(id)")
        case .none: L10n.text("Reading…")
        }
    }
}

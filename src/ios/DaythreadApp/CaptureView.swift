import SwiftUI
import OmiCore

struct CaptureView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.locale) private var interfaceLocale

    var body: some View {
        let _ = interfaceLocale
        List {
            PhoneRecordingSection()
            if let failure = model.captureFailure {
                Section {
                    Text(L10n.text("Capture stopped: \(L10n.key(failure)). Resume explicitly after resolving the failure."))
                        .font(.callout).foregroundStyle(.red)
                }
            }
            Section {
                DisclosureGroup {
                    RecordingProcessingActivity()
                } label: {
                    LabeledContent(L10n.text("Processing"), value: processingSummary)
                }.accessibilityIdentifier("capture.processing")
            }
            syncSection
            Section {
                NavigationLink {
                    Form { RecordingScheduleSection() }
                        .omiScreen().navigationTitle(L10n.text("Recording schedule"))
                        .navigationBarTitleDisplayMode(.inline)
                } label: {
                    LabeledContent(L10n.text("Recording schedule"), value: scheduleSummary)
                }.accessibilityIdentifier("capture.schedule")
                NavigationLink { pendantControls } label: {
                    LabeledContent(L10n.text("Omi pendant"), value: model.bluetooth.connected ? model.bluetooth.name : L10n.text("Not connected"))
                }.accessibilityIdentifier("capture.pendant")
                NavigationLink { uploadSettings } label: {
                    Label(L10n.text("Upload settings"), systemImage: "arrow.up.circle")
                }.accessibilityIdentifier("capture.uploadSettings")
                DisclosureGroup { speechControls } label: {
                    if model.meetingStart != nil {
                        LabeledContent(L10n.text("Nearby speech"), value: L10n.text("Meeting hint active"))
                    } else { Text(L10n.text("Nearby speech")) }
                }
            } header: { Text(L10n.text("Recording options")) }
        }.listStyle(.insetGrouped).omiScreen()
            .navigationTitle(L10n.text("Capture")).navigationBarTitleDisplayMode(.large)
            .refreshable { await model.refreshStatus() }
            .task(id: model.archiveIdentity) { model.microphone.selectArchive(model.archiveIdentity) }
    }

    private var syncSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 8) {
                LabeledContent(L10n.text("Waiting to sync"), value: L10n.text("\(model.queueCount) items · \(model.unsyncedMinutes.formatted(.number.locale(L10n.locale).precision(.fractionLength(1)))) min"))
                if let upload = model.uploadFeedback {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(L10n.key(upload.title)).font(.subheadline)
                        if let fraction = upload.fraction {
                            ProgressView(value: fraction).accessibilityLabel(L10n.text("Current upload"))
                        }
                    }
                }
                if let message = model.queueErrors.first {
                    Text(L10n.key(message)).font(.caption).foregroundStyle(.red).lineLimit(2)
                }
            }.padding(.vertical, 4)
            if model.queueCount > 0 || !model.queueErrors.isEmpty {
                Button { model.checkpoint(); model.retryUploads(); Task { await model.refreshStatus() } } label: {
                    Label(L10n.text("Sync now"), systemImage: "arrow.triangle.2.circlepath")
                }
            }
            NavigationLink(L10n.text("Capture & sync details")) { syncDetails }
                .accessibilityIdentifier("capture.syncDetails")
        } header: { Text(L10n.text("Sync")) }
    }

    private var pendantControls: some View {
        List {
            Section {
                Label(L10n.key(model.bluetooth.status), systemImage: model.bluetooth.connected ? "waveform" : "antenna.radiowaves.left.and.right")
                if model.bluetooth.connected {
                    LabeledContent(L10n.text("Device"), value: model.bluetooth.name)
                    LabeledContent(L10n.text("Battery"), value: model.bluetooth.battery.map { "\($0)%" } ?? L10n.text("Reading…"))
                    LabeledContent(L10n.text("Format"), value: codecName)
                    Button {
                        if model.bluetooth.captureEnabled { model.disableMicrophone() }
                        else { model.startManualPendant() }
                    } label: {
                        Label(!model.bluetooth.captureEnabled ? L10n.text("Start recording") : L10n.text("Stop recording"), systemImage: !model.bluetooth.captureEnabled ? "record.circle" : "stop.circle")
                    }.buttonStyle(PrimaryActionStyle()).accessibilityIdentifier("capture.toggle")
                        .disabled(!model.hasSelectedProfile)
                    if model.bluetooth.captureEnabled {
                        Button { model.startManualRecording() } label: {
                            Label(L10n.text("Record with iPhone"), systemImage: "mic")
                        }.accessibilityIdentifier("recording.start")
                            .disabled(model.microphone.requestingPermission || !model.hasSelectedProfile)
                    }
                } else {
                    Button { model.bluetooth.scan() } label: {
                        Label(L10n.text("Find Omi"), systemImage: "dot.radiowaves.left.and.right")
                    }.buttonStyle(PrimaryActionStyle()).accessibilityIdentifier("pair.scan")
                    ForEach(model.bluetooth.devices, id: \.identifier) { device in
                        Button(device.name ?? L10n.text("Omi · \(device.identifier.uuidString.prefix(8))")) { model.bluetooth.connect(device) }
                    }
                    if model.bluetooth.scanning { ProgressView(L10n.text("Scanning for nearby pendants")) }
                }
            } footer: {
                Text(L10n.text("These controls affect recordings on this iPhone. Saved uploads and other devices continue."))
            }
            PendantClickSettings()
        }.listStyle(.insetGrouped).omiScreen().navigationTitle(L10n.text("Omi pendant"))
            .navigationBarTitleDisplayMode(.inline)
            .onDisappear { model.bluetooth.stopScan() }
    }

    private var uploadSettings: some View {
        @Bindable var model = model
        return Form {
            Section {
                Toggle(L10n.text("Upload only on Wi-Fi"), isOn: $model.uploadWiFiOnly)
                    .accessibilityIdentifier("settings.uploadWiFiOnly")
            } footer: {
                Text(L10n.text("Recordings wait on this iPhone until a permitted connection is available. Recording continues while uploads wait."))
            }
            Section {
                Text(L10n.text("Record with this iPhone’s microphone. Audio stays on this iPhone until you upload it. Recordings stop automatically after two hours."))
            }
        }.omiScreen().navigationTitle(L10n.text("Upload settings")).navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder private var speechControls: some View {
        Button { model.event(.mark) } label: {
            Label(markLabel, systemImage: "bookmark")
        }.disabled(model.nextMarkAt.map { model.now < $0 } ?? false).accessibilityIdentifier("hint.mark")
        Button { model.event(.meetingToggle) } label: {
            Label(model.meetingStart == nil ? L10n.text("Start meeting hint") : L10n.text("End meeting hint"), systemImage: "person.2")
        }.accessibilityIdentifier("hint.meeting")
        if model.meetingStart != nil {
            Text(model.meetingQualifiesAt.map { model.now < $0 } == true ? L10n.text("Provisional meeting · short meetings are ignored") : L10n.text("Meeting hint active"))
                .font(.callout).foregroundStyle(Theme.accent)
        }
        if !model.notice.isEmpty { Text(L10n.key(model.notice)).font(.callout).foregroundStyle(.secondary) }
        Text(L10n.text("Flags and meeting controls hint at surrounding speech. Important marks are limited to once per minute. Configure single and double clicks in pendant settings."))
            .font(.caption).foregroundStyle(.secondary)
        Text(L10n.text("Automatic meeting ending requires a continuous recording synced from Omi. End phone meetings manually."))
            .font(.caption).foregroundStyle(.secondary)
    }

    private var syncDetails: some View {
        List {
            Section {
                Text(L10n.key(model.serverStatus)).font(.callout).textSelection(.enabled)
                if statusStale {
                    Text(L10n.text("Current backend state unknown; showing the last successful observation."))
                        .font(.caption).foregroundStyle(.orange)
                }
                if let upload = model.uploadFeedback {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(L10n.key(upload.title)).font(.subheadline.weight(.medium))
                        if let fraction = upload.fraction {
                            ProgressView(value: fraction).accessibilityLabel(L10n.text("Current upload"))
                            Text(L10n.text("Current item: \(Int(fraction * 100))% · \(bytes(upload.sentBytes)) of \(bytes(upload.totalBytes))"))
                                .font(.caption).monospacedDigit()
                        }
                        Text(L10n.text("\(bytes(upload.remainingBytes)) left in upload queue")).font(.caption).foregroundStyle(.secondary)
                        Text(upload.estimatedSeconds.map { L10n.text("About \(duration($0)) to upload queued items") } ?? L10n.text("Upload time estimate unavailable until transfer speed is measured."))
                            .font(.caption).foregroundStyle(.secondary)
                    }.accessibilityElement(children: .combine)
                }
                processingFeedback
                let generation = model.completenessStatus["generation"]
                if generation["queued"].integer > 0 || generation["running"].integer > 0 {
                    Text(L10n.text("AI tasks: \(generation["queued"].integer) queued · \(generation["running"].integer) processing"))
                        .font(.subheadline).monospacedDigit()
                    ForEach(["summarizing", "organizing", "translating"], id: \.self) { stage in
                        let count = generation["stages"][stage].integer
                        if count > 0 { Text(stageName(stage) + " · " + String(count)).font(.caption).foregroundStyle(.secondary) }
                    }
                }
                ForEach(Array(model.queueErrors.enumerated()), id: \.offset) { _, message in
                    Text(L10n.key(message)).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                }
            } header: { Text(L10n.text("Your server")) }
            Section {
                LabeledContent(L10n.text("Phone storage"), value: "\(model.storageMB.formatted(.number.locale(L10n.locale).precision(.fractionLength(1)))) MB / \(model.storageBudgetMB) MB")
                ProgressView(value: min(model.storageMB, Double(model.storageBudgetMB)), total: Double(model.storageBudgetMB))
                    .accessibilityLabel(L10n.text("Phone storage used"))
                    .accessibilityValue(L10n.text("\(Int(model.storageMB)) of \(model.storageBudgetMB) megabytes"))
            } footer: {
                Text(L10n.text("Unsent audio stays on this iPhone. Synced audio remains available for local rewind for one hour. Capture stops if the storage budget fills."))
            }
            Section {
                Text(L10n.text("Backend observed: \(model.completenessStatus["as_of"].text.isEmpty ? L10n.text("not yet checked") : model.completenessStatus["as_of"].text)"))
                Text(L10n.text("Last server receipt: \(model.completenessStatus["ingestion"]["last_received_at"].text.isEmpty ? L10n.text("unknown") : model.completenessStatus["ingestion"]["last_received_at"].text)"))
                Text(L10n.text("\(model.completenessStatus["processing"]["queued"].integer) queued (\(model.completenessStatus["processing"]["retrying"].integer) retrying) · \(model.completenessStatus["processing"]["transcribing"].integer) transcribing · \(model.completenessStatus["processing"]["blocked"].integer) blocked"))
                LabeledContent(L10n.text("Last decoded audio"), value: model.lastDecodedAt?.formatted(.dateTime.locale(L10n.locale)) ?? L10n.text("Unknown this session"))
                LabeledContent(L10n.text("Last persisted audio"), value: model.lastPersistedAt?.formatted(.dateTime.locale(L10n.locale)) ?? L10n.text("Unknown this session"))
                Text(L10n.text("A saved archive or connection does not establish continuous capture. Backend values show the timestamp of the last successful check."))
                    .foregroundStyle(.secondary)
            }.font(.caption)
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
            Section { NavigationLink(L10n.text("Feedback and repairs")) { FeedbackReviewView() } }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle(L10n.text("Capture & sync details"))
            .navigationBarTitleDisplayMode(.inline)
    }

    private var scheduleSummary: String {
        let policy = model.recordingSchedule
        if !policy.scheduled { return L10n.text("Off") }
        if !policy.enabled || policy.manualOverride { return L10n.text("Manual override") }
        return L10n.text("On")
    }
    private var processingSummary: String {
        let status = model.completenessStatus
        guard !statusStale, status["processing"] != .null else { return L10n.text("Status unavailable") }
        let audioPending = status["processing"]["queued"].integer + status["processing"]["transcribing"].integer
        if status["processing"]["blocked"].integer > 0 || (audioPending > 0 && status["worker_running"] != .bool(true)) { return L10n.text("Needs attention") }
        if max(status["processing"]["transcribing"].integer, status["processing"]["active"].array.count) + status["generation"]["running"].integer > 0 { return L10n.text("Processing") }
        if status["processing"]["queued"].integer + status["generation"]["queued"].integer > 0 { return L10n.text("Queued") }
        return L10n.text("Up to date")
    }
    private var statusStale: Bool {
        guard let at = AppModel.date(model.completenessStatus["as_of"].text) else { return true }
        return model.now.timeIntervalSince(at) > 120 || model.statusRefreshFailed
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
                            ProgressView(value: Double(completed), total: Double(total)).accessibilityLabel(stageName(item["stage"].text))
                            Text(L10n.text("\(completed) of \(total) parts complete")).font(.caption).monospacedDigit()
                        } else { ProgressView().controlSize(.small) }
                        if case .number(let elapsed) = item["elapsed_seconds"], elapsed > 0 {
                            Text(L10n.text("Running for \(duration(elapsed))")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if !statusStale, model.completenessStatus["worker_running"] == .bool(true),
                   case .number(let seconds) = processing["estimated_remaining_seconds"], seconds >= 0, seconds.isFinite {
                    Text(L10n.text("About \(duration(seconds)) to finish the processing queue")).font(.caption).foregroundStyle(.secondary)
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
        case "summarizing": L10n.text("Summarizing")
        case "organizing": L10n.text("Organizing recording topics")
        case "translating": L10n.text("Translating")
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

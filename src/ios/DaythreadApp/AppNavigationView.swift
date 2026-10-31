import SwiftUI
import OmiCore

private struct GoToTodayKey: EnvironmentKey {
    static let defaultValue: () -> Void = {}
}

extension EnvironmentValues {
    var goToToday: () -> Void {
        get { self[GoToTodayKey.self] }
        set { self[GoToTodayKey.self] = newValue }
    }
}

struct MoreView: View {
    @Environment(AppModel.self) private var model
    @State private var preparing = false

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            if model.hasSelectedProfile {
                Section(L10n.text("Your memory")) {
                    NavigationLink { WeeklyReviewView() } label: {
                        DestinationLabel(title: L10n.text("Weekly review"), symbol: "calendar.badge.clock")
                    }
                    NavigationLink { CompletionNotificationsView() } label: {
                        DestinationLabel(title: L10n.text("Notifications"), symbol: "bell")
                    }
                    NavigationLink { InboxView() } label: {
                        DestinationLabel(title: L10n.text("Inbox"), symbol: "checklist")
                    }
                    NavigationLink { LabelsView() } label: {
                        DestinationLabel(title: L10n.text("People & projects"), symbol: "person.2")
                    }
                    if model.canRewind {
                        NavigationLink { RewindView() } label: {
                            DestinationLabel(title: L10n.text("Rewind"), symbol: "gobackward.10")
                        }
                    }
                    Button { preparing = true } label: {
                        DestinationLabel(title: L10n.text("Prepare for a conversation"), symbol: "text.bubble")
                    }.buttonStyle(.plain)
                }
            }
            Section(L10n.text("Make it yours")) {
                NavigationLink { ManagedAccountView() } label: {
                    DestinationLabel(title: L10n.text("Account & connection"), subtitle: model.managedAccount.signedIn ? L10n.text("Managed hosting & model credits") : L10n.text("Optional sign-in or your own backend"), symbol: "person.crop.circle", color: Theme.accent)
                }

                NavigationLink(value: SettingsDestination.profiles) {
                    DestinationLabel(title: L10n.text("Profiles"), subtitle: model.hasSelectedProfile ? model.selectedProfileName : L10n.text("Choose an archive to get started"), symbol: "person.crop.circle", color: Theme.sage)
                }
                NavigationLink(value: SettingsDestination.settings) {
                    DestinationLabel(title: L10n.text("Settings"), symbol: "gearshape")
                }
                if model.hasSelectedProfile {
                    NavigationLink { FeedbackReviewView() } label: {
                        DestinationLabel(title: L10n.text("Feedback & repairs"), symbol: "wrench.and.screwdriver", color: .secondary)
                    }
                }
            }
        }
        .listStyle(.insetGrouped).omiScreen()
        .navigationTitle(L10n.text("More")).navigationBarTitleDisplayMode(.large)
        .sheet(isPresented: $preparing) { ConversationPrepView() }
    }
}

struct DestinationLabel: View {
    let title: String
    var subtitle: String? = nil
    let symbol: String
    var color: Color = Theme.accent

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        HStack(spacing: 14) {
            Image(systemName: symbol).font(.body)
                .foregroundStyle(color)
                .frame(width: 32, height: 32)
                .background(color.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.body.weight(.medium)).foregroundStyle(.primary)
                if let subtitle { Text(subtitle).font(.caption).foregroundStyle(.secondary) }
            }.fixedSize(horizontal: false, vertical: true)
        }.padding(.vertical, 5)
    }
}

struct CaptureGlance: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    private var title: String {
        if model.microphone.active != nil { return L10n.text("Recording from phone microphone") }
        if model.omiRecording { return L10n.text("Recording from Omi") }
        if model.captureFailure != nil { return L10n.text("Capture needs attention") }
        if model.paused { return L10n.text("Capture paused") }
        return model.bluetooth.connected ? L10n.text("Omi connected") : L10n.text("Recording stopped")
    }
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        let layout = dynamicTypeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8)) : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
        VStack(alignment: .leading, spacing: 8) {
            layout {
                NavigationLink { CaptureView() } label: {
                    Label(title, systemImage: model.microphone.active != nil || model.omiRecording ? "record.circle" : model.paused ? "pause.circle" : "waveform")
                        .font(.caption).foregroundStyle(model.captureFailure == nil ? Color.secondary : .orange)
                        .frame(minHeight: 44)
                }.accessibilityIdentifier("day.captureStatus").accessibilityHint(L10n.text("Recording activity"))
                if !dynamicTypeSize.isAccessibilitySize { Spacer(minLength: 0) }
                Button {
                    if model.recordingRequested { model.disableMicrophone() }
                    else if model.bluetooth.connected && model.recordingSchedule.activeSource == "omi" { model.startManualPendant() }
                    else { model.startManualRecording() }
                } label: {
                    Label(model.recordingRequested ? L10n.text("Pause recording") : L10n.text("Start recording"),
                          systemImage: model.recordingRequested ? "pause.fill" : "record.circle")
                        .font(.caption.weight(.semibold))
                }.buttonStyle(.bordered).tint(Theme.accent)
                    .disabled(!model.hasSelectedProfile || model.microphone.requestingPermission)
                    .accessibilityIdentifier("day.recordingToggle")
            }
            let waiting = model.queuedRecordings + model.microphone.recordings.filter { $0.id != model.microphone.active?.id }.count
            if waiting > 0 {
                Text(L10n.text("\(waiting) recordings waiting to upload")).font(.caption).foregroundStyle(.secondary)
            }
            ErrorNote(message: model.microphone.error)
            if model.captureFailure != nil {
                Text(L10n.key(model.notice)).font(.caption).foregroundStyle(.orange)
            }
        }.padding(.vertical, 4)
    }
}

struct DayDecisionsView: View {
    let day: String
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List { DecisionsSection(day: day) }
            .listStyle(.insetGrouped).omiScreen()
            .navigationTitle(L10n.text("Decisions")).navigationBarTitleDisplayMode(.inline)
    }
}

/// The same processing summary is visible on Today and in recording activity.
struct RecordingProcessingActivity: View {
    @Environment(AppModel.self) private var model
    private var status: JSONValue { model.completenessStatus }
    private var processing: JSONValue { status["processing"] }
    private var generation: JSONValue { status["generation"] }
    private var audioActive: Int { max(processing["transcribing"].integer, processing["active"].array.count) }
    private var audioQueued: Int { processing["queued"].integer }
    private var aiActive: Int { generation["running"].integer }
    private var aiQueued: Int { generation["queued"].integer }
    private var stale: Bool {
        guard let at = AppModel.date(status["as_of"].text) else { return true }
        return model.now.timeIntervalSince(at) > 120 || model.statusRefreshFailed
    }
    private var hasCounts: Bool { audioActive + audioQueued + aiActive + aiQueued > 0 }
    private var stages: [String] {
        var names = processing["active"].array.map { stageName($0["stage"].text) }
        for stage in ["summarizing", "organizing", "translating"] where generation["stages"][stage].integer > 0 {
            names.append(stageName(stage))
        }
        var seen = Set<String>()
        return names.filter { seen.insert($0).inserted }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if status == .null || processing == .null {
                Label(L10n.text("Processing status unavailable"), systemImage: "questionmark.circle")
                    .foregroundStyle(.secondary)
            } else {
                if hasCounts {
                    HStack(alignment: .top, spacing: 8) {
                        if !stale && (aiActive > 0 || (audioActive > 0 && status["worker_running"] == .bool(true))) {
                            ProgressView().controlSize(.mini).accessibilityLabel(L10n.text("Processing"))
                        } else { Image(systemName: "clock").accessibilityHidden(true) }
                        VStack(alignment: .leading, spacing: 4) {
                            if audioActive + audioQueued > 0 {
                                Text(L10n.text("Recordings: \(audioQueued) queued · \(audioActive) processing"))
                            }
                            if aiActive + aiQueued > 0 {
                                Text(L10n.text("AI tasks: \(aiQueued) queued · \(aiActive) processing"))
                            }
                        }.monospacedDigit()
                    }.foregroundStyle(.primary)
                    if !stale {
                        let active = generation["active"].array
                        if active.isEmpty && !stages.isEmpty {
                            Text(stages.joined(separator: " · ")).foregroundStyle(.secondary)
                        }
                        ForEach(Array(active.enumerated()), id: \.offset) { _, item in
                            let total = item["total_chunks"].integer
                            let completed = max(0, min(total, item["completed_chunks"].integer))
                            if total > 0 {
                                Text(stageName(item["stage"].text) + " · " + L10n.text("\(completed) of \(total) parts complete"))
                                    .foregroundStyle(.secondary).monospacedDigit()
                                ProgressView(value: Double(completed), total: Double(total))
                                    .accessibilityLabel(stageName(item["stage"].text))
                            }
                        }
                    }
                }
                if stale {
                    Text(L10n.text("Last known counts; current activity is unknown.")).foregroundStyle(.secondary)
                } else if status["worker_running"] == .bool(false), audioActive + audioQueued > 0 {
                    Text(L10n.text("Processing is waiting for the worker to start.")).foregroundStyle(.orange)
                }
                if processing["blocked"].integer > 0 {
                    Text(L10n.text("\(processing["blocked"].integer) items need attention.")).foregroundStyle(.orange)
                }
            }
        }.font(.caption).accessibilityElement(children: .combine)
            .accessibilityIdentifier("recording.processingActivity")
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
}

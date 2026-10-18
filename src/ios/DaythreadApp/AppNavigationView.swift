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
            Section {
                EditorialTitle(eyebrow: "Daythread", title: L10n.text("Room to remember.")).editorialRow()
            }
            if model.hasSelectedProfile {
                Section(L10n.text("Your memory")) {
                    NavigationLink { CompletionNotificationsView() } label: {
                        DestinationLabel(title: L10n.text("Notifications"), subtitle: L10n.text("Your completed recaps, rewinds & translations"), symbol: "bell", color: Theme.accent)
                    }
                    NavigationLink { InboxView() } label: {
                        DestinationLabel(title: L10n.text("Inbox"), subtitle: L10n.text("Follow-ups, commitments & questions"), symbol: "checklist", color: Theme.sage)
                    }
                    NavigationLink { LabelsView() } label: {
                        DestinationLabel(title: L10n.text("People & projects"), subtitle: L10n.text("Keep related moments together"), symbol: "person.2", color: Theme.accent)
                    }
                    NavigationLink { RewindView() } label: {
                        DestinationLabel(title: L10n.text("Rewind"), subtitle: L10n.text("Listen back to the last few minutes"), symbol: "gobackward.10", color: Theme.sage)
                    }
                    Button { preparing = true } label: {
                        DestinationLabel(title: L10n.text("Prepare for a conversation"), subtitle: L10n.text("Bring the right context with you"), symbol: "text.bubble", color: Theme.accent)
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
                    DestinationLabel(title: L10n.text("Settings"), subtitle: L10n.text("Appearance, capture & connections"), symbol: "slider.horizontal.3", color: Theme.accent)
                }
                if model.hasSelectedProfile {
                    NavigationLink { FeedbackReviewView() } label: {
                        DestinationLabel(title: L10n.text("Feedback & repairs"), subtitle: L10n.text("Review and improve your archive"), symbol: "wrench.and.screwdriver", color: .secondary)
                    }
                }
            }
        }
        .listStyle(.insetGrouped).omiScreen()
        .navigationTitle(L10n.text("More")).navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $preparing) { ConversationPrepView() }
    }
}

struct DestinationLabel: View {
    let title: String
    let subtitle: String
    let symbol: String
    var color: Color = Theme.accent

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        HStack(spacing: 14) {
            Image(systemName: symbol).font(.title3)
                .foregroundStyle(color)
                .frame(width: 44, height: 44)
                .background(color.opacity(0.10), in: RoundedRectangle(cornerRadius: 14))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.body.weight(.medium)).foregroundStyle(.primary)
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }.fixedSize(horizontal: false, vertical: true)
        }.padding(.vertical, 5)
    }
}

struct DayQuickActions: View {
    let importAudio: () -> Void
    let rewind: () -> Void
    let ask: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        let layout = dynamicTypeSize.isAccessibilitySize ? AnyLayout(VStackLayout(spacing: 10)) : AnyLayout(HStackLayout(spacing: 10))
        layout {
            Button(action: importAudio) { tile(L10n.text("Add audio"), symbol: "plus", color: Theme.accent) }
                .accessibilityIdentifier("day.import")
            Button(action: rewind) { tile(L10n.text("Rewind"), symbol: "gobackward.10", color: Theme.sage) }
            Button(action: ask) {
                tile(L10n.text("Ask your day"), symbol: "bubble.left.and.bubble.right", color: Theme.accent)
            }.accessibilityIdentifier("day.chat")
        }.buttonStyle(.plain)
    }

    private func tile(_ title: String, symbol: String, color: Color) -> some View {
        VStack(spacing: 9) {
            Image(systemName: symbol).font(.title3.weight(.medium)).foregroundStyle(color).accessibilityHidden(true)
            Text(title).font(.caption.weight(.semibold)).foregroundStyle(.primary)
                .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, minHeight: 64)
        .padding(.horizontal, 6).padding(.vertical, 10)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 20))
        .contentShape(RoundedRectangle(cornerRadius: 20))
    }
}

struct CaptureGlance: View {
    @Environment(AppModel.self) private var model
    private var title: String {
        if model.microphone.active != nil { return L10n.text("Recording from phone microphone") }
        if model.omiRecording { return L10n.text("Recording from Omi") }
        if model.captureFailure != nil { return L10n.text("Capture needs attention") }
        if model.paused { return L10n.text("Capture paused") }
        return model.bluetooth.connected ? L10n.text("Omi connected") : L10n.text("Ready when you are")
    }
    private var statusStale: Bool {
        guard let at = AppModel.date(model.completenessStatus["as_of"].text) else { return true }
        return model.now.timeIntervalSince(at) > 120 || !model.serverStatus.hasPrefix(L10n.source("Home-core connected"))
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        let processing = model.completenessStatus["processing"]
        let generation = model.completenessStatus["generation"]
        VStack(alignment: .leading, spacing: 10) {
            Text(L10n.text("Recording activity")).font(.subheadline.weight(.semibold))
            NavigationLink { CaptureView() } label: {
                Label(title, systemImage: model.microphone.active != nil || model.omiRecording ? "record.circle" : model.paused ? "pause.circle" : "waveform")
                    .font(.subheadline.weight(.medium)).foregroundStyle(Theme.sage)
            }.accessibilityIdentifier("day.captureStatus")
            if model.completenessStatus != .null {
                Text(L10n.text("Recordings: \(processing["queued"].integer) queued · \(processing["transcribing"].integer) processing"))
                    .font(.subheadline).monospacedDigit()
            }
            let waiting = model.queuedRecordings + model.microphone.recordings.filter { $0.id != model.microphone.active?.id }.count
            if waiting > 0 {
                Text(L10n.text("\(waiting) recordings waiting to upload")).font(.caption).foregroundStyle(.secondary)
            }
            if !statusStale && model.completenessStatus["worker_running"] == .bool(true) {
                ForEach(Array(processing["active"].array.enumerated()), id: \.offset) { _, item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(stageName(item["stage"].text)).font(.caption)
                        let total = item["total_chunks"].integer
                        let complete = max(0, min(total, item["completed_chunks"].integer))
                        if total > 0 {
                            ProgressView(value: Double(complete), total: Double(total))
                                .accessibilityLabel(stageName(item["stage"].text))
                            Text(L10n.text("\(complete) of \(total) parts complete")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if generation["queued"].integer > 0 || generation["running"].integer > 0 {
                Text(L10n.text("AI tasks: \(generation["queued"].integer) queued · \(generation["running"].integer) processing"))
                    .font(.subheadline).monospacedDigit()
                if !statusStale {
                    ForEach(["summarizing", "organizing", "translating"], id: \.self) { stage in
                        let count = generation["stages"][stage].integer
                        if count > 0 { Text(stageName(stage) + " · " + String(count)).font(.caption).foregroundStyle(.secondary) }
                    }
                }
            }
            if statusStale && model.completenessStatus != .null {
                Text(L10n.text("Last known counts; current activity is unknown.")).font(.caption).foregroundStyle(.secondary)
            } else if model.completenessStatus["worker_running"] == .bool(false), processing["queued"].integer + processing["transcribing"].integer > 0 {
                Text(L10n.text("Processing is waiting for the worker to start.")).font(.caption).foregroundStyle(.orange)
            }
            if processing["blocked"].integer > 0 {
                Text(L10n.text("\(processing["blocked"].integer) items need attention.")).font(.caption).foregroundStyle(.orange)
            }
        }.padding(.vertical, 4).accessibilityIdentifier("day.recordingActivity")
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

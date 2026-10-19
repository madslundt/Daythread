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
        Label(title, systemImage: symbol)
            .font(.subheadline.weight(.medium)).foregroundStyle(color)
            .frame(maxWidth: .infinity, minHeight: 44)
            .padding(.horizontal, 6)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 12))
            .contentShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct CaptureGlance: View {
    @Environment(AppModel.self) private var model
    private var title: String {
        if model.microphone.active != nil { return L10n.text("Recording from phone microphone") }
        if model.omiRecording { return L10n.text("Recording from Omi") }
        if model.captureFailure != nil { return L10n.text("Capture needs attention") }
        if model.paused { return L10n.text("Capture paused") }
        return model.bluetooth.connected ? L10n.text("Omi connected") : L10n.text("Recording stopped on this iPhone")
    }
    private var statusStale: Bool {
        guard let at = AppModel.date(model.completenessStatus["as_of"].text) else { return true }
        return model.now.timeIntervalSince(at) > 120 || !model.serverStatus.hasPrefix(L10n.source("Home-core connected"))
    }
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        let processing = model.completenessStatus["processing"]
        NavigationLink { CaptureView() } label: {
            VStack(alignment: .leading, spacing: 6) {
                Text(L10n.text("Recording activity")).font(.subheadline.weight(.semibold)).foregroundStyle(.primary)
                Label(title, systemImage: model.microphone.active != nil || model.omiRecording ? "record.circle" : model.paused ? "pause.circle" : "waveform")
                    .font(.subheadline).foregroundStyle(model.captureFailure == nil ? Theme.sage : .orange)
                let waiting = model.queuedRecordings + model.microphone.recordings.filter { $0.id != model.microphone.active?.id }.count
                if waiting > 0 {
                    Text(L10n.text("\(waiting) recordings waiting to upload")).font(.caption).foregroundStyle(.secondary)
                }
                if statusStale && model.completenessStatus != .null {
                    Text(L10n.text("Last known counts; current activity is unknown.")).font(.caption).foregroundStyle(.secondary)
                } else if !statusStale {
                    let queued = processing["queued"].integer
                    let active = processing["transcribing"].integer
                    if queued + active > 0 {
                        Text(L10n.text("Recordings: \(queued) queued · \(active) processing"))
                            .font(.caption).monospacedDigit().foregroundStyle(.secondary)
                    }
                    if model.completenessStatus["worker_running"] == .bool(false), queued + active > 0 {
                        Text(L10n.text("Processing is waiting for the worker to start.")).font(.caption).foregroundStyle(.orange)
                    }
                }
                if processing["blocked"].integer > 0 {
                    Text(L10n.text("\(processing["blocked"].integer) items need attention.")).font(.caption).foregroundStyle(.orange)
                }
            }.padding(.vertical, 4)
        }.accessibilityIdentifier("day.captureStatus")
            .accessibilityIdentifier("day.recordingActivity")
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

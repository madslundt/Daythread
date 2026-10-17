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

    var body: some View {
        List {
            Section {
                EditorialTitle(eyebrow: "Daythread", title: "Room to remember.").editorialRow()
            }
            if model.hasSelectedProfile {
                Section("Your memory") {
                    NavigationLink { CompletionNotificationsView() } label: {
                        DestinationLabel(title: "Notifications", subtitle: "Your completed recaps, rewinds & translations", symbol: "bell", color: Theme.accent)
                    }
                    NavigationLink { InboxView() } label: {
                        DestinationLabel(title: "Inbox", subtitle: "Follow-ups, commitments & questions", symbol: "checklist", color: Theme.sage)
                    }
                    NavigationLink { LabelsView() } label: {
                        DestinationLabel(title: "People & projects", subtitle: "Keep related moments together", symbol: "person.2", color: Theme.accent)
                    }
                    NavigationLink { RewindView() } label: {
                        DestinationLabel(title: "Rewind", subtitle: "Listen back to the last few minutes", symbol: "gobackward.10", color: Theme.sage)
                    }
                    Button { preparing = true } label: {
                        DestinationLabel(title: "Prepare for a conversation", subtitle: "Bring the right context with you", symbol: "text.bubble", color: Theme.accent)
                    }.buttonStyle(.plain)
                }
            }
            Section("Make it yours") {
                NavigationLink(value: SettingsDestination.profiles) {
                    DestinationLabel(title: "Profiles", subtitle: model.hasSelectedProfile ? model.selectedProfileName : "Choose an archive to get started", symbol: "person.crop.circle", color: Theme.sage)
                }
                NavigationLink(value: SettingsDestination.settings) {
                    DestinationLabel(title: "Settings", subtitle: "Appearance, capture & connections", symbol: "slider.horizontal.3", color: Theme.accent)
                }
                if model.hasSelectedProfile {
                    NavigationLink { FeedbackReviewView() } label: {
                        DestinationLabel(title: "Feedback & repairs", subtitle: "Review and improve your archive", symbol: "wrench.and.screwdriver", color: .secondary)
                    }
                }
            }
        }
        .listStyle(.insetGrouped).omiScreen()
        .navigationTitle("More").navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $preparing) { ConversationPrepView() }
    }
}

struct DestinationLabel: View {
    let title: String
    let subtitle: String
    let symbol: String
    var color: Color = Theme.accent

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: symbol).font(.title3)
                .foregroundStyle(color)
                .frame(width: 44, height: 44)
                .background(color.opacity(0.10), in: RoundedRectangle(cornerRadius: 14))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(LocalizedStringKey(title)).font(.body.weight(.medium)).foregroundStyle(.primary)
                Text(LocalizedStringKey(subtitle)).font(.caption).foregroundStyle(.secondary)
            }.fixedSize(horizontal: false, vertical: true)
        }.padding(.vertical, 5)
    }
}

struct DayQuickActions: View {
    let importAudio: () -> Void
    let rewind: () -> Void
    let ask: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let layout = dynamicTypeSize.isAccessibilitySize ? AnyLayout(VStackLayout(spacing: 10)) : AnyLayout(HStackLayout(spacing: 10))
        layout {
            Button(action: importAudio) { tile("Add audio", symbol: "plus", color: Theme.accent) }
                .accessibilityIdentifier("day.import")
            Button(action: rewind) { tile("Rewind", symbol: "gobackward.10", color: Theme.sage) }
            Button(action: ask) {
                tile("Ask your day", symbol: "bubble.left.and.bubble.right", color: Theme.accent)
            }.accessibilityIdentifier("day.chat")
        }.buttonStyle(.plain)
    }

    private func tile(_ title: String, symbol: String, color: Color) -> some View {
        VStack(spacing: 9) {
            Image(systemName: symbol).font(.title3.weight(.medium)).foregroundStyle(color).accessibilityHidden(true)
            Text(LocalizedStringKey(title)).font(.caption.weight(.semibold)).foregroundStyle(.primary)
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
        if model.captureFailure != nil { return "Capture needs attention" }
        if model.paused { return "Capture paused" }
        return model.bluetooth.connected ? "Omi connected" : "Ready when you are"
    }
    private var subtitle: String {
        if model.queueCount > 0 { return "\(model.queueCount) items waiting to sync" }
        return model.bluetooth.connected ? "Open capture for live status" : "Connect Omi or add an audio recording"
    }
    var body: some View {
        NavigationLink { CaptureView() } label: {
            HStack(spacing: 12) {
                Image(systemName: model.paused ? "pause.circle" : "waveform")
                    .font(.title3).foregroundStyle(Theme.sage)
                    .frame(width: 38, height: 38)
                    .background(Theme.sage.opacity(0.10), in: Circle()).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.primary)
                    Text(subtitle).font(.caption).foregroundStyle(.secondary)
                }.fixedSize(horizontal: false, vertical: true)
            }.padding(.vertical, 2)
        }
    }
}

struct DayDecisionsView: View {
    let day: String
    var body: some View {
        List { DecisionsSection(day: day) }
            .listStyle(.insetGrouped).omiScreen()
            .navigationTitle("Decisions").navigationBarTitleDisplayMode(.inline)
    }
}

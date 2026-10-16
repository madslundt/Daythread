import SwiftUI
import OmiCore
import BackgroundTasks

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: "local.omi.sync", using: .main) { task in
            Task { @MainActor in
                BackgroundUploader.shared?.pump()
                task.setTaskCompleted(success: true)
                BackgroundUploader.scheduleRetry()
            }
        }
        return true
    }
    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String, completionHandler: @escaping () -> Void) {
        Task { @MainActor in BackgroundUploader.backgroundCompletion = completionHandler }
    }
}

@main
struct OmiLocalApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel()
    @AppStorage("appearancePreference") private var appearance = AppAppearance.system
    var body: some Scene {
        WindowGroup {
            RootView().environment(model).tint(Theme.accent)
                .preferredColorScheme(appearance.colorScheme)
        }
    }
}

enum AppAppearance: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: Self { self }
    var title: String { rawValue.capitalized }
    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }
}

enum Theme {
    static let accent = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.90, green: 0.62, blue: 0.50, alpha: 1)
            : UIColor(red: 0.60, green: 0.32, blue: 0.24, alpha: 1)
    })
    static let canvas = Color(uiColor: .systemGroupedBackground)
}

struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        TabView {
            NavigationStack { CaptureView() }.tabItem { Label("Capture", systemImage: "waveform") }
            NavigationStack { DayView() }.tabItem { Label("Day", systemImage: "book.closed") }
            NavigationStack { RewindView() }.tabItem { Label("Rewind", systemImage: "gobackward.10") }
            NavigationStack { ChatView() }.tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }
            NavigationStack { SettingsView() }.tabItem { Label("Settings", systemImage: "slider.horizontal.3") }
        }
        .task {
            await model.refreshStatus()
            while !Task.isCancelled {
                await model.tick()
                try? await Task.sleep(for: .seconds(1))
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { model.checkpoint(); BackgroundUploader.scheduleRetry() }
            if phase == .active { model.reconnectBackend(); Task { await model.refreshStatus() } }
        }
        .alert("Omi Local", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
            Button("OK") { model.error = nil }
        } message: { Text(model.error ?? "") }
    }
}

struct EditorialTitle: View {
    let eyebrow: String
    let title: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(eyebrow.uppercased()).font(.caption.weight(.semibold)).tracking(2).foregroundStyle(Theme.accent)
            Text(title).font(.system(size: 36, weight: .regular, design: .serif)).foregroundStyle(.primary)
        }.padding(.vertical, 12).accessibilityElement(children: .combine)
    }
}

struct ErrorNote: View {
    let message: String?
    var body: some View {
        if let message { Label(message, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.red).textSelection(.enabled) }
    }
}

enum LocalCache {
    static func load(_ key: String) -> JSONValue? {
        guard let data = UserDefaults.standard.data(forKey: "cache." + key) else { return nil }
        return try? JSONDecoder().decode(JSONValue.self, from: data)
    }
    static func save(_ value: JSONValue, key: String) {
        if let data = try? JSONEncoder().encode(value) { UserDefaults.standard.set(data, forKey: "cache." + key) }
    }
}

struct SourceList: View {
    let sources: [JSONValue]
    @Environment(AppModel.self) private var model
    @State private var selected: SourceSelection?
    var body: some View {
        ForEach(Array(sources.enumerated()), id: \.offset) { _, source in
            Button { selected = SourceSelection(source: source) } label: {
                VStack(alignment: .leading, spacing: 5) {
                    Text(AppModel.date(source["started_at"].text)?.formatted(date: .abbreviated, time: .shortened) ?? source["started_at"].text)
                        .font(.caption).foregroundStyle(.secondary)
                    Text(source["text"].text.isEmpty ? "Transcription \(source["state"].text)" : source["text"].text)
                        .font(.body).foregroundStyle(.primary).lineLimit(4).multilineTextAlignment(.leading)
                }.padding(.vertical, 5)
            }
        }
        .sheet(item: $selected) { selection in SourceDetail(source: selection.source) }
    }
}
struct SourceSelection: Identifiable { let id = UUID(); let source: JSONValue }
struct SourceDetail: View {
    let source: JSONValue
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var error: String?
    @State private var loading = false
    var body: some View {
        NavigationStack {
            List {
                Section("Recorded evidence") {
                    Text(source["started_at"].text).font(.caption).textSelection(.enabled)
                    Text(source["text"].text.isEmpty ? "Transcription pending" : source["text"].text).textSelection(.enabled)
                    Text(source["id"].text).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                }
                NavigationLink("Create decision from this source") {
                    SourceDecisionCreation(sourceID: source["id"].text)
                }.disabled(source["text"].text.isEmpty)
                if source["audio_available"] == .bool(true) {
                Button {
                    loading = true
                    Task {
                        defer { loading = false }
                        do { model.play(try await model.client().download("v1/audio/\(source["id"].text)", query: [:])) }
                        catch { self.error = error.localizedDescription }
                    }
                } label: { Label(loading ? "Loading audio…" : "Play original audio", systemImage: "play.circle") }.disabled(loading)
                } else { Text("Original audio unavailable").font(.caption).foregroundStyle(.secondary) }
                ErrorNote(message: error)
            }.navigationTitle("Source").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
}

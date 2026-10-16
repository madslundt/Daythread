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
    static let canvas = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.09, green: 0.085, blue: 0.08, alpha: 1)
            : UIColor(red: 0.97, green: 0.955, blue: 0.93, alpha: 1)
    })
    static let onAccent = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.17, green: 0.10, blue: 0.08, alpha: 1)
            : .white
    })
}

struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        TabView {
            NavigationStack { DayView() }.tabItem { Label("Day", systemImage: "book.closed") }
            NavigationStack { RewindView() }.tabItem { Label("Rewind", systemImage: "gobackward.10") }
            NavigationStack { CaptureView() }.tabItem { Label("Capture", systemImage: "waveform") }
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
    var subtitle: String? = nil
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(eyebrow.uppercased()).font(.caption.weight(.semibold)).tracking(1.8).foregroundStyle(Theme.accent)
            Text(title).font(.system(.largeTitle, design: .serif)).foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
            if let subtitle {
                Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 12)
            .accessibilityElement(children: .combine)
    }
}

/// Keeps reading and form controls comfortable on iPad while filling narrow windows.
struct OmiScreenStyle: ViewModifier {
    func body(content: Content) -> some View {
        content
            .scrollContentBackground(.hidden)
            .frame(maxWidth: 860)
            .frame(maxWidth: .infinity)
            .background(Theme.canvas)
            .toolbarBackground(Theme.canvas, for: .navigationBar)
    }
}

extension View {
    func omiScreen() -> some View { modifier(OmiScreenStyle()) }
    func editorialRow() -> some View {
        listRowBackground(Color.clear).listRowSeparator(.hidden)
    }
}

struct PrimaryActionStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity, minHeight: 28)
            .padding(.horizontal, 16).padding(.vertical, 12)
            .foregroundStyle(isEnabled ? Theme.onAccent : Color.secondary)
            .background(isEnabled ? Theme.accent : Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
            .opacity(configuration.isPressed ? 0.8 : 1)
    }
}

struct ErrorNote: View {
    let message: String?
    var body: some View {
        if let message { Label(message, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.red).textSelection(.enabled) }
    }
}

enum LocalCache {
    static func reconcile(address: String, revision: String) {
        guard !revision.isEmpty else { return }
        let key = "archiveRevision." + address
        if UserDefaults.standard.string(forKey: key) != revision {
            for cacheKey in UserDefaults.standard.dictionaryRepresentation().keys where cacheKey.hasPrefix("cache." + address + ".") {
                UserDefaults.standard.removeObject(forKey: cacheKey)
            }
            UserDefaults.standard.set(revision, forKey: key)
        }
    }
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
                    Text(source["matches"].array.first?["text"].text ?? (source["text"].text.isEmpty ? "Transcription \(source["state"].text)" : source["text"].text))
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
    var onCorrect: (() -> Void)? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var fresh: JSONValue?
    private var evidence: JSONValue { fresh ?? source }
    private var transcriptDescription: String {
        if evidence == .null { return "Source removed" }
        if evidence["state"].text == "expired" { return "Transcript expired under your retention policy." }
        if !evidence["text"].text.isEmpty { return evidence["text"].text }
        return evidence["state"].text == "done" ? "No speech detected" : "Transcription pending"
    }
    @State private var error: String?
    @State private var loading = false
    @State private var editing = false
    @State private var draft = ""
    var body: some View {
        NavigationStack {
            List {
                Section("Recorded evidence") {
                    NavigationLink("Report incorrect or missing information") { FeedbackEditor(sourceID: evidence["id"].text) }
                    Text(sourceDate).font(.caption).textSelection(.enabled)
                    Text(evidence["text"].text.isEmpty ? "No transcript text · \(evidence["transcription_outcome"].text) · \(evidence["state"].text)" : evidence["text"].text).textSelection(.enabled)
                    Text(evidence["id"].text).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                }
                NavigationLink { SourceLabelsView(sourceID: evidence["id"].text) } label: { Label("People & projects", systemImage: "person.2") }.disabled(evidence == .null)
                NavigationLink("Create decision from this source") {
                    SourceDecisionCreation(sourceID: evidence["id"].text)
                }.disabled(evidence["text"].text.isEmpty)
                Section("Transcript correction") {
                    if editing {
                        TextEditor(text: $draft).frame(minHeight: 160).accessibilityLabel("Correct transcript")
                        Button("Save correction") {
                            let address = model.backendAddress
                            loading = true
                            Task {
                                defer { loading = false }
                                do {
                                    guard model.backendAddress == address else { return }
                                    _ = try await model.client().request("v1/transcripts/\(evidence["id"].text)", method: "PATCH", body: ["text": draft])
                                    guard !Task.isCancelled, model.backendAddress == address else { return }
                                    onCorrect?(); dismiss()
                                } catch { self.error = error.localizedDescription }
                            }
                        }.disabled(loading || draft.count > 20000)
                        Button("Cancel edit") { editing = false }
                    } else { Button("Correct transcript") { draft = evidence["text"].text; editing = true } }
                }
                if evidence["state"].text == "blocked" || !evidence["error_code"].text.isEmpty {
                    Button("Retry transcription after resolving the problem") { Task {
                        do { _ = try await model.client().request("v1/audio/\(evidence["id"].text)/retry", method: "POST"); error = nil }
                        catch { self.error = error.localizedDescription }
                    } }
                }
                if evidence["audio_availability"].text == "missing" { Text("The archive expects audio, but the file is missing.").font(.caption) }
                if evidence["audio_available"] == .bool(false) {
                    Text(evidence["source_kind"].text == "omi_api" ? "Imported from Omi · transcript only; original audio unavailable." : "Audio expired under retention; transcript remains available.").font(.caption)
                } else {
                Button {
                    loading = true
                    Task {
                        defer { loading = false }
                        do { model.play(try await model.client().download("v1/audio/\(evidence["id"].text)", query: [:])) }
                        catch { self.error = error.localizedDescription }
                    }
                } label: { Label(loading ? "Loading audio…" : "Play original audio", systemImage: "play.circle") }.disabled(loading || evidence["audio_availability"].text == "missing")
                }
                ErrorNote(message: error)
            }.task(id: model.archiveRevision + model.backendAddress) {
                do {
                    let result = try await model.client().request("v1/transcripts/\(source["id"].text)")
                    guard !Task.isCancelled else { return }
                    fresh = result; error = nil
                } catch {
                    guard !Task.isCancelled else { return }
                    self.error = error.localizedDescription
                    if (error as? BackendFailure)?.status == 404 { fresh = .null }
                }
            }.navigationTitle("Source").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
    private var sourceDate: String {
        guard let date = AppModel.date(evidence["started_at"].text) else { return evidence["started_at"].text }
        let formatter = DateFormatter()
        formatter.timeZone = TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current
        formatter.dateStyle = .medium; formatter.timeStyle = .short
        return formatter.string(from: date) + " · " + formatter.timeZone.identifier
    }
}

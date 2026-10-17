import SwiftUI
import OmiCore
import BackgroundTasks
import UserNotifications

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = CompletionNotificationDelegate.shared
        BGTaskScheduler.shared.register(forTaskWithIdentifier: "com.daythread.sync", using: .main) { task in
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
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Task { @MainActor in CompletionNotifications.shared.receivedToken(deviceToken) }
    }
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Task { @MainActor in CompletionNotifications.shared.registrationFailed(error) }
    }
}

@main
struct DaythreadApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel()
    @AppStorage("appearancePreference") private var appearance = AppAppearance.system
    @AppStorage("interfaceLanguagePreference") private var interfaceLanguagePreference = "system"
    var body: some Scene {
        WindowGroup {
            RootView().environment(model).environment(CompletionNotifications.shared).tint(Theme.accent)
                .preferredColorScheme(appearance.colorScheme)
                .environment(\.locale, interfaceLanguagePreference == "system"
                    ? Locale.autoupdatingCurrent : Locale(identifier: interfaceLanguagePreference))
        }
    }
}

enum AppAppearance: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: Self { self }
    var title: LocalizedStringKey { LocalizedStringKey(rawValue.capitalized) }
    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }
}

enum Theme {
    static let surface = Color(uiColor: .secondarySystemGroupedBackground)
    static let sage = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.64, green: 0.78, blue: 0.69, alpha: 1)
            : UIColor(red: 0.24, green: 0.40, blue: 0.33, alpha: 1)
    })
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

enum SettingsDestination: Hashable { case settings, profiles }
private enum RootTab: Hashable { case today, diary, capture, chat, more }

struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(CompletionNotifications.self) private var notifications
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedTab: RootTab = UserDefaults.standard.string(forKey: "selectedProfileID")?.isEmpty == false ? .today : .more
    @State private var settingsPath: [SettingsDestination] = []
    @State private var todayNavigationID = UUID()
    var body: some View {
            TabView(selection: Binding(get: { model.hasSelectedProfile ? selectedTab : .more }, set: { tab in
                if !model.hasSelectedProfile && tab != .more { settingsPath = [.profiles] }
                if tab == .today && model.hasSelectedProfile { todayNavigationID = UUID() }
                selectedTab = model.hasSelectedProfile ? tab : .more
            })) {
                NavigationStack { if model.hasSelectedProfile { DayView() } }.id(todayNavigationID).tabItem { Label("Today", systemImage: "sun.max") }.tag(RootTab.today)
                NavigationStack { if model.hasSelectedProfile { DiaryView() } }.tabItem { Label("Diary", systemImage: "book.closed") }.tag(RootTab.diary)
                NavigationStack { if model.hasSelectedProfile { CaptureView() } }.tabItem { Label("Capture", systemImage: "waveform") }.tag(RootTab.capture)
                NavigationStack { if model.hasSelectedProfile { ChatView() } }.tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }.tag(RootTab.chat)
                NavigationStack(path: $settingsPath) {
                    MoreView().navigationDestination(for: SettingsDestination.self) { destination in
                        switch destination {
                        case .settings: SettingsView()
                        case .profiles: ProfilesSettingsView()
                        }
                    }
                }.tabItem { Label("More", systemImage: "square.grid.2x2") }.tag(RootTab.more)
            }
            .id(model.archiveIdentity)
            .environment(\.goToToday, {
                todayNavigationID = UUID()
                selectedTab = .today
            })
        .task {
            notifications.configure(model: model)
            notifications.setActive(scenePhase == .active)
            if !model.hasSelectedProfile { settingsPath = [.profiles] }
            await model.refreshStatus()
            while !Task.isCancelled {
                await model.tick()
                try? await Task.sleep(for: .seconds(1))
            }
        }
        .onChange(of: model.selectedProfileID) { _, _ in
            settingsPath = model.hasSelectedProfile ? [] : [.profiles]
            selectedTab = model.hasSelectedProfile ? .today : .more
        }
        .onChange(of: model.archiveIdentity) { _, _ in notifications.configure(model: model) }
        .onChange(of: model.backendConfiguration) { _, _ in notifications.configure(model: model) }
        .onChange(of: model.archiveRevision) { old, _ in if !old.isEmpty { notifications.clearPreviews() } }
        .onChange(of: scenePhase) { _, phase in
            notifications.setActive(phase == .active)
            if phase == .background { model.checkpoint(); BackgroundUploader.scheduleRetry() }
            if phase == .active { model.reconnectBackend(); Task { await model.refreshStatus() } }
        }
        .background(CompletionSceneOverlay(model: model, notifications: notifications))
        .alert("Daythread", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
            Button("OK") { model.error = nil }
        } message: { Text(model.error ?? "") }
    }

}

struct ProfileEditorView: View {
    let profile: AppModel.Profile?
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var saving = false
    @State private var error: String?
    init(profile: AppModel.Profile? = nil) {
        self.profile = profile
        _name = State(initialValue: profile?.name ?? "")
    }
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Profile name", text: $name)
                        .textContentType(.nickname).accessibilityIdentifier("profiles.name")
                } footer: {
                    Text("Each profile has its own recordings, diary and chat. Settings apply to every profile. Anyone using this app can select a profile.")
                }
                ErrorNote(message: error)
            }.navigationTitle(profile == nil ? "Create profile" : "Edit profile").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(saving) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(saving ? "Saving…" : profile == nil ? "Create" : "Save") {
                            saving = true
                            Task {
                                defer { saving = false }
                                do {
                                    if let profile { try await model.renameProfile(profile, name: name) }
                                    else { try await model.createProfile(name: name) }
                                    dismiss()
                                }
                                catch { self.error = error.localizedDescription }
                            }
                        }.disabled(saving || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || name.count > 80)
                    }
                }
        }.interactiveDismissDisabled(saving)
    }
}

struct EditorialTitle: View {
    let eyebrow: String
    let title: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(LocalizedStringKey(eyebrow)).textCase(.uppercase).font(.caption.weight(.semibold)).tracking(1.6).foregroundStyle(Theme.accent)
            Text(LocalizedStringKey(title)).font(.system(.largeTitle, design: .serif)).foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 4)
            .accessibilityElement(children: .combine)
    }
}

/// Keeps reading and form controls comfortable on iPad while filling narrow windows.
struct OmiScreenStyle: ViewModifier {
    func body(content: Content) -> some View {
        content
            .scrollContentBackground(.hidden)
            .listSectionSpacing(20)
            .contentMargins(.top, 12, for: .scrollContent)
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
    var onCorrect: (() -> Void)? = nil
    @Environment(AppModel.self) private var model
    @State private var selected: SourceSelection?
    var body: some View {
        ForEach(Array(sources.enumerated()), id: \.element["id"].text) { _, source in
            Button { selected = SourceSelection(source: source) } label: {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: "waveform").font(.body).foregroundStyle(Theme.sage)
                        .frame(width: 36, height: 42).background(Theme.sage.opacity(0.08), in: RoundedRectangle(cornerRadius: 11))
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 6) {
                        Text(title(source)).font(.body.weight(.medium)).foregroundStyle(.primary).lineLimit(3)
                        Text(metadata(source)).font(.caption).foregroundStyle(.secondary)
                        if let match = source["matches"].array.first, !source["title"].text.isEmpty {
                            Text(match["text"].text).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                    if model.importanceMarks.contains(.init(kind: "source", id: source["id"].text)) || source["important"] == .bool(true) {
                        Image(systemName: "star.fill").font(.caption).foregroundStyle(Theme.accent).accessibilityLabel("Important")
                    }
                    Image(systemName: "chevron.right").font(.caption2.weight(.semibold)).foregroundStyle(.tertiary).padding(.top, 7).accessibilityHidden(true)
                }.multilineTextAlignment(.leading).padding(.vertical, 8).contentShape(Rectangle())
            }.buttonStyle(.plain)
        }
        .sheet(item: $selected) { selection in SourceDetail(source: selection.source, onCorrect: onCorrect) }
    }

    private func title(_ source: JSONValue) -> String {
        if !source["title"].text.isEmpty { return source["title"].text }
        if let match = source["matches"].array.first, !match["text"].text.isEmpty { return match["text"].text }
        if !source["text"].text.isEmpty { return source["text"].text }
        return source["state"].text == "done" ? "No transcript text" : "Recording · " + source["state"].text
    }

    private func metadata(_ source: JSONValue) -> String {
        let diary = DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current)
        var parts: [String] = []
        if let start = AppModel.date(source["started_at"].text) {
            parts.append(diary.label(start, template: "d MMM HHmm"))
            if let end = AppModel.date(source["ended_at"].text), end > start {
                let seconds = Int(end.timeIntervalSince(start).rounded())
                parts.append(seconds < 60 ? "\(seconds) sec" : "\(max(1, seconds / 60)) min")
            }
        }
        if !source["meeting_hint_ids"].array.isEmpty { parts.append("Meeting hint") }
        if !source["sections"].array.isEmpty { parts.append("\(source["sections"].array.count) topics") }
        if !source["alternate_sources"].array.isEmpty { parts.append("\(source["alternate_sources"].array.count + 1) uploads") }
        return parts.joined(separator: " · ")
    }
}
struct SourceSelection: Identifiable { let id = UUID(); let source: JSONValue }
struct SourceDetail: View {
    let source: JSONValue
    var onCorrect: (() -> Void)? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @State private var fresh: JSONValue?
    private var evidence: JSONValue { fresh ?? source }
    private var transcriptDescription: String {
        if evidence == .null { return "Source removed" }
        if evidence["state"].text == "expired" { return "Transcript expired under your retention policy." }
        if !evidence["text"].text.isEmpty { return evidence["text"].text }
        return evidence["state"].text == "done" ? "No transcript text" : "Transcription pending"
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
                    ImportanceButton(target: ImportanceTarget(kind: "source", id: source["id"].text), label: "recording").disabled(evidence == .null)
                    SourceTranscriptTopicsView(source: evidence, editing: editing)
                    Text(evidence["id"].text).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                }
                if !source["alternate_sources"].array.isEmpty && evidence != .null {
                    Section("Other uploads of this recording") {
                        SourceList(sources: source["alternate_sources"].array)
                    }
                }
                NavigationLink { SourceLabelsView(sourceID: evidence["id"].text) } label: { Label("People & projects", systemImage: "person.2") }.disabled(evidence == .null)
                NavigationLink {
                    SpeakersView(kind: "source", conversationID: evidence["id"].text)
                } label: { Label("Speakers · listen & name", systemImage: "person.wave.2") }
                    .disabled(evidence == .null).accessibilityIdentifier("source.speakers")
                NavigationLink("Create decision from this source") {
                    SourceDecisionCreation(sourceID: evidence["id"].text)
                }.disabled(evidence["text"].text.isEmpty)
                Section("Transcript correction") {
                    if editing {
                        TextEditor(text: $draft).frame(minHeight: 160).accessibilityLabel("Correct transcript")
                        Button("Save correction") {
                            let address = model.archiveIdentity
                            loading = true
                            Task {
                                defer { loading = false }
                                do {
                                    guard model.archiveIdentity == address else { return }
                                    let corrected = try await model.client().request("v1/transcripts/\(evidence["id"].text)", method: "PATCH", body: ["text": draft])
                                    guard !Task.isCancelled, model.archiveIdentity == address else { return }
                                    fresh = corrected
                                    onCorrect?(); dismiss()
                                } catch { self.error = error.localizedDescription }
                            }
                        }.disabled(loading || draft.unicodeScalars.count > 200000)
                        Button("Cancel edit") { editing = false }
                    } else { Button("Correct transcript") { draft = evidence["text"].text; editing = true } }
                }
                if evidence != .null {
                    TranscriptionCleanupReviewView(source: evidence) { draft = evidence["text"].text; editing = true }
                    SourceActionsView(source: evidence) { onCorrect?(); dismiss() }
                }
                if evidence["state"].text == "blocked" || !evidence["error_code"].text.isEmpty ||
                    (evidence["state"].text == "done" && evidence["text"].text.isEmpty && evidence["audio_available"] != .bool(false) &&
                     ["silence_gate", "empty_stt"].contains(evidence["transcription_outcome"].text)) {
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
                        do {
                            let identity = model.archiveIdentity
                            let audio = try await model.client().download("v1/audio/\(evidence["id"].text)", query: [:])
                            guard model.archiveIdentity == identity, !Task.isCancelled else { return }
                            model.play(audio)
                        }
                        catch { self.error = error.localizedDescription }
                    }
                } label: { Label(loading ? "Loading audio…" : "Play original audio", systemImage: "play.circle") }.disabled(loading || evidence["audio_availability"].text == "missing")
                }
                ErrorNote(message: error)
            }.task(id: model.archiveRevision + model.archiveIdentity + String(describing: scenePhase)) {
                guard scenePhase == .active else { return }
                let identity = model.archiveIdentity
                do {
                    let result = try await model.client().request("v1/transcripts/\(source["id"].text)")
                    guard !Task.isCancelled, model.archiveIdentity == identity else { return }
                    fresh = result; error = nil
                } catch {
                    guard !Task.isCancelled, model.archiveIdentity == identity else { return }
                    self.error = error.localizedDescription
                    if (error as? BackendFailure)?.status == 404 { fresh = .null }
                }
            }.omiScreen().navigationTitle("Recording").navigationBarTitleDisplayMode(.inline)
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

struct ImportanceTarget: Codable, Hashable {
    let kind: String
    let id: String
    var section = ""
}
struct ImportanceDirectory: Decodable { let items: [ImportanceTarget] }

struct ImportanceButton: View {
    let target: ImportanceTarget
    let label: String
    @Environment(AppModel.self) private var model
    @State private var error: String?
    private var important: Bool { model.importanceMarks.contains(target) }
    private var busy: Bool { model.importanceSaving.contains(target) }
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button {
                Task {
                    error = nil
                    if !model.importanceLoaded { await model.loadImportance(); return }
                    do { try await model.setImportant(target, to: !important) }
                    catch { self.error = error.localizedDescription }
                }
            } label: {
                Label(busy ? "Saving…" : !model.importanceLoaded && model.importanceError != nil ? "Retry importance" : important ? "Important" : "Mark important", systemImage: important ? "star.fill" : "star")
            }
            .buttonStyle(.borderless)
            .accessibilityLabel((important ? "Remove importance from " : "Mark as important: ") + label)
            .accessibilityValue(important ? "Important" : "Not marked important")
            .disabled(busy || (!model.importanceLoaded && model.importanceError == nil))
            ErrorNote(message: error ?? (!model.importanceLoaded ? model.importanceError : nil))
        }.task(id: model.archiveIdentity) { await model.loadImportance() }
    }
}

struct RecapImportanceView: View {
    let recap: JSONValue
    var body: some View {
        if UUID(uuidString: recap["output_id"].text) != nil {
            ImportanceButton(target: ImportanceTarget(kind: "recap", id: recap["output_id"].text), label: "day recap")
            DisclosureGroup("Important recap sections") {
                ForEach(Array(RecapSections.texts(recap["summary"].text).enumerated()), id: \.offset) { _, text in
                    VStack(alignment: .leading) {
                        Text(.init(text)).font(.callout)
                        ImportanceButton(target: ImportanceTarget(kind: "recap", id: recap["output_id"].text, section: text), label: "recap section")
                    }
                }
                Text("Marks belong to this saved recap version.").font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

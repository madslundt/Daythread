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
                .environment(\.locale, Locale(identifier: L10n.languages.contains(interfaceLanguagePreference) ? interfaceLanguagePreference : L10n.language))
        }
    }
}

enum AppAppearance: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: Self { self }
    var title: String { L10n.key(rawValue.capitalized) }
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

enum SettingsDestination: Hashable { case settings, profiles, account }
private enum RootTab: Hashable { case today, diary, capture, chat, more }

struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(CompletionNotifications.self) private var notifications
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedTab: RootTab = UserDefaults.standard.string(forKey: "selectedProfileID")?.isEmpty == false ? .today : .more
    @State private var settingsPath: [SettingsDestination] = []
    @State private var todayNavigationID = UUID()
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
            TabView(selection: Binding(get: { model.hasSelectedProfile ? selectedTab : .more }, set: { tab in
                if !model.hasSelectedProfile && tab != .more { settingsPath = [.profiles] }
                if tab == .today && model.hasSelectedProfile { todayNavigationID = UUID() }
                selectedTab = model.hasSelectedProfile ? tab : .more
            })) {
                NavigationStack { if model.hasSelectedProfile { DayView() } }.id(todayNavigationID).tabItem { Label(L10n.text("Today"), systemImage: "sun.max") }.tag(RootTab.today)
                NavigationStack { if model.hasSelectedProfile { DiaryView() } }.tabItem { Label(L10n.text("Diary"), systemImage: "book.closed") }.tag(RootTab.diary)
                NavigationStack { if model.hasSelectedProfile { CaptureView() } }.tabItem { Label(L10n.text("Capture"), systemImage: "waveform") }.tag(RootTab.capture)
                NavigationStack { if model.hasSelectedProfile { ChatView() } }.tabItem { Label(L10n.text("Chat"), systemImage: "bubble.left.and.bubble.right") }.tag(RootTab.chat)
                NavigationStack(path: $settingsPath) {
                    MoreView().navigationDestination(for: SettingsDestination.self) { destination in
                        switch destination {
                        case .settings: SettingsView()
                        case .account: ManagedAccountView()
                        case .profiles: ProfilesSettingsView()
                        }
                    }
                }.tabItem { Label(L10n.text("More"), systemImage: "square.grid.2x2") }.tag(RootTab.more)
            }
            .id(model.archiveIdentity + (model.usesManagedHosting && !model.managedAccount.hostingVerified ? ".offline" : ""))
            .environment(\.goToToday, {
                todayNavigationID = UUID()
                selectedTab = .today
            })
        .task {
            notifications.configure(model: model)
            notifications.setActive(scenePhase == .active)
            if !model.hasSelectedProfile { settingsPath = [model.usesManagedHosting ? .account : .profiles] }
            await model.refreshStatus()
            while !Task.isCancelled {
                await model.tick()
                try? await Task.sleep(for: .seconds(1))
            }
        }
        .onChange(of: model.selectedProfileID) { _, _ in
            settingsPath = model.hasSelectedProfile ? [] : [model.usesManagedHosting ? .account : .profiles]
            selectedTab = model.hasSelectedProfile ? .today : .more
        }
        .onChange(of: model.hasSelectedProfile) { _, ready in
            notifications.configure(model: model)
            if !ready { settingsPath = [model.usesManagedHosting ? .account : .profiles]; selectedTab = .more }
        }
        .onChange(of: model.managedAccount.hostingVerified) { _, _ in notifications.configure(model: model) }
        .onChange(of: model.managedAccount.sessionID) { _, _ in notifications.configure(model: model) }
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
            Button(L10n.text("OK")) { model.error = nil }
        } message: { Text(L10n.key(model.error ?? "")) }
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            Form {
                Section {
                    TextField(L10n.text("Profile name"), text: $name)
                        .textContentType(.nickname).accessibilityIdentifier("profiles.name")
                } footer: {
                    Text(L10n.text("Each profile has its own recordings, diary and chat. Settings apply to every profile. Anyone using this app can select a profile."))
                }
                ErrorNote(message: error)
            }.navigationTitle(profile == nil ? L10n.text("Create profile") : L10n.text("Edit profile")).navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() }.disabled(saving) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(saving ? L10n.text("Saving…") : profile == nil ? L10n.text("Create") : L10n.text("Save")) {
                            saving = true
                            Task {
                                defer { saving = false }
                                do {
                                    if let profile { try await model.renameProfile(profile, name: name) }
                                    else { try await model.createProfile(name: name) }
                                    dismiss()
                                }
                                catch { self.error = L10n.error(error) }
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 8) {
            Text(eyebrow).textCase(.uppercase).font(.caption.weight(.semibold)).tracking(1.6).foregroundStyle(Theme.accent)
            Text(title).font(.system(.largeTitle, design: .serif)).foregroundStyle(.primary)
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        if let message { Label(L10n.key(message), systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.red).textSelection(.enabled) }
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        ForEach(Array(sources.enumerated()), id: \.element["id"].text) { _, source in
            Button { selected = SourceSelection(source: source) } label: {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: "waveform").font(.body).foregroundStyle(Theme.sage)
                        .frame(width: 36, height: 42).background(Theme.sage.opacity(0.08), in: RoundedRectangle(cornerRadius: 11))
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 6) {
                        Text(title(source)).font(.body.weight(.medium)).foregroundStyle(.primary).lineLimit(3)
                        Text(metadata(source)).font(.caption).foregroundStyle(.secondary)
                        if !source["tags"].array.isEmpty {
                            Text(source["tags"].array.map { $0["name"].text }.joined(separator: ", ")).font(.caption).foregroundStyle(Theme.accent)
                        }
                        if let match = source["matches"].array.first, !source["title"].text.isEmpty {
                            Text(match["text"].text).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                    if model.importanceMarks.contains(.init(kind: "source", id: source["id"].text)) || source["important"] == .bool(true) {
                        Image(systemName: "star.fill").font(.caption).foregroundStyle(Theme.accent).accessibilityLabel(L10n.text("Important"))
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
        return source["state"].text == "done" ? L10n.text("No transcript text") : L10n.text("Recording · ") + source["state"].text
    }

    private func metadata(_ source: JSONValue) -> String {
        let diary = DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current)
        var parts: [String] = []
        if let start = AppModel.date(source["started_at"].text) {
            parts.append(diary.label(start, template: "d MMM HHmm"))
            if let end = AppModel.date(source["ended_at"].text), end > start {
                let seconds = Int(end.timeIntervalSince(start).rounded())
                parts.append(seconds < 60 ? L10n.text("\(seconds) sec") : L10n.text("\(max(1, seconds / 60)) min"))
            }
        }
        if !source["meeting_hint_ids"].array.isEmpty { parts.append(L10n.text("Meeting hint")) }
        if !source["sections"].array.isEmpty { parts.append(L10n.text("\(source["sections"].array.count) topics")) }
        if !source["alternate_sources"].array.isEmpty { parts.append(L10n.text("\(source["alternate_sources"].array.count + 1) uploads")) }
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
        if evidence == .null { return L10n.text("Source removed") }
        if evidence["state"].text == "expired" { return L10n.text("Transcript expired under your retention policy.") }
        if !evidence["text"].text.isEmpty { return evidence["text"].text }
        return evidence["state"].text == "done" ? L10n.text("No transcript text") : L10n.text("Transcription pending")
    }
    @State private var error: String?
    @State private var loading = false
    @State private var editing = false
    @State private var draft = ""
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            List {
                Section(L10n.text("Recorded evidence")) {
                    NavigationLink(L10n.text("Report incorrect or missing information")) { FeedbackEditor(sourceID: evidence["id"].text) }
                    Text(sourceDate).font(.caption).textSelection(.enabled)
                    ImportanceButton(target: ImportanceTarget(kind: "source", id: source["id"].text), label: L10n.key("recording")).disabled(evidence == .null)
                    SourceTranscriptTopicsView(source: evidence, editing: editing)
                    Text(evidence["id"].text).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                }
                NavigationLink(L10n.text("Conversation tags")) {
                    ConversationTagsView(sourceID: evidence["id"].text, onChanged: onCorrect)
                }.disabled(evidence == .null || evidence["state"].text == "expired")
                ConversationLocationView(location: evidence["source_metadata"]["location"])
                if !source["alternate_sources"].array.isEmpty && evidence != .null {
                    Section(L10n.text("Other uploads of this recording")) {
                        SourceList(sources: source["alternate_sources"].array)
                    }
                }
                NavigationLink { SourceLabelsView(sourceID: evidence["id"].text) } label: { Label(L10n.text("People & projects"), systemImage: "person.2") }.disabled(evidence == .null)
                NavigationLink {
                    SpeakersView(kind: "source", conversationID: evidence["id"].text)
                } label: { Label(L10n.text("Speakers · listen & name"), systemImage: "person.wave.2") }
                    .disabled(evidence == .null).accessibilityIdentifier("source.speakers")
                NavigationLink(L10n.text("Create decision from this source")) {
                    SourceDecisionCreation(sourceID: evidence["id"].text)
                }.disabled(evidence["text"].text.isEmpty)
                Section(L10n.text("Transcript correction")) {
                    if editing {
                        TextEditor(text: $draft).frame(minHeight: 160).accessibilityLabel(L10n.text("Correct transcript"))
                        Button(L10n.text("Save correction")) {
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
                                } catch { self.error = L10n.error(error) }
                            }
                        }.disabled(loading || draft.unicodeScalars.count > 200000)
                        Button(L10n.text("Cancel edit")) { editing = false }
                    } else { Button(L10n.text("Correct transcript")) { draft = evidence["text"].text; editing = true } }
                }
                if evidence != .null {
                    TranscriptionCleanupReviewView(source: evidence) { draft = evidence["text"].text; editing = true }
                    SourceActionsView(source: evidence) { onCorrect?(); dismiss() }
                }
                if evidence["state"].text == "blocked" || !evidence["error_code"].text.isEmpty ||
                    (evidence["state"].text == "done" && evidence["text"].text.isEmpty && evidence["audio_available"] != .bool(false) &&
                     ["silence_gate", "empty_stt"].contains(evidence["transcription_outcome"].text)) {
                    Button(L10n.text("Retry transcription after resolving the problem")) { Task {
                        do { _ = try await model.client().request("v1/audio/\(evidence["id"].text)/retry", method: "POST"); error = nil }
                        catch { self.error = L10n.error(error) }
                    } }
                }
                if evidence["audio_availability"].text == "missing" { Text(L10n.text("The archive expects audio, but the file is missing.")).font(.caption) }
                if evidence["audio_available"] == .bool(false) {
                    Text(SourceProvenanceView.isTranscriptImport(evidence)
                         ? L10n.text("Imported from \(SourceProvenanceView.vendorLabel(for: evidence)) · transcript only; original audio unavailable.")
                         : L10n.text("Audio expired under retention; transcript remains available.")).font(.caption)
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
                        catch { self.error = L10n.error(error) }
                    }
                } label: { Label(loading ? L10n.text("Loading audio…") : L10n.text("Play original audio"), systemImage: "play.circle") }.disabled(loading || evidence["audio_availability"].text == "missing")
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
                    self.error = L10n.error(error)
                    if (error as? BackendFailure)?.status == 404 { fresh = .null }
                }
            }.omiScreen().navigationTitle(L10n.text("Recording")).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L10n.text("Done")) { dismiss() } } }
        }
    }
    private var sourceDate: String {
        guard let date = AppModel.date(evidence["started_at"].text) else { return evidence["started_at"].text }
        let formatter = DateFormatter(); formatter.locale = L10n.locale
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 4) {
            Button {
                Task {
                    error = nil
                    if !model.importanceLoaded { await model.loadImportance(); return }
                    do { try await model.setImportant(target, to: !important) }
                    catch { self.error = L10n.error(error) }
                }
            } label: {
                Label(busy ? L10n.text("Saving…") : !model.importanceLoaded && model.importanceError != nil ? L10n.text("Retry importance") : important ? L10n.text("Important") : L10n.text("Mark important"), systemImage: important ? "star.fill" : "star")
            }
            .buttonStyle(.borderless)
            .accessibilityLabel((important ? L10n.text("Remove importance from ") : L10n.text("Mark as important: ")) + label)
            .accessibilityValue(important ? L10n.text("Important") : L10n.text("Not marked important"))
            .disabled(busy || (!model.importanceLoaded && model.importanceError == nil))
            ErrorNote(message: error ?? (!model.importanceLoaded ? model.importanceError : nil))
        }.task(id: model.archiveIdentity) { await model.loadImportance() }
    }
}

struct RecapImportanceView: View {
    let recap: JSONValue
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        if UUID(uuidString: recap["output_id"].text) != nil {
            ImportanceButton(target: ImportanceTarget(kind: "recap", id: recap["output_id"].text), label: L10n.text("day recap"))
            DisclosureGroup(L10n.text("Important recap sections")) {
                ForEach(Array(RecapSections.texts(recap["summary"].text).enumerated()), id: \.offset) { _, text in
                    VStack(alignment: .leading) {
                        Text(.init(text)).font(.callout)
                        ImportanceButton(target: ImportanceTarget(kind: "recap", id: recap["output_id"].text, section: text), label: L10n.text("recap section"))
                    }
                }
                Text(L10n.text("Marks belong to this saved recap version.")).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

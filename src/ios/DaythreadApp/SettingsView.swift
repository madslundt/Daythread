import SwiftUI
import OmiCore

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("appearancePreference") private var appearance = AppAppearance.system
    @AppStorage("interfaceLanguagePreference") private var interfaceLanguagePreference = "system"
    @State private var error: String?
    @State private var status: String?
    @State private var loading = false
    @State private var summaryLanguage = "auto"
    @State private var recapLanguage = "auto"
    @State private var translationLanguages: [String] = []
    @State private var translationLanguagesLoaded = false
    @State private var timezone = TimeZone.current.identifier
    @State private var audioRetentionDays = 7
    @State private var transcriptRetentionDays = 30
    @State private var settingsLoaded = false
    private let retentionOptions = [0, 1, 7, 14, 30, 90, 180, 365, 730, 1825]
    @Environment(\.locale) private var interfaceLocale

    var body: some View {
        let _ = interfaceLocale
        List {
            Section {
                NavigationLink { generalSettings } label: {
                    DestinationLabel(title: L10n.text("General"), subtitle: L10n.text("Appearance, languages & time zone"), symbol: "slider.horizontal.3", color: Theme.accent)
                }.accessibilityIdentifier("settings.general")
                NavigationLink { recordingSettings } label: {
                    DestinationLabel(title: L10n.text("Recording & devices"), subtitle: L10n.text("This iPhone, uploads & connected devices"), symbol: "waveform", color: Theme.sage)
                }.accessibilityIdentifier("settings.capture")
                NavigationLink { storageSettings } label: {
                    DestinationLabel(title: L10n.text("Storage & privacy"), subtitle: L10n.text("Recordings, retention & location"), symbol: "archivebox", color: Theme.sage)
                }.accessibilityIdentifier("settings.storage")
                NavigationLink { LocationZonesView() } label: {
                    DestinationLabel(title: L10n.text("Location zones"), subtitle: L10n.text("Home, work, gym & other places"), symbol: "map", color: Theme.sage)
                }.accessibilityIdentifier("settings.locationZones")
                NavigationLink { accountSettings } label: {
                    DestinationLabel(title: L10n.text("Account & profiles"), subtitle: model.hasSelectedProfile ? model.selectedProfileName : L10n.text("Choose an archive to get started"), symbol: "person.crop.circle", color: Theme.sage)
                }.accessibilityIdentifier("settings.connections")
                if !model.usesManagedHosting {
                    NavigationLink { ServerConfigurationView() } label: {
                        DestinationLabel(title: L10n.text("Advanced"), subtitle: L10n.text("Models & your server"), symbol: "server.rack", color: Theme.accent)
                    }.accessibilityIdentifier("settings.server")
                }
            }
            saveFeedback
        }.listStyle(.insetGrouped).omiScreen()
            .navigationTitle(L10n.text("Settings")).navigationBarTitleDisplayMode(.large)
            .task { await loadSettings() }
    }

    private var generalSettings: some View {
        Form {
            Section(L10n.text("Appearance")) {
                Picker(L10n.text("Appearance"), selection: $appearance) {
                    ForEach(AppAppearance.allCases) { option in Text(option.title).tag(option) }
                }.accessibilityIdentifier("settings.appearance")
                Picker(L10n.text("Interface language"), selection: $interfaceLanguagePreference) {
                    Text(L10n.text("System language")).tag("system")
                    ForEach(L10n.languages, id: \.self) { code in
                        Text(verbatim: Locale(identifier: code).localizedString(forLanguageCode: code) ?? code).tag(code)
                    }
                }.accessibilityIdentifier("settings.interfaceLanguage")
            }
            Section {
                Picker(L10n.text("Summary language"), selection: $summaryLanguage) {
                    Text(L10n.text("Automatic / follow source language")).tag("auto")
                    ForEach(translationLanguageChoices(including: summaryLanguage == "auto" ? [] : [summaryLanguage]), id: \.self) { code in
                        Text(L10n.locale.localizedString(forLanguageCode: code) ?? code).tag(code)
                    }
                }.accessibilityIdentifier("settings.summaryLanguage")
                Picker(L10n.text("Recap language"), selection: $recapLanguage) {
                    Text(L10n.text("Automatic / follow source language")).tag("auto")
                    ForEach(translationLanguageChoices(including: recapLanguage == "auto" ? [] : [recapLanguage]), id: \.self) { code in
                        Text(L10n.locale.localizedString(forLanguageCode: code) ?? code).tag(code)
                    }
                }.accessibilityIdentifier("settings.recapLanguage")
                Picker(L10n.text("Time zone"), selection: $timezone) {
                    ForEach(TimeZone.knownTimeZoneIdentifiers, id: \.self) { zone in Text(zone).tag(zone) }
                }.accessibilityIdentifier("settings.timezone")
                Button(L10n.text("Save preferences")) { Task { await saveGeneral() } }
                    .disabled(loading || !settingsLoaded).accessibilityIdentifier("settings.saveSummaryLanguage")
            } footer: {
                Text(L10n.text("Choose summary and recap languages separately. Recap language applies to day and week recaps. Automatic follows the source language. The time zone sets the day boundaries in your archive."))
            }
            Section {
                NavigationLink {
                    TranslationLanguageSelectionView(languages: $translationLanguages)
                } label: {
                    LabeledContent(L10n.text("Allowed languages"), value: L10n.text("\(translationLanguages.count) selected"))
                }.accessibilityIdentifier("settings.translationLanguages")
                Button(L10n.text("Save translation languages")) { Task { await saveTranslationLanguages() } }
                    .accessibilityIdentifier("settings.saveTranslationLanguages")
            } header: { Text(L10n.text("Transcript, summary & recap translations")) } footer: {
                Text(L10n.text("Choose the languages offered by Translate for transcripts, summaries, and recaps. Translations are generated only when you click Translate."))
            }.disabled(loading || !translationLanguagesLoaded)
            CompletionNotificationSettings()
            saveFeedback
        }.omiScreen().navigationTitle(L10n.text("General")).navigationBarTitleDisplayMode(.inline)
    }

    private var recordingSettings: some View {
        @Bindable var model = model
        return Form {
            Section {
                NavigationLink(L10n.text("Recording controls")) { CaptureView() }
                Toggle(L10n.text("Upload only on Wi-Fi"), isOn: $model.uploadWiFiOnly)
                    .accessibilityIdentifier("settings.uploadWiFiOnly")
            } header: { Text(L10n.text("This iPhone")) } footer: {
                Text(L10n.text("Recordings wait on this iPhone until a permitted connection is available. Recording continues while uploads wait."))
            }
            Section {
                NavigationLink(L10n.text("Pendant details")) { pendantSettings }
                NavigationLink { OmiConnectionsView() } label: {
                    DestinationLabel(title: L10n.text("Omi import & audio forwarding"), subtitle: L10n.text("Optional cloud transcripts & audio receiver"), symbol: "arrow.triangle.2.circlepath", color: Theme.accent)
                }.accessibilityIdentifier("settings.omiConnections")
            } header: { Text(L10n.text("Devices & connections")) }
            saveFeedback
        }.omiScreen().navigationTitle(L10n.text("Recording & devices")).navigationBarTitleDisplayMode(.inline)
    }

    private var pendantSettings: some View {
        @Bindable var model = model
        return Form {
            Section(L10n.text("Pendant information")) {
                LabeledContent(L10n.text("Model"), value: model.bluetooth.model)
                LabeledContent(L10n.text("Firmware"), value: model.bluetooth.firmware)
                LabeledContent(L10n.text("Codec"), value: model.bluetooth.codec.map(String.init) ?? L10n.text("Unknown"))
                LabeledContent(L10n.text("Button notifications"), value: model.bluetooth.buttonAvailable ? L10n.text("Available") : L10n.text("Not discovered"))
                if model.bluetooth.connected { Button(L10n.text("Disconnect and forget Omi"), role: .destructive) { model.flush(); model.bluetooth.disconnect() } }
            }
            PendantClickSettings()
        }.omiScreen().navigationTitle(L10n.text("Pendant details")).navigationBarTitleDisplayMode(.inline)
    }

    private var storageSettings: some View {
        @Bindable var model = model
        return Form {
            archiveSection
            Section(L10n.text("On this iPhone")) {
                Picker(L10n.text("Maximum storage for recordings on this iPhone"), selection: $model.storageBudgetMB) {
                    Text(L10n.text("1 GB")).tag(1000)
                    Text(L10n.text("2 GB")).tag(2000)
                    Text(L10n.text("4 GB")).tag(4000)
                    Text(L10n.text("8 GB")).tag(8000)
                }
                Text(L10n.text("Unsent recordings stay on this iPhone. Uploaded pendant audio can be removed after one hour. Recording stops when storage is full; existing recordings are kept."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Toggle(L10n.text("Save conversation locations"), isOn: $model.recordingLocationsEnabled)
                    .accessibilityIdentifier("settings.recordingLocations")
                if model.recordingLocationsEnabled, let message = model.locations.error {
                    Text(L10n.key(message)).font(.caption).foregroundStyle(.secondary)
                }
            } header: { Text(L10n.text("Location")) } footer: {
                Text(L10n.text("Off by default. While pendant capture is active, save a recent phone location and its accuracy with each recording, including offline. Locations sync to your private archive and expire with transcripts. iOS may show a location indicator. Turning this off stops new observations; existing locations stay until their recordings expire or are deleted."))
            }
            saveFeedback
        }.omiScreen().navigationTitle(L10n.text("Storage & privacy")).navigationBarTitleDisplayMode(.inline)
    }

    private var accountSettings: some View {
        Form {
            Section {
                NavigationLink { ManagedAccountView() } label: {
                    DestinationLabel(title: L10n.text("Account & connection"), subtitle: model.managedAccount.signedIn ? L10n.text("Managed hosting & model credits") : L10n.text("Optional sign-in or your own backend"), symbol: "person.crop.circle", color: Theme.sage)
                }.accessibilityIdentifier("settings.managedAccount")
                NavigationLink { ProfilesSettingsView() } label: {
                    DestinationLabel(title: L10n.text("Profiles"), subtitle: model.hasSelectedProfile ? model.selectedProfileName : L10n.text("Choose an archive to get started"), symbol: "person.crop.circle", color: Theme.accent)
                }.accessibilityIdentifier("settings.profiles")
            }
            Section {
                if !model.usesManagedHosting {
                    NavigationLink { BackendEndpointsView() } label: {
                        DestinationLabel(title: L10n.text("Server connection"), subtitle: model.backendAddress, symbol: "network", color: Theme.sage)
                    }.accessibilityIdentifier("settings.backendEndpoints")
                }
                if let url = try? model.client().base {
                    Link(destination: url) { Label(L10n.text("Open active web app"), systemImage: "arrow.up.right.square") }
                }
            }
        }.omiScreen().navigationTitle(L10n.text("Account & profiles")).navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder private var saveFeedback: some View {
        if status != nil || error != nil || loading {
            Section {
                if loading { ProgressView(L10n.text("Saving preferences…")) }
                if let status { Label(L10n.key(status), systemImage: "checkmark.circle").font(.callout).foregroundStyle(Theme.sage) }
                ErrorNote(message: error)
                if error != nil { Button(L10n.text("Reload preferences")) { Task { await loadSettings() } }.disabled(loading) }
            }
        }
    }
    private var archiveSection: some View {
        Section {
            if model.usesManagedHosting {
                Text(L10n.text("Audio and transcripts are kept until you delete them."))
            } else {
                Picker(L10n.text("Keep original audio"), selection: $audioRetentionDays) {
                    ForEach(retentionChoices(audioRetentionDays), id: \.self) { days in Text(retentionLabel(days)).tag(days) }
                }.accessibilityIdentifier("settings.audioRetention")
                Picker(L10n.text("Keep transcripts"), selection: $transcriptRetentionDays) {
                    ForEach(retentionChoices(transcriptRetentionDays), id: \.self) { days in Text(retentionLabel(days)).tag(days) }
                }.accessibilityIdentifier("settings.transcriptRetention")
                Text(L10n.text("Expiring transcripts also deletes related diary and chat content, inbox items, manual edits and history. Shorter periods apply to existing recordings when saved."))
                    .font(.caption).foregroundStyle(.secondary)
                Button(L10n.text("Save archive retention")) { Task { await saveRetention() } }
                    .disabled(loading || !settingsLoaded).accessibilityIdentifier("settings.saveRetention")
            }
        } header: { Text(L10n.text("Archive")) }
    }
    private func retentionChoices(_ current: Int) -> [Int] { Array(Set(retentionOptions + [current])).sorted() }
    private func retentionLabel(_ days: Int) -> String {
        days == 0 ? L10n.text("Until I remove it") : days == 1 ? L10n.text("\(days) day") : L10n.text("\(days) days")
    }
    private func loadSettings() async {
        error = nil; settingsLoaded = false; translationLanguagesLoaded = false
        do {
            let settings = try await model.client().request("v1/settings")
            guard !Task.isCancelled else { return }
            if !settings["summary_language"].text.isEmpty { summaryLanguage = settings["summary_language"].text }
            if !settings["recap_language"].text.isEmpty { recapLanguage = settings["recap_language"].text }
            if case .array = settings["translation_languages"] {
                translationLanguages = settings["translation_languages"].array.map(\.text)
                translationLanguagesLoaded = true
            }
            if TimeZone(identifier: settings["timezone"].text) != nil { timezone = settings["timezone"].text }
            if case .number = settings["audio_retention_days"], case .number = settings["transcript_retention_days"] {
                audioRetentionDays = settings["audio_retention_days"].integer
                transcriptRetentionDays = settings["transcript_retention_days"].integer
            }
            settingsLoaded = true
        } catch { if !Task.isCancelled { self.error = L10n.error(error) } }
    }
    private func saveRetention() async {
        guard settingsLoaded, !model.usesManagedHosting else { return }
        loading = true; error = nil; status = nil; defer { loading = false }
        do {
            let settings = try await model.client().request("v1/settings", method: "PATCH", body: ["audio_retention_days": audioRetentionDays, "transcript_retention_days": transcriptRetentionDays])
            audioRetentionDays = settings["audio_retention_days"].integer
            transcriptRetentionDays = settings["transcript_retention_days"].integer
            status = L10n.text("Archive retention saved")
        } catch { self.error = L10n.error(error) }
    }
    private func saveTranslationLanguages() async {
        guard translationLanguagesLoaded else { return }
        loading = true; error = nil; status = nil; defer { loading = false }
        do {
            let settings = try await model.client().request("v1/settings", method: "PATCH", body: ["translation_languages": translationLanguages])
            translationLanguages = settings["translation_languages"].array.map(\.text)
            status = L10n.text("Translation languages saved")
        } catch { self.error = L10n.error(error) }
    }
    private func saveGeneral() async {
        guard settingsLoaded else { return }
        loading = true; error = nil; status = nil; defer { loading = false }
        do {
            let settings = try await model.client().request("v1/settings", method: "PATCH", body: ["summary_language": summaryLanguage, "recap_language": recapLanguage, "timezone": timezone])
            summaryLanguage = settings["summary_language"].text
            if !settings["recap_language"].text.isEmpty { recapLanguage = settings["recap_language"].text }
            timezone = settings["timezone"].text
            model.serverTimeZoneIdentifier = timezone
            status = L10n.text("Preferences saved")
        } catch { self.error = L10n.error(error) }
    }
}

func translationLanguageChoices(including additional: [String] = []) -> [String] {
    let codes = ("aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy " +
            "da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz " +
            "ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv " +
            "mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt " +
            "qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr " +
            "ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu").split(separator: " ").map(String.init)
    return Set(codes + additional).sorted {
        (L10n.locale.localizedString(forLanguageCode: $0) ?? $0).localizedStandardCompare(L10n.locale.localizedString(forLanguageCode: $1) ?? $1) == .orderedAscending
    }
}

struct TranslationLanguageSelectionView: View {
    @Binding var languages: [String]
    @State private var search = ""
    @Environment(\.locale) private var interfaceLocale
    private var choices: [String] {
        translationLanguageChoices(including: languages).filter { code in
            search.isEmpty || code.localizedCaseInsensitiveContains(search) ||
                (L10n.locale.localizedString(forLanguageCode: code) ?? code).localizedCaseInsensitiveContains(search)
        }
    }
    var body: some View {
        let _ = interfaceLocale
        List {
            if let first = languages.first {
                Text(L10n.text("Default: \(L10n.locale.localizedString(forLanguageCode: first) ?? first)"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(choices, id: \.self) { code in
                Toggle(L10n.locale.localizedString(forLanguageCode: code) ?? code, isOn: Binding(
                    get: { languages.contains(code) },
                    set: { enabled in
                        if enabled && !languages.contains(code) { languages.append(code) }
                        else if !enabled { languages.removeAll { $0 == code } }
                    }
                )).accessibilityIdentifier("settings.translationLanguage.\(code)")
            }
        }.navigationTitle(L10n.text("Translation languages"))
            .searchable(text: $search, prompt: L10n.text("Find a language"))
    }
}

private struct ProfileEditor: Identifiable {
    let profile: AppModel.Profile?
    var id: String { profile?.id ?? "create" }
}

struct ProfilesSettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var editor: ProfileEditor?
    @State private var loading = false
    @State private var error: String?

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        Form {
            Section {
                Picker(L10n.text("Active profile"), selection: Binding(get: { model.selectedProfileID }, set: { id in
                    model.selectProfile(model.profiles.first { $0.id == id })
                })) {
                    Text(L10n.text("Choose a profile…")).tag("")
                    ForEach(model.profiles) { profile in Text(profile.name).tag(profile.id) }
                }.accessibilityIdentifier("profiles.select")
                if !model.hasSelectedProfile {
                    Text(L10n.text("Choose a profile to open recordings, diary and other pages. All settings remain available."))
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            Section {
                ForEach(model.profiles) { profile in
                    HStack(spacing: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(profile.name).font(.body.weight(.medium))
                            if profile.id == model.selectedProfileID {
                                Text(L10n.text("Current profile")).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Button(L10n.text("Edit")) { editor = ProfileEditor(profile: profile) }
                            .accessibilityLabel(L10n.text("Edit ") + profile.name)
                    }.padding(.vertical, 4)
                }
                if !model.usesManagedHosting {
                    Button { editor = ProfileEditor(profile: nil) } label: {
                        Label(L10n.text("Create profile"), systemImage: "person.badge.plus")
                    }.accessibilityIdentifier("profiles.create")
                }
            } footer: {
                Text(model.usesManagedHosting
                    ? L10n.text("Only your signed-in account can access these profiles.")
                    : L10n.text("Each profile has its own recordings, diary and chat. Settings apply to every profile. Anyone using this server can select any profile."))
            }
            if loading { ProgressView(L10n.text("Loading profiles…")) }
            if let error {
                Section {
                    ErrorNote(message: error)
                    Button(L10n.text("Try again")) { Task { await load() } }
                }
            }
        }.omiScreen().navigationTitle(L10n.text("Profiles")).navigationBarTitleDisplayMode(.inline)
            .task { await load() }
            .sheet(item: $editor) { selection in ProfileEditorView(profile: selection.profile) }
    }

    private func load() async {
        loading = true; error = nil
        defer { loading = false }
        do { try await model.loadProfiles() }
        catch { self.error = L10n.error(error) }
    }
}

/// The same controls are available from device settings and the connected pendant screen.
struct PendantClickSettings: View {
    @Environment(AppModel.self) private var model
    @Environment(CompletionNotifications.self) private var notifications
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        @Bindable var model = model
        Section {
            Picker(L10n.text("Single click"), selection: $model.singleClickAction) {
                ForEach(WearableAction.allCases, id: \.self) { action in Text(label(action)).tag(action) }
            }.accessibilityIdentifier("settings.singleClickAction")
            Picker(L10n.text("Double click"), selection: $model.doubleClickAction) {
                ForEach(WearableAction.allCases, id: \.self) { action in Text(label(action)).tag(action) }
            }.accessibilityIdentifier("settings.doubleClickAction")
            Picker(L10n.text("Recent audio duration"), selection: $model.recentAudioSeconds) {
                ForEach(ClickControls.durations, id: \.self) { seconds in Text(L10n.text("\(seconds) sec")).tag(seconds) }
            }.accessibilityIdentifier("settings.recentAudioSeconds")
            Toggle(L10n.text("Click notifications"), isOn: $model.clickNotificationsEnabled)
                .accessibilityIdentifier("settings.clickNotifications")
            if model.clickNotificationsEnabled && !notifications.authorized {
                if notifications.permission == .denied {
                    Text(L10n.text("Notifications are disabled in iPhone Settings."))
                    Link(L10n.text("Notification settings on this iPhone"), destination: URL(string: UIApplication.openNotificationSettingsURLString)!)
                } else {
                    Button(L10n.text("Allow click notifications")) { Task { await notifications.requestPermission() } }
                        .accessibilityIdentifier("settings.allowClickNotifications")
                }
            }
        } header: { Text(L10n.text("Click controls")) } footer: {
            Text(L10n.text("Summarize, answer and translate use audio before the click. Translations use your first allowed translation language."))
            Text(L10n.text("Click notifications confirm pendant actions immediately, including offline. Final result notifications remain enabled separately."))
        }.task(id: scenePhase) {
            if scenePhase == .active { await notifications.refreshAuthorization() }
        }
    }
    private func label(_ action: WearableAction) -> String {
        switch action {
        case .mark: L10n.text("Mark as important")
        case .meetingToggle: L10n.text("Start / end meeting")
        case .summarize: L10n.text("Summarize recent audio")
        case .answer: L10n.text("Answer recent question")
        case .translate: L10n.text("Translate recent audio")
        case .captureToggle: L10n.text("Start / pause recording")
        case .none: L10n.text("Do nothing")
        }
    }
}

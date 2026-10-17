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
    @State private var translationLanguages: [String] = ["en"]
    @State private var translationLanguagesLoaded = false
    @State private var timeout = 120
    @State private var audioRetentionDays = 7
    @State private var transcriptRetentionDays = 30
    @State private var retentionLoaded = false
    private let retentionOptions = [0, 1, 7, 14, 30, 90, 180, 365, 730, 1825]
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Section {
                EditorialTitle(eyebrow: L10n.text("Make it yours"), title: L10n.text("Your space.")).editorialRow()
            }
            Section(L10n.text("Appearance")) {
                Picker(L10n.text("Appearance"), selection: $appearance) {
                    ForEach(AppAppearance.allCases) { option in Text(option.title).tag(option) }
                }.accessibilityIdentifier("settings.appearance")
                Picker(L10n.text("Interface language"), selection: $interfaceLanguagePreference) {
                    Text(L10n.text("System language")).tag("system")
                    Text("English").tag("en")
                    Text("Dansk").tag("da")
                }.accessibilityIdentifier("settings.interfaceLanguage")
                Text(L10n.text("Saved on this iPhone. This changes the interface, not transcription or summary languages."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            CompletionNotificationSettings()
            Section(L10n.text("Everyday preferences")) {
                NavigationLink { captureSettings } label: {
                    DestinationLabel(title: L10n.text("Capture & uploads"), subtitle: L10n.text("Wi-Fi, meeting gestures & your pendant"), symbol: "waveform", color: Theme.sage)
                }.accessibilityIdentifier("settings.capture")
                NavigationLink { languageSettings } label: {
                    DestinationLabel(title: L10n.text("Languages & summaries"), subtitle: L10n.text("Recap language & transcript translations"), symbol: "character.bubble", color: Theme.accent)
                }.accessibilityIdentifier("settings.languages")
                NavigationLink { storageSettings } label: {
                    DestinationLabel(title: L10n.text("Archive & phone storage"), subtitle: L10n.text("Retention periods & local capture budget"), symbol: "archivebox", color: Theme.sage)
                }.accessibilityIdentifier("settings.storage")
            }
            Section(L10n.text("Your setup")) {
                NavigationLink { ManagedAccountView() } label: {
                    DestinationLabel(title: L10n.text("Account & connection"), subtitle: model.managedAccount.signedIn ? L10n.text("Managed hosting & model credits") : L10n.text("Optional sign-in or your own backend"), symbol: "person.crop.circle", color: Theme.sage)
                }.accessibilityIdentifier("settings.managedAccount")
                NavigationLink(L10n.text("Location zones")) { LocationZonesView() }
                    .accessibilityIdentifier("settings.locationZones")
                NavigationLink {
                    if model.usesManagedHosting { ManagedAccountView() } else { ServerConfigurationView() }
                } label: {
                    DestinationLabel(title: L10n.text("Models & server"), subtitle: L10n.text("Speech, language models, keys & advanced options"), symbol: "slider.horizontal.3", color: Theme.accent)
                }.accessibilityIdentifier("settings.server")
                NavigationLink { connectionSettings } label: {
                    DestinationLabel(title: L10n.text("Connections & profiles"), subtitle: L10n.text("Home-core, your archives & Omi imports"), symbol: "network", color: Theme.sage)
                }.accessibilityIdentifier("settings.connections")
            }
            saveFeedback
        }.listStyle(.insetGrouped).omiScreen()
            .navigationTitle(L10n.text("Settings")).navigationBarTitleDisplayMode(.inline)
            .task { timeout = model.meetingMinutes; await loadSettings() }
    }

    private var captureSettings: some View {
        @Bindable var model = model
        return Form {
            saveFeedback
            Section {
                Toggle(L10n.text("Upload only on Wi-Fi"), isOn: $model.uploadWiFiOnly)
                    .accessibilityIdentifier("settings.uploadWiFiOnly")
            } header: { Text(L10n.text("Uploads")) } footer: {
                Text(L10n.text("Saved on this iPhone. When enabled, recordings, flags and capture gaps wait for Wi-Fi before uploading. Capture continues locally. Sync now follows this setting. Turn it off to allow cellular uploads."))
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
            Section(L10n.text("Meeting hints")) {
                Picker(L10n.text("Wearable gesture"), selection: $model.gesture) {
                    Text(L10n.text("Double click · stock firmware")).tag(MeetingGesture.doubleClick)
                    Text(L10n.text("Hold · verified firmware only")).tag(MeetingGesture.verifiedHold)
                }
                Text(L10n.text("Stock Omi firmware uses a three-second hold to power off. Choose hold only after verifying that your firmware emits a resolved long-tap event."))
                    .font(.caption).foregroundStyle(.secondary)
                Stepper(L10n.text("Auto-stop after \(timeout) minutes"), value: $timeout, in: 1...480, step: 5)
                Button(L10n.text("Save meeting timeout to home-core")) { Task { await savePolicy() } }.disabled(loading)
                Text(L10n.text("Meetings shorter than one minute are ignored. Flags have a one-minute cooldown. Hints include the surrounding speech and do not define exact boundaries."))
                    .font(.callout).foregroundStyle(.secondary)
            }
            Section(L10n.text("Pendant information")) {
                LabeledContent(L10n.text("Model"), value: model.bluetooth.model)
                LabeledContent(L10n.text("Firmware"), value: model.bluetooth.firmware)
                LabeledContent(L10n.text("Codec"), value: model.bluetooth.codec.map(String.init) ?? L10n.text("Unknown"))
                LabeledContent(L10n.text("Button notifications"), value: model.bluetooth.buttonAvailable ? L10n.text("Available") : L10n.text("Not discovered"))
                if model.bluetooth.connected { Button(L10n.text("Disconnect and forget Omi"), role: .destructive) { model.flush(); model.bluetooth.disconnect() } }
            }
        }.omiScreen().navigationTitle(L10n.text("Capture & uploads")).navigationBarTitleDisplayMode(.inline)
            .task { if !retentionLoaded { await loadSettings() } }
    }

    private var languageSettings: some View {
        Form {
            saveFeedback
            Section {
                Picker(L10n.text("Summary language"), selection: $summaryLanguage) {
                    Text(L10n.text("Automatic / follow source language")).tag("auto")
                    ForEach(summaryLanguageChoices, id: \.self) { code in
                        Text(L10n.locale.localizedString(forLanguageCode: code) ?? code).tag(code)
                    }
                }.accessibilityIdentifier("settings.summaryLanguage")
                Button(L10n.text("Save summary language")) { Task { await saveSummaryLanguage() } }
                    .disabled(loading).accessibilityIdentifier("settings.saveSummaryLanguage")
            } header: { Text(L10n.text("Summaries")) } footer: {
                Text(L10n.text("Choose the language for recaps and summaries. Automatic follows the source language."))
            }
            Section {
                NavigationLink {
                    TranslationLanguageSelectionView(languages: $translationLanguages)
                } label: {
                    LabeledContent(L10n.text("Allowed languages"), value: L10n.text("\(translationLanguages.count) selected"))
                }.accessibilityIdentifier("settings.translationLanguages")
                Button(L10n.text("Save translation languages")) { Task { await saveTranslationLanguages() } }
                    .accessibilityIdentifier("settings.saveTranslationLanguages")
            } header: { Text(L10n.text("Transcript translations")) } footer: {
                Text(L10n.text("Choose the languages offered by the Translate button. The first selected language is the default. Translations are available for every summary language, including Automatic. Save an empty list to disable translation."))
            }.disabled(loading || !translationLanguagesLoaded)
            Section(L10n.text("Transcription")) {
                Text(L10n.text("Transcription automatically keeps the languages spoken in each conversation."))
                    .font(.callout).foregroundStyle(.secondary)
                NavigationLink(L10n.text("Models, keys & all server settings")) {
                    if model.usesManagedHosting { ManagedAccountView() } else { ServerConfigurationView() }
                }
            }
        }.omiScreen().navigationTitle(L10n.text("Languages & summaries")).navigationBarTitleDisplayMode(.inline)
            .task { if !translationLanguagesLoaded { await loadSettings() } }
    }

    private var storageSettings: some View {
        @Bindable var model = model
        return Form {
            saveFeedback
            archiveSection
            Section(L10n.text("On this iPhone")) {
                Picker(L10n.text("Phone capture budget"), selection: $model.storageBudgetMB) {
                    Text(L10n.text("1 GB")).tag(1000)
                    Text(L10n.text("2 GB")).tag(2000)
                    Text(L10n.text("4 GB")).tag(4000)
                    Text(L10n.text("8 GB")).tag(8000)
                }
                Text(L10n.text("Original compressed frames and decoded WAV chunks are kept until home-core acknowledges them. Synced audio is eligible for removal from the phone after one hour; event history stays for replay. Lowering the budget preserves existing recordings and stops new capture when full."))
                    .font(.callout).foregroundStyle(.secondary)
                Text(L10n.text("Capture can continue without home-core. Summaries and chat use your backend's local models. Pendant recordings and button events while Bluetooth is disconnected require separate, verified device storage support."))
                    .font(.callout).foregroundStyle(.secondary)
            }
        }.omiScreen().navigationTitle(L10n.text("Archive & phone storage")).navigationBarTitleDisplayMode(.inline)
            .task { if !retentionLoaded { await loadSettings() } }
    }

    private var connectionSettings: some View {
        Form {
            Section {
                NavigationLink { ProfilesSettingsView() } label: {
                    DestinationLabel(title: L10n.text("Profiles"), subtitle: model.hasSelectedProfile ? model.selectedProfileName : L10n.text("Choose an archive to get started"), symbol: "person.crop.circle", color: Theme.accent)
                }.accessibilityIdentifier("settings.profiles")
            }
            Section {
                if model.usesManagedHosting {
                    NavigationLink(L10n.text("Managed hosting & model credits")) { ManagedAccountView() }
                    Text(L10n.text("The managed service address is fixed by this app."))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                NavigationLink { BackendEndpointsView() } label: {
                    DestinationLabel(title: L10n.text("Backend endpoints"), subtitle: model.backendAddress.isEmpty ? L10n.text("Connect to home-core") : model.backendAddress, symbol: "network", color: Theme.sage)
                }.accessibilityIdentifier("settings.backendEndpoints")
                }
                if let url = try? model.client().base {
                    Link(destination: url) { Label(L10n.text("Open active web app"), systemImage: "arrow.up.right.square") }
                }
            } header: { Text(L10n.text("Home-core")) } footer: {
                Text(L10n.text("Use an HTTPS LAN or private VPN address. You can keep an ordered fallback list for changing network contexts. HTTP is available only for trusted LAN/VPN use and is unencrypted."))
            }
            Section {
                NavigationLink { OmiConnectionsView() } label: {
                    DestinationLabel(title: L10n.text("Omi import & audio forwarding"), subtitle: L10n.text("Optional cloud transcripts & audio receiver"), symbol: "arrow.triangle.2.circlepath", color: Theme.accent)
                }.accessibilityIdentifier("settings.omiConnections")
            } header: { Text(L10n.text("Omi connections")) }
            saveFeedback
        }.omiScreen().navigationTitle(L10n.text("Connections & profiles")).navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder
    private var saveFeedback: some View {
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
            Picker(L10n.text("Keep original audio"), selection: $audioRetentionDays) {
                ForEach(retentionChoices(audioRetentionDays), id: \.self) { days in
                    Text(retentionLabel(days)).tag(days)
                }
            }.accessibilityIdentifier("settings.audioRetention")
            Text(L10n.text("Default: 7 days. Expired audio is removed automatically; transcripts have their own retention period."))
                .font(.caption).foregroundStyle(.secondary)
            Picker(L10n.text("Keep transcripts"), selection: $transcriptRetentionDays) {
                ForEach(retentionChoices(transcriptRetentionDays), id: \.self) { days in
                    Text(retentionLabel(days)).tag(days)
                }
            }.accessibilityIdentifier("settings.transcriptRetention")
            Text(L10n.text("Default: 30 days. Expiring transcripts also clears derived diary/chat content and related inbox items, including manual edits and history."))
                .font(.caption).foregroundStyle(.secondary)
            Button(L10n.text("Save archive retention")) { Task { await saveRetention() } }
                .accessibilityIdentifier("settings.saveRetention")
        } header: { Text(L10n.text("Local archive")) } footer: {
            Text(L10n.text("Saved on home-core and shared with the web app. Shorter retention periods apply to existing recordings when saved. Earlier backups keep their own copies."))
        }.disabled(loading || !retentionLoaded)
    }
    private func retentionChoices(_ current: Int) -> [Int] {
        Array(Set(retentionOptions + [current])).sorted()
    }
    private func retentionLabel(_ days: Int) -> String {
        days == 0 ? L10n.text("Until I remove it") : days == 1 ? L10n.text("\(days) day") : L10n.text("\(days) days")
    }
    private func loadSettings() async {
        error = nil; retentionLoaded = false; translationLanguagesLoaded = false
        do {
            let settings = try await model.client().request("v1/settings")
            guard !Task.isCancelled else { return }
            if settings["meeting_max_minutes"].integer > 0 {
                model.meetingMinutes = settings["meeting_max_minutes"].integer; timeout = model.meetingMinutes
            }
            if !settings["summary_language"].text.isEmpty { summaryLanguage = settings["summary_language"].text }
            if case .array = settings["translation_languages"] {
                translationLanguages = settings["translation_languages"].array.map(\.text)
                translationLanguagesLoaded = true
            }
            if case .number = settings["audio_retention_days"],
               case .number = settings["transcript_retention_days"] {
                audioRetentionDays = settings["audio_retention_days"].integer
                transcriptRetentionDays = settings["transcript_retention_days"].integer
                retentionLoaded = true
            }
        } catch { if !Task.isCancelled { self.error = L10n.error(error) } }
    }
    private func saveRetention() async {
        guard retentionLoaded else { return }
        loading = true; error = nil; status = nil; defer { loading = false }
        do {
            let settings = try await model.client().request("v1/settings", method: "PATCH", body: [
                "audio_retention_days": audioRetentionDays,
                "transcript_retention_days": transcriptRetentionDays,
            ])
            audioRetentionDays = settings["audio_retention_days"].integer
            transcriptRetentionDays = settings["transcript_retention_days"].integer
            status = L10n.text("Archive retention saved")
        } catch { self.error = L10n.error(error) }
    }
    private func savePolicy() async {
        loading = true; error = nil; defer { loading = false }
        do {
            _ = try await model.client().request("v1/settings", method: "PATCH", body: ["meeting_max_minutes": timeout])
            model.meetingMinutes = timeout
            status = L10n.text("Meeting timeout saved")
        } catch { self.error = L10n.error(error) }
    }
    private var summaryLanguageChoices: [String] {
        languageChoices(including: summaryLanguage == "auto" ? [] : [summaryLanguage])
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
    private func saveSummaryLanguage() async {
        loading = true; error = nil; status = nil; defer { loading = false }
        do {
            let settings = try await model.client().request("v1/settings", method: "PATCH", body: ["summary_language": summaryLanguage])
            summaryLanguage = settings["summary_language"].text
            status = L10n.text("Summary language saved")
        } catch { self.error = L10n.error(error) }
    }
}

private func languageChoices(including additional: [String] = []) -> [String] {
    let codes = ("aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy " +
            "da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz " +
            "ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv " +
            "mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt " +
            "qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr " +
            "ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu").split(separator: " ").map(String.init)
    return Set(codes + additional).sorted {
        (L10n.locale.localizedString(forLanguageCode: $0) ?? $0)
            .localizedStandardCompare(L10n.locale.localizedString(forLanguageCode: $1) ?? $1) == .orderedAscending
    }
}

struct TranslationLanguageSelectionView: View {
    @Binding var languages: [String]
    @State private var search = ""
    private var choices: [String] {
        languageChoices(including: languages).filter { code in
            search.isEmpty || code.localizedCaseInsensitiveContains(search) ||
                (L10n.locale.localizedString(forLanguageCode: code) ?? code).localizedCaseInsensitiveContains(search)
        }
    }
    @Environment(\.locale) private var interfaceLocale
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
                NavigationLink { ManagedAccountView() } label: {
                    DestinationLabel(title: L10n.text("Account & connection"), subtitle: model.managedAccount.signedIn ? L10n.text("Managed hosting & model credits") : L10n.text("Optional sign-in or your own backend"), symbol: "person.crop.circle", color: Theme.sage)
                }
            }
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
                Button { editor = ProfileEditor(profile: nil) } label: {
                    Label(L10n.text("Create profile"), systemImage: "person.badge.plus")
                }.accessibilityIdentifier("profiles.create")
            } footer: {
                Text(L10n.text("Each profile has its own recordings, diary and chat. Settings apply to every profile. Anyone using this server can select any profile."))
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

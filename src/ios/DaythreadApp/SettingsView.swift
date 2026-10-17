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
    var body: some View {
        List {
            Section {
                EditorialTitle(eyebrow: "Make it yours", title: "Your space.").editorialRow()
            }
            Section("Appearance") {
                Picker("Appearance", selection: $appearance) {
                    ForEach(AppAppearance.allCases) { option in Text(option.title).tag(option) }
                }.accessibilityIdentifier("settings.appearance")
                Picker("Interface language", selection: $interfaceLanguagePreference) {
                    Text("System language").tag("system")
                    Text("English").tag("en")
                    Text("Dansk").tag("da")
                }.accessibilityIdentifier("settings.interfaceLanguage")
                Text("Saved on this iPhone. This changes the interface, not transcription or summary languages.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            CompletionNotificationSettings()
            Section("Everyday preferences") {
                NavigationLink { captureSettings } label: {
                    DestinationLabel(title: "Capture & uploads", subtitle: "Wi-Fi, meeting gestures & your pendant", symbol: "waveform", color: Theme.sage)
                }.accessibilityIdentifier("settings.capture")
                NavigationLink { languageSettings } label: {
                    DestinationLabel(title: "Languages & summaries", subtitle: "Recap language & transcript translations", symbol: "character.bubble", color: Theme.accent)
                }.accessibilityIdentifier("settings.languages")
                NavigationLink { storageSettings } label: {
                    DestinationLabel(title: "Archive & phone storage", subtitle: "Retention periods & local capture budget", symbol: "archivebox", color: Theme.sage)
                }.accessibilityIdentifier("settings.storage")
            }
            Section("Your setup") {
                NavigationLink { ServerConfigurationView() } label: {
                    DestinationLabel(title: "Models & server", subtitle: "Speech, language models, keys & advanced options", symbol: "slider.horizontal.3", color: Theme.accent)
                }.accessibilityIdentifier("settings.server")
                NavigationLink { connectionSettings } label: {
                    DestinationLabel(title: "Connections & profiles", subtitle: "Home-core, your archives & Omi imports", symbol: "network", color: Theme.sage)
                }.accessibilityIdentifier("settings.connections")
            }
            saveFeedback
        }.listStyle(.insetGrouped).omiScreen()
            .navigationTitle("Settings").navigationBarTitleDisplayMode(.inline)
            .task { timeout = model.meetingMinutes; await loadSettings() }
    }

    private var captureSettings: some View {
        @Bindable var model = model
        return Form {
            saveFeedback
            Section {
                Toggle("Upload only on Wi-Fi", isOn: $model.uploadWiFiOnly)
                    .accessibilityIdentifier("settings.uploadWiFiOnly")
            } header: { Text("Uploads") } footer: {
                Text("Saved on this iPhone. When enabled, recordings, flags and capture gaps wait for Wi-Fi before uploading. Capture continues locally. Sync now follows this setting. Turn it off to allow cellular uploads.")
            }
            Section("Meeting hints") {
                Picker("Wearable gesture", selection: $model.gesture) {
                    Text("Double click · stock firmware").tag(MeetingGesture.doubleClick)
                    Text("Hold · verified firmware only").tag(MeetingGesture.verifiedHold)
                }
                Text("Stock Omi firmware uses a three-second hold to power off. Choose hold only after verifying that your firmware emits a resolved long-tap event.")
                    .font(.caption).foregroundStyle(.secondary)
                Stepper("Auto-stop after \(timeout) minutes", value: $timeout, in: 1...480, step: 5)
                Button("Save meeting timeout to home-core") { Task { await savePolicy() } }.disabled(loading)
                Text("Meetings shorter than one minute are ignored. Flags have a one-minute cooldown. Hints include the surrounding speech and do not define exact boundaries.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Section("Pendant information") {
                LabeledContent("Model", value: model.bluetooth.model)
                LabeledContent("Firmware", value: model.bluetooth.firmware)
                LabeledContent("Codec", value: model.bluetooth.codec.map(String.init) ?? "Unknown")
                LabeledContent("Button notifications", value: model.bluetooth.buttonAvailable ? "Available" : "Not discovered")
                if model.bluetooth.connected { Button("Disconnect and forget Omi", role: .destructive) { model.flush(); model.bluetooth.disconnect() } }
            }
        }.omiScreen().navigationTitle("Capture & uploads").navigationBarTitleDisplayMode(.inline)
            .task { if !retentionLoaded { await loadSettings() } }
    }

    private var languageSettings: some View {
        Form {
            saveFeedback
            Section {
                Picker("Summary language", selection: $summaryLanguage) {
                    Text("Automatic / follow source language").tag("auto")
                    ForEach(summaryLanguageChoices, id: \.self) { code in
                        Text(Locale.current.localizedString(forLanguageCode: code) ?? code).tag(code)
                    }
                }.accessibilityIdentifier("settings.summaryLanguage")
                Button("Save summary language") { Task { await saveSummaryLanguage() } }
                    .disabled(loading).accessibilityIdentifier("settings.saveSummaryLanguage")
            } header: { Text("Summaries") } footer: {
                Text("Choose the language for recaps and summaries. Automatic follows the source language.")
            }
            Section {
                NavigationLink {
                    TranslationLanguageSelectionView(languages: $translationLanguages)
                } label: {
                    LabeledContent("Allowed languages", value: "\(translationLanguages.count) selected")
                }.accessibilityIdentifier("settings.translationLanguages")
                Button("Save translation languages") { Task { await saveTranslationLanguages() } }
                    .accessibilityIdentifier("settings.saveTranslationLanguages")
            } header: { Text("Transcript translations") } footer: {
                Text("Choose the languages offered by the Translate button. The first selected language is the default. Translations are available for every summary language, including Automatic. Save an empty list to disable translation.")
            }.disabled(loading || !translationLanguagesLoaded)
            Section("Transcription") {
                Text("Transcription automatically keeps the languages spoken in each conversation.")
                    .font(.callout).foregroundStyle(.secondary)
                NavigationLink("Models, keys & all server settings") { ServerConfigurationView() }
            }
        }.omiScreen().navigationTitle("Languages & summaries").navigationBarTitleDisplayMode(.inline)
            .task { if !translationLanguagesLoaded { await loadSettings() } }
    }

    private var storageSettings: some View {
        @Bindable var model = model
        return Form {
            saveFeedback
            archiveSection
            Section("On this iPhone") {
                Picker("Phone capture budget", selection: $model.storageBudgetMB) {
                    Text("1 GB").tag(1000)
                    Text("2 GB").tag(2000)
                    Text("4 GB").tag(4000)
                    Text("8 GB").tag(8000)
                }
                Text("Original compressed frames and decoded WAV chunks are kept until home-core acknowledges them. Synced audio is eligible for removal from the phone after one hour; event history stays for replay. Lowering the budget preserves existing recordings and stops new capture when full.")
                    .font(.callout).foregroundStyle(.secondary)
                Text("Capture can continue without home-core. Summaries and chat use your backend's local models. Pendant recordings and button events while Bluetooth is disconnected require separate, verified device storage support.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }.omiScreen().navigationTitle("Archive & phone storage").navigationBarTitleDisplayMode(.inline)
            .task { if !retentionLoaded { await loadSettings() } }
    }

    private var connectionSettings: some View {
        Form {
            Section {
                NavigationLink { ProfilesSettingsView() } label: {
                    DestinationLabel(title: "Profiles", subtitle: model.hasSelectedProfile ? model.selectedProfileName : "Choose an archive to get started", symbol: "person.crop.circle", color: Theme.accent)
                }.accessibilityIdentifier("settings.profiles")
            }
            Section {
                NavigationLink { BackendEndpointsView() } label: {
                    DestinationLabel(title: "Backend endpoints", subtitle: model.backendAddress.isEmpty ? "Connect to home-core" : model.backendAddress, symbol: "network", color: Theme.sage)
                }.accessibilityIdentifier("settings.backendEndpoints")
                if let url = try? model.client().base {
                    Link(destination: url) { Label("Open active web app", systemImage: "arrow.up.right.square") }
                }
            } header: { Text("Home-core") } footer: {
                Text("Use an HTTPS LAN or private VPN address. You can keep an ordered fallback list for changing network contexts. HTTP is available only for trusted LAN/VPN use and is unencrypted.")
            }
            Section {
                NavigationLink { OmiConnectionsView() } label: {
                    DestinationLabel(title: "Omi import & audio forwarding", subtitle: "Optional cloud transcripts & audio receiver", symbol: "arrow.triangle.2.circlepath", color: Theme.accent)
                }.accessibilityIdentifier("settings.omiConnections")
            } header: { Text("Omi connections") }
            saveFeedback
        }.omiScreen().navigationTitle("Connections & profiles").navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder
    private var saveFeedback: some View {
        if status != nil || error != nil || loading {
            Section {
                if loading { ProgressView("Saving preferences…") }
                if let status { Label(status, systemImage: "checkmark.circle").font(.callout).foregroundStyle(Theme.sage) }
                ErrorNote(message: error)
                if error != nil { Button("Reload preferences") { Task { await loadSettings() } }.disabled(loading) }
            }
        }
    }
    private var archiveSection: some View {
        Section {
            Picker("Keep original audio", selection: $audioRetentionDays) {
                ForEach(retentionChoices(audioRetentionDays), id: \.self) { days in
                    Text(retentionLabel(days)).tag(days)
                }
            }.accessibilityIdentifier("settings.audioRetention")
            Text("Default: 7 days. Expired audio is removed automatically; transcripts have their own retention period.")
                .font(.caption).foregroundStyle(.secondary)
            Picker("Keep transcripts", selection: $transcriptRetentionDays) {
                ForEach(retentionChoices(transcriptRetentionDays), id: \.self) { days in
                    Text(retentionLabel(days)).tag(days)
                }
            }.accessibilityIdentifier("settings.transcriptRetention")
            Text("Default: 30 days. Expiring transcripts also clears derived diary/chat content and related inbox items, including manual edits and history.")
                .font(.caption).foregroundStyle(.secondary)
            Button("Save archive retention") { Task { await saveRetention() } }
                .accessibilityIdentifier("settings.saveRetention")
        } header: { Text("Local archive") } footer: {
            Text("Saved on home-core and shared with the web app. Shorter retention periods apply to existing recordings when saved. Earlier backups keep their own copies.")
        }.disabled(loading || !retentionLoaded)
    }
    private func retentionChoices(_ current: Int) -> [Int] {
        Array(Set(retentionOptions + [current])).sorted()
    }
    private func retentionLabel(_ days: Int) -> String {
        days == 0 ? "Until I remove it" : "\(days) \(days == 1 ? "day" : "days")"
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
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
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
            status = "Archive retention saved"
        } catch { self.error = error.localizedDescription }
    }
    private func savePolicy() async {
        loading = true; error = nil; defer { loading = false }
        do {
            _ = try await model.client().request("v1/settings", method: "PATCH", body: ["meeting_max_minutes": timeout])
            model.meetingMinutes = timeout
            status = "Meeting timeout saved"
        } catch { self.error = error.localizedDescription }
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
            status = "Translation languages saved"
        } catch { self.error = error.localizedDescription }
    }
    private func saveSummaryLanguage() async {
        loading = true; error = nil; status = nil; defer { loading = false }
        do {
            let settings = try await model.client().request("v1/settings", method: "PATCH", body: ["summary_language": summaryLanguage])
            summaryLanguage = settings["summary_language"].text
            status = "Summary language saved"
        } catch { self.error = error.localizedDescription }
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
        (Locale.current.localizedString(forLanguageCode: $0) ?? $0)
            .localizedStandardCompare(Locale.current.localizedString(forLanguageCode: $1) ?? $1) == .orderedAscending
    }
}

struct TranslationLanguageSelectionView: View {
    @Binding var languages: [String]
    @State private var search = ""
    private var choices: [String] {
        languageChoices(including: languages).filter { code in
            search.isEmpty || code.localizedCaseInsensitiveContains(search) ||
                (Locale.current.localizedString(forLanguageCode: code) ?? code).localizedCaseInsensitiveContains(search)
        }
    }
    var body: some View {
        List {
            if let first = languages.first {
                Text("Default: \(Locale.current.localizedString(forLanguageCode: first) ?? first)")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(choices, id: \.self) { code in
                Toggle(Locale.current.localizedString(forLanguageCode: code) ?? code, isOn: Binding(
                    get: { languages.contains(code) },
                    set: { enabled in
                        if enabled && !languages.contains(code) { languages.append(code) }
                        else if !enabled { languages.removeAll { $0 == code } }
                    }
                )).accessibilityIdentifier("settings.translationLanguage.\(code)")
            }
        }.navigationTitle("Translation languages")
            .searchable(text: $search, prompt: "Find a language")
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

    var body: some View {
        Form {
            Section {
                Picker("Active profile", selection: Binding(get: { model.selectedProfileID }, set: { id in
                    model.selectProfile(model.profiles.first { $0.id == id })
                })) {
                    Text("Choose a profile…").tag("")
                    ForEach(model.profiles) { profile in Text(profile.name).tag(profile.id) }
                }.accessibilityIdentifier("profiles.select")
                if !model.hasSelectedProfile {
                    Text("Choose a profile to open recordings, diary and other pages. All settings remain available.")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            Section {
                ForEach(model.profiles) { profile in
                    HStack(spacing: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(profile.name).font(.body.weight(.medium))
                            if profile.id == model.selectedProfileID {
                                Text("Current profile").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Button("Edit") { editor = ProfileEditor(profile: profile) }
                            .accessibilityLabel("Edit " + profile.name)
                    }.padding(.vertical, 4)
                }
                Button { editor = ProfileEditor(profile: nil) } label: {
                    Label("Create profile", systemImage: "person.badge.plus")
                }.accessibilityIdentifier("profiles.create")
            } footer: {
                Text("Each profile has its own recordings, diary and chat. Settings apply to every profile. Anyone using this server can select any profile.")
            }
            if loading { ProgressView("Loading profiles…") }
            if let error {
                Section {
                    ErrorNote(message: error)
                    Button("Try again") { Task { await load() } }
                }
            }
        }.omiScreen().navigationTitle("Profiles").navigationBarTitleDisplayMode(.inline)
            .task { await load() }
            .sheet(item: $editor) { selection in ProfileEditorView(profile: selection.profile) }
    }

    private func load() async {
        loading = true; error = nil
        defer { loading = false }
        do { try await model.loadProfiles() }
        catch { self.error = error.localizedDescription }
    }
}

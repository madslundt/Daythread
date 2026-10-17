import SwiftUI
import OmiCore

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("appearancePreference") private var appearance = AppAppearance.system
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
        @Bindable var model = model
        Form {
            Section {
                EditorialTitle(eyebrow: "Make it yours", title: "Your space.")
                    .editorialRow()
            }
            Section {
                NavigationLink(value: SettingsDestination.profiles) {
                    Label("Profiles", systemImage: "person.crop.circle")
                }.accessibilityIdentifier("settings.profiles")
            }
            Section {
                NavigationLink { BackendEndpointsView() } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Label("Backend endpoints", systemImage: "network")
                        Text(model.backendAddress.isEmpty ? "Not configured" : model.backendAddress)
                            .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }.accessibilityIdentifier("settings.backendEndpoints")
                if let url = try? model.client().base { Link(destination: url) { Label("Open active web app", systemImage: "arrow.up.right.square") } }
                if let status { Label(status, systemImage: "checkmark.circle").font(.callout).foregroundStyle(.secondary) }
                ErrorNote(message: error)
            } header: { Text("Home-core") } footer: { Text("Use an HTTPS LAN or private VPN address. You can keep an ordered fallback list for changing network contexts. HTTP is available only for trusted LAN/VPN use and is unencrypted.") }
            Section("Appearance") {
                Picker("Appearance", selection: $appearance) {
                    ForEach(AppAppearance.allCases) { option in
                        Text(option.title).tag(option)
                    }
                }.accessibilityIdentifier("settings.appearance")
            }
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
            Section("Transcription") {
                Text("Transcription automatically keeps the languages spoken in each conversation.")
                    .font(.callout).foregroundStyle(.secondary)
                NavigationLink("Models, keys & all server settings") { ServerConfigurationView() }
            }
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
            Section("Pendant information") {
                LabeledContent("Model", value: model.bluetooth.model)
                LabeledContent("Firmware", value: model.bluetooth.firmware)
                LabeledContent("Codec", value: model.bluetooth.codec.map(String.init) ?? "Unknown")
                LabeledContent("Button notifications", value: model.bluetooth.buttonAvailable ? "Available" : "Not discovered")
                if model.bluetooth.connected { Button("Disconnect and forget Omi", role: .destructive) { model.flush(); model.bluetooth.disconnect() } }
            }
            archiveSection
            Section("Local storage") {
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
        }.omiScreen().navigationTitle("Settings").navigationBarTitleDisplayMode(.inline)
            .scrollDismissesKeyboard(.interactively)
            .task { timeout = model.meetingMinutes; await loadSettings() }
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
        retentionLoaded = false; translationLanguagesLoaded = false
        do {
            let settings = try await model.client().request("v1/settings")
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
        } catch { self.error = error.localizedDescription }
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

private struct BackendEndpointDraft: Identifiable {
    let id: UUID
    let endpointID: UUID?
    var address: String
}

struct BackendEndpointsView: View {
    @Environment(AppModel.self) private var model
    @State private var draft: BackendEndpointDraft?
    @State private var testing: UUID?
    @State private var results: [UUID: String] = [:]
    @State private var error: String?

    var body: some View {
        List {
            Section {
                ForEach(model.backendEndpoints) { endpoint in
                    BackendEndpointRow(
                        endpoint: endpoint,
                        isActive: model.backendAddress == endpoint.address,
                        isTesting: testing == endpoint.id,
                        result: results[endpoint.id],
                        activate: { model.activateEndpoint(endpoint.id) },
                        test: { test(endpoint) },
                        edit: { draft = BackendEndpointDraft(id: endpoint.id, endpointID: endpoint.id, address: endpoint.address) },
                        remove: { remove(endpoint) }
                    )
                }
                .onMove { model.moveEndpoints(from: $0, to: $1) }
            } header: {
                Text("Ordered fallback list")
            } footer: {
                Text("Requests start at the active endpoint and try each remaining endpoint once only when the failure looks like DNS, reachability, TLS or a 502/503/504 gateway failure. A successful endpoint becomes active for recovery and future uploads.")
            }
            ErrorNote(message: error)
        }
        .omiScreen()
        .navigationTitle("Backend endpoints")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) { EditButton() }
            ToolbarItem(placement: .topBarTrailing) {
                Button { draft = BackendEndpointDraft(id: UUID(), endpointID: nil, address: "") } label: { Label("Add endpoint", systemImage: "plus") }
            }
        }
        .sheet(item: $draft) { value in
            BackendEndpointEditor(draft: value) { saved in
                do {
                    try model.saveEndpoint(address: saved.address, replacing: saved.endpointID)
                    draft = nil
                    error = nil
                } catch let failure { self.error = failure.localizedDescription }
            }
        }
    }

    private func test(_ endpoint: BackendEndpoint) {
        testing = endpoint.id; results[endpoint.id] = nil; error = nil
        Task {
            do {
                try await model.testEndpoint(endpoint)
                results[endpoint.id] = "Reachable"
            } catch let failure {
                results[endpoint.id] = "Failed: \(failure.localizedDescription)"
            }
            testing = nil
        }
    }

    private func remove(_ endpoint: BackendEndpoint) {
        do {
            try model.removeEndpoint(endpoint.id)
            results.removeValue(forKey: endpoint.id)
        } catch let failure { error = failure.localizedDescription }
    }
}

private struct BackendEndpointRow: View {
    let endpoint: BackendEndpoint
    let isActive: Bool
    let isTesting: Bool
    let result: String?
    let activate: () -> Void
    let test: () -> Void
    let edit: () -> Void
    let remove: () -> Void

    private var isHTTP: Bool { URL(string: endpoint.address)?.scheme?.lowercased() == "http" }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(action: activate) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: isActive ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(isActive ? Theme.accent : .secondary)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(endpoint.address).font(.body.monospaced()).textSelection(.enabled)
                        Text(isActive ? "Active endpoint" : "Tap to use this endpoint")
                            .font(.caption).foregroundStyle(.secondary)
                        if isHTTP { Label("HTTP is unencrypted; use only on a trusted LAN/VPN.", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange) }
                        if let result { Text(result).font(.caption).foregroundStyle(result == "Reachable" ? .green : .red) }
                    }
                    Spacer(minLength: 0)
                }
            }.buttonStyle(.plain)
            HStack {
                Button(isTesting ? "Testing…" : "Test connection", action: test).disabled(isTesting)
                Button("Edit", action: edit)
                Button("Remove", role: .destructive, action: remove)
            }.font(.subheadline)
        }.padding(.vertical, 4)
    }
}

private struct BackendEndpointEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State private var draft: BackendEndpointDraft
    let save: (BackendEndpointDraft) -> Void

    init(draft: BackendEndpointDraft, save: @escaping (BackendEndpointDraft) -> Void) {
        _draft = State(initialValue: draft)
        self.save = save
    }

    private var isHTTP: Bool { URL(string: draft.address.trimmingCharacters(in: .whitespacesAndNewlines))?.scheme?.lowercased() == "http" }

    var body: some View {
        NavigationStack {
            Form {
                Section("Endpoint URL") {
                    TextField("https://daythread.home.arpa", text: $draft.address)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                        .accessibilityLabel("Backend endpoint URL")
                    Text("Include the scheme and port when needed. Do not include credentials, a query or a fragment.")
                        .font(.caption).foregroundStyle(.secondary)
                    if isHTTP {
                        Label("HTTP is unencrypted. Use this only for a trusted home LAN or private VPN; HTTPS remains the recommended choice.", systemImage: "exclamationmark.triangle")
                            .font(.callout).foregroundStyle(.orange)
                    }
                }
            }
            .navigationTitle(draft.endpointID == nil ? "Add endpoint" : "Edit endpoint")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save(draft); dismiss() }
                        .disabled(draft.address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
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

private struct TranslationLanguageSelectionView: View {
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

private struct ServerSettingField: Decodable, Identifiable {
    let key: String
    let label: String
    let group: String
    let kind: String
    let nullable: Bool
    let restart_required: Bool
    let description: String
    var id: String { key }
}

private struct ServerConfiguration: Decodable {
    let fields: [ServerSettingField]
    let values: [String: JSONValue]
    let secrets: [String: Bool]
    let revision: String
    let pending_restart: [String]
}

private struct ServerSecretEdit {
    var mode = "keep"
    var value = ""
    func change() throws -> JSONValue? {
        switch mode {
        case "replace":
            guard !value.isEmpty else { throw BackendFailure(message: "Enter the replacement key or choose another key action.") }
            return .string(value)
        case "clear": return .string("")
        case "inherit": return .null
        default: return nil
        }
    }
}

private struct ServerModelRoute: Identifiable {
    let id = UUID()
    var name = ""
    var model = ""
    var endpoint = ""
    var revision = ""
    var secret = ServerSecretEdit()
}

private struct ServerSecretControl: View {
    let label: String
    let configured: Bool
    var route = false
    var nullable = false
    @Binding var edit: ServerSecretEdit
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\(label) · \(configured ? "Configured" : "Not set")").font(.subheadline)
            Picker(label, selection: $edit.mode) {
                Text("Keep current key").tag("keep")
                Text("Replace key").tag("replace")
                if route || nullable { Text(route ? "Use provider key" : "Use shared key").tag("inherit") }
                Text(route || nullable ? "No authentication" : "Clear key").tag("clear")
            }.onChange(of: edit.mode) { _, _ in edit.value = "" }
            if edit.mode == "replace" {
                SecureField("New \(label)", text: $edit.value)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            }
        }
    }
}

private struct ServerRouteControl: View {
    @Binding var route: ServerModelRoute
    let llm: Bool
    let configured: Bool
    let remove: () -> Void
    private let tasks = ["chat", "summary", "extraction", "preparation", "weekly", "retrieval"]
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if llm {
                Picker("Task", selection: $route.name) {
                    ForEach(tasks, id: \.self) { Text($0).tag($0) }
                }
            } else {
                TextField("Language code or name (da, en, danish, auto)", text: $route.name)
                    .accessibilityLabel("Language code or detector name")
            }
            TextField("Model override (blank inherits default)", text: $route.model)
                .accessibilityLabel("Model override")
            TextField("Endpoint override (blank inherits default)", text: $route.endpoint)
                .keyboardType(.URL).accessibilityLabel("Endpoint override")
            if llm { TextField("Revision override (blank inherits default)", text: $route.revision) }
            ServerSecretControl(label: "Route key", configured: configured, route: true, edit: $route.secret)
            Button("Remove route \(route.name)", role: .destructive, action: remove)
        }.textInputAutocapitalization(.never).autocorrectionDisabled().padding(.vertical, 8)
    }
}

@MainActor
private struct ServerConfigurationView: View {
    @Environment(AppModel.self) private var model
    @State private var snapshot: ServerConfiguration?
    @State private var values: [String: JSONValue] = [:]
    @State private var routes: [String: [ServerModelRoute]] = [:]
    @State private var secrets: [String: ServerSecretEdit] = [:]
    @State private var busy = false
    @State private var error: String?
    @State private var status: String?
    @State private var search = ""
    private let tasks = ["chat", "summary", "extraction", "preparation", "weekly", "retrieval"]
    private let connectionProviders = ["stt", "llm"]

    private func connectionKeys(_ provider: String) -> [String] {
        ["\(provider)_base_url", "\(provider)_api_key", "\(provider)_model"]
    }
    private func isConnectionField(_ field: ServerSettingField) -> Bool {
        connectionProviders.contains { connectionKeys($0).contains(field.key) }
    }
    private func connectionFields(_ provider: String, _ snapshot: ServerConfiguration) -> [ServerSettingField] {
        connectionKeys(provider).compactMap { key in snapshot.fields.first { $0.key == key && matches($0) } }
    }
    private func optionsTitle(_ group: String) -> String {
        switch group {
        case "Transcription": "Transcription options & language routes"
        case "Language models & chat": "Language model options & task routes"
        default: group
        }
    }

    var body: some View {
        Form {
            Section {
                Text("STT and LLM connections are independent. Each can use a different endpoint, API key, and model.")
                    .font(.callout).foregroundStyle(.secondary)
                Text("Shared with the web app. Keys are write-only and stay on home-core.")
                    .font(.callout).foregroundStyle(.secondary)
                if let snapshot, !snapshot.pending_restart.isEmpty {
                    Text("Saved changes awaiting backend restart: \(snapshot.pending_restart.joined(separator: ", ")).")
                        .font(.callout)
                }
                ErrorNote(message: error)
                if let status { Text(status).foregroundStyle(.secondary) }
                if busy { ProgressView("Updating settings…") }
                Button("Reload server settings") { Task { await load() } }.disabled(busy)
            }
            if let snapshot {
                ForEach(connectionProviders, id: \.self) { provider in
                    let fields = connectionFields(provider, snapshot)
                    if !fields.isEmpty {
                        Section {
                            ForEach(fields) { field in setting(field, snapshot: snapshot) }
                        } header: {
                            Text(provider == "stt" ? "Speech-to-text (STT)" : "Language model (LLM)")
                        } footer: {
                            Text(provider == "stt" ? "Transcribes your recordings into text. Enter a local or hosted endpoint and its credentials." : "Answers questions and generates summaries. Enter a local or hosted endpoint and its credentials.")
                        }.disabled(busy)
                    }
                }
                ForEach(groups(snapshot), id: \.self) { group in
                    Section(optionsTitle(group)) {
                        ForEach(snapshot.fields.filter { $0.group == group && !isConnectionField($0) && matches($0) }) { field in
                            setting(field, snapshot: snapshot)
                        }
                    }.disabled(busy)
                }
                Section {
                    Button("Save server settings") { Task { await save(snapshot) } }
                        .disabled(busy).accessibilityIdentifier("settings.saveServer")
                    Text("Settings marked restart required take effect the next time home-core starts.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }.navigationTitle("Server settings").navigationBarTitleDisplayMode(.inline)
            .searchable(text: $search, prompt: "Find a server setting")
            .scrollDismissesKeyboard(.interactively).task { await load() }
    }

    private func groups(_ snapshot: ServerConfiguration) -> [String] {
        snapshot.fields.filter { !isConnectionField($0) && matches($0) }.reduce(into: [String]()) { result, field in
            if !result.contains(field.group) { result.append(field.group) }
        }
    }
    private func matches(_ field: ServerSettingField) -> Bool {
        search.isEmpty || "\(field.label) \(field.key) \(field.description)".localizedCaseInsensitiveContains(search)
    }
    private func valueBinding(_ key: String) -> Binding<String> {
        Binding(get: {
            switch values[key] {
            case .string(let text): return text
            case .number(let number): return String(format: "%g", number)
            default: return ""
            }
        }, set: { values[key] = .string($0); status = nil })
    }
    @ViewBuilder
    private func setting(_ field: ServerSettingField, snapshot: ServerConfiguration) -> some View {
        let title = field.label + (field.restart_required ? " · restart required" : "")
        if field.key == "translation_languages" {
            NavigationLink {
                TranslationLanguageSelectionView(languages: Binding(
                    get: { values[field.key]?.array.map(\.text) ?? [] },
                    set: { values[field.key] = .array($0.map(JSONValue.string)); status = nil }
                ))
            } label: {
                LabeledContent(title, value: "\(values[field.key]?.array.count ?? 0) selected")
            }
        } else if field.kind == "routes" {
            Text(field.label).font(.headline)
            ForEach(routes[field.key] ?? []) { row in
                ServerRouteControl(route: Binding(get: {
                    routes[field.key]?.first(where: { $0.id == row.id }) ?? row
                }, set: { updated in
                    routes[field.key] = routes[field.key]?.map { $0.id == row.id ? updated : $0 }
                    status = nil
                }), llm: field.key == "llm_routes", configured: snapshot.secrets["\(field.key).\(row.name.lowercased()).token"] ?? false) {
                    routes[field.key]?.removeAll { $0.id == row.id }; status = nil
                }
            }
            Button(field.key == "stt_routes" ? "Add language route" : "Add task route") {
                let name = field.key == "llm_routes" ? tasks.first(where: { task in
                    !(routes[field.key] ?? []).contains(where: { $0.name == task })
                }) ?? "chat" : ""
                routes[field.key, default: []].append(ServerModelRoute(name: name)); status = nil
            }.disabled(field.key == "llm_routes" && (routes[field.key]?.count ?? 0) >= tasks.count)
        } else if field.kind == "secret" {
            ServerSecretControl(label: field.label, configured: snapshot.secrets[field.key] ?? false,
                nullable: field.nullable,
                edit: Binding(get: { secrets[field.key] ?? ServerSecretEdit() }, set: { secrets[field.key] = $0; status = nil }))
        } else if field.kind == "boolean" {
            if field.nullable {
                Picker(title, selection: Binding(get: {
                    if case .bool(let value) = values[field.key] { return value ? "true" : "false" }
                    return "default"
                }, set: { values[field.key] = $0 == "default" ? .null : .bool($0 == "true"); status = nil })) {
                    Text("Model default").tag("default"); Text("Enabled").tag("true"); Text("Disabled").tag("false")
                }
            } else {
                Toggle(title, isOn: Binding(get: { values[field.key] == .bool(true) }, set: { values[field.key] = .bool($0); status = nil }))
            }
        } else {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.subheadline)
                TextField(field.nullable ? "Default" : field.label, text: valueBinding(field.key))
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .keyboardType(field.kind == "number" || field.kind == "integer" ? .numbersAndPunctuation : field.key.contains("base_url") ? .URL : .default)
                    .accessibilityLabel(title)
            }
        }
        if !field.description.isEmpty {
            Text(field.description).font(.caption).foregroundStyle(.secondary)
        }
    }

    private func apply(_ result: ServerConfiguration) {
        snapshot = result; values = result.values; secrets = [:]; routes = [:]
        for field in result.fields where field.kind == "routes" {
            if case .object(let map) = result.values[field.key] {
                routes[field.key] = map.keys.sorted().map { name in
                    let route = map[name] ?? .null
                    return ServerModelRoute(name: name, model: route["model"].text, endpoint: route["base_url"].text, revision: route["revision"].text)
                }
            }
        }
    }
    private func load() async {
        busy = true; error = nil; status = nil; defer { busy = false }
        do { apply(try await model.client().decoded("v1/configuration", as: ServerConfiguration.self)) }
        catch is CancellationError { return }
        catch { self.error = error.localizedDescription }
    }
    private func save(_ snapshot: ServerConfiguration) async {
        busy = true; error = nil; status = nil; defer { busy = false }
        do {
            var changes: [String: JSONValue] = [:]
            for field in snapshot.fields {
                var value = values[field.key] ?? .null
                if field.kind == "secret" {
                    guard let edited = try (secrets[field.key] ?? ServerSecretEdit()).change() else { continue }
                    changes[field.key] = edited
                    continue
                } else if field.kind == "routes" {
                    var map: [String: JSONValue] = [:]
                    for row in routes[field.key] ?? [] {
                        let name = row.name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
                        guard name.range(of: "^[a-z-]{2,32}$", options: .regularExpression) != nil,
                              map[name] == nil else { throw BackendFailure(message: "Enter a unique language code or task for every route.") }
                        var route: [String: JSONValue] = ["model": row.model.isEmpty ? .null : .string(row.model), "base_url": row.endpoint.isEmpty ? .null : .string(row.endpoint)]
                        if field.key == "llm_routes" { route["revision"] = row.revision.isEmpty ? .null : .string(row.revision) }
                        if let token = try row.secret.change() { route["token"] = token }
                        map[name] = .object(route)
                    }
                    value = .object(map)
                } else if field.kind == "integer" || field.kind == "number" {
                    if case .string(let text) = value {
                        guard let number = Double(text), number.isFinite else { throw BackendFailure(message: "Enter a number for \(field.label).") }
                        value = .number(number)
                    }
                } else if field.nullable && value == .string("") { value = .null }
                if value != snapshot.values[field.key] { changes[field.key] = value }
            }
            if changes.isEmpty { status = "Server settings are up to date"; return }
            let payload = try JSONSerialization.jsonObject(with: JSONEncoder().encode(JSONValue.object(changes)))
            let result = try await model.client().decoded("v1/configuration", method: "PATCH", body: ["revision": snapshot.revision, "changes": payload], as: ServerConfiguration.self)
            apply(result); status = "Server settings saved on home-core"
            await model.refreshStatus()
        } catch { self.error = error.localizedDescription }
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

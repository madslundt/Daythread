import SwiftUI
import OmiCore

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("appearancePreference") private var appearance = AppAppearance.system
    @State private var address = ""
    @State private var error: String?
    @State private var status: String?
    @State private var loading = false
    @State private var language = "auto"
    @State private var timeout = 120
    @State private var audioRetentionDays = 7
    @State private var transcriptRetentionDays = 30
    @State private var retentionLoaded = false
    private let retentionOptions = [0, 1, 7, 14, 30, 90, 180, 365, 730, 1825]
    var body: some View {
        @Bindable var model = model
        Form {
            Section {
                EditorialTitle(eyebrow: "Make it yours", title: "Your space.", subtitle: "Your connection, preferences, and recordings.")
                    .editorialRow()
            }
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Backend address").font(.subheadline.weight(.medium))
                    TextField("http://home-core.local:8787", text: $address)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                        .accessibilityLabel("Backend address").accessibilityIdentifier("settings.backend")
                }.padding(.vertical, 4)
                Button { Task { await saveConnection() } } label: { Label("Save & test connection", systemImage: "network") }
                    .buttonStyle(PrimaryActionStyle()).disabled(loading)
                if loading { ProgressView("Connecting…") }
                if let status { Label(status, systemImage: "checkmark.circle").font(.callout).foregroundStyle(.secondary) }
                ErrorNote(message: error)
                if let url = try? model.client().base { Link(destination: url) { Label("Open web app", systemImage: "arrow.up.right.square") } }
            } header: { Text("Home-core") } footer: { Text("Use your home-core LAN address or private VPN. No account or authentication is required.") }
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
                Picker("Language", selection: $language) {
                    Text("Automatic / mixed").tag("auto")
                    Text("Danish").tag("da")
                    Text("English").tag("en")
                }
                Button("Save transcription language") { Task { await saveLanguage() } }.disabled(loading)
                NavigationLink("Models, keys & all server settings") { ServerConfigurationView() }
            }
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
            .task { address = model.backendAddress; timeout = model.meetingMinutes; await loadSettings() }
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
    private func saveConnection() async {
        error = nil; status = nil; loading = true; defer { loading = false }
        do {
            let client = try BackendClient(address: address)
            model.backendAddress = client.base.absoluteString; model.reconnectBackend()
            _ = try await client.request("v1/status"); status = "Connected to home-core"
            await loadSettings(); await model.refreshStatus()
        } catch { self.error = error.localizedDescription }
    }
    private func loadSettings() async {
        retentionLoaded = false
        do {
            let settings = try await model.client().request("v1/settings")
            if settings["meeting_max_minutes"].integer > 0 {
                model.meetingMinutes = settings["meeting_max_minutes"].integer; timeout = model.meetingMinutes
            }
            if !settings["language"].text.isEmpty { language = settings["language"].text }
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
    private func saveLanguage() async {
        loading = true; error = nil; defer { loading = false }
        do { _ = try await model.client().request("v1/settings", method: "PATCH", body: ["language": language]); status = "Transcription language saved" }
        catch { self.error = error.localizedDescription }
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
    @Binding var edit: ServerSecretEdit
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\(label) · \(configured ? "Configured" : "Not set")").font(.subheadline)
            Picker(label, selection: $edit.mode) {
                Text("Keep current key").tag("keep")
                Text("Replace key").tag("replace")
                if route { Text("Use shared key").tag("inherit") }
                Text(route ? "No authentication" : "Clear key").tag("clear")
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

    var body: some View {
        Form {
            Section {
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
                ForEach(groups(snapshot), id: \.self) { group in
                    Section(group) {
                        ForEach(snapshot.fields.filter { $0.group == group && matches($0) }) { field in
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
        snapshot.fields.filter(matches).reduce(into: [String]()) { result, field in
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
        if field.kind == "routes" {
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
                    value = edited
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

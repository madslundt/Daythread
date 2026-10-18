import SwiftUI

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

private struct ServerSecretEdit: Equatable {
    var mode = "keep"
    var value = ""
    func change() throws -> JSONValue? {
        switch mode {
        case "replace":
            guard !value.isEmpty else { throw BackendFailure(message: L10n.text("Enter the replacement key or choose another key action.")) }
            return .string(value)
        case "clear": return .string("")
        case "inherit": return .null
        default: return nil
        }
    }
}

private struct ServerModelRoute: Identifiable, Equatable {
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 8) {
            Text("\(label) · \(configured ? L10n.text("Configured") : L10n.text("Not set"))").font(.subheadline)
            Picker(label, selection: $edit.mode) {
                Text(L10n.text("Keep current key")).tag("keep")
                Text(L10n.text("Replace key")).tag("replace")
                if route || nullable { Text(route ? L10n.text("Use provider key") : L10n.text("Use shared key")).tag("inherit") }
                Text(route || nullable ? L10n.text("No authentication") : L10n.text("Clear key")).tag("clear")
            }.onChange(of: edit.mode) { _, _ in edit.value = "" }
            if edit.mode == "replace" {
                SecureField(L10n.text("New \(label)"), text: $edit.value)
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            if llm {
                Picker(L10n.text("Task"), selection: $route.name) {
                    ForEach(tasks, id: \.self) { Text($0).tag($0) }
                }
            } else {
                TextField(L10n.text("Language code or name (da, en, danish, auto)"), text: $route.name)
                    .accessibilityLabel(L10n.text("Language code or detector name"))
            }
            TextField(L10n.text("Model override (blank inherits default)"), text: $route.model)
                .accessibilityLabel(L10n.text("Model override"))
            TextField(L10n.text("Endpoint override (blank inherits default)"), text: $route.endpoint)
                .keyboardType(.URL).accessibilityLabel(L10n.text("Endpoint override"))
            if llm { TextField(L10n.text("Revision override (blank inherits default)"), text: $route.revision) }
            ServerSecretControl(label: L10n.text("Route key"), configured: configured, route: true, edit: $route.secret)
            Button(L10n.text("Remove route \(route.name)"), role: .destructive, action: remove)
        }.textInputAutocapitalization(.never).autocorrectionDisabled().padding(.vertical, 8)
    }
}

@MainActor
struct ServerConfigurationView: View {
    @Environment(AppModel.self) private var model
    @State private var snapshot: ServerConfiguration?
    @State private var values: [String: JSONValue] = [:]
    @State private var routes: [String: [ServerModelRoute]] = [:]
    @State private var secrets: [String: ServerSecretEdit] = [:]
    @State private var busy = false
    @State private var error: String?
    @State private var status: String?
    @State private var search = ""
    @State private var connectionRevision = UUID()
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
        case L10n.text("Transcription"): L10n.text("Transcription options & language routes")
        case L10n.text("Language models & chat"): L10n.text("Language model options & task routes")
        default: group
        }
    }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        Form {
            Section {
                Text(L10n.text("Transcription and summaries can use different providers. Each has its own address, API key and model."))
                    .font(.callout).foregroundStyle(.secondary)
                Text(L10n.text("Shared with the web app. API keys are stored on your server and cannot be read back."))
                    .font(.callout).foregroundStyle(.secondary)
                if let snapshot, !snapshot.pending_restart.isEmpty {
                    Text(L10n.text("Saved changes awaiting backend restart: \(snapshot.pending_restart.joined(separator: ", "))."))
                        .font(.callout)
                }
                ErrorNote(message: error)
                if let status { Text(L10n.key(status)).foregroundStyle(.secondary) }
                if busy { ProgressView(L10n.text("Updating settings…")) }
                Button(L10n.text("Reload server settings")) { Task { await load() } }.disabled(busy)
            }
            if let snapshot {
                ForEach(connectionProviders, id: \.self) { provider in
                    let fields = connectionFields(provider, snapshot)
                    if !fields.isEmpty {
                        Section {
                            ForEach(fields) { field in setting(field, snapshot: snapshot) }
                            ServerModelConnectionView(provider: provider, modelName: valueBinding(provider + "_model"),
                                                      revision: connectionRevision, inheritedModel: "") { includeModel in
                                try connectionRequest(provider: provider, includeModel: includeModel)
                            }
                        } header: {
                            Text(provider == "stt" ? L10n.text("Transcription") : L10n.text("Summaries & chat"))
                        } footer: {
                            Text(provider == "stt" ? L10n.text("Transcribes your recordings into text. Enter a local or hosted endpoint and its credentials.") : L10n.text("Answers questions and generates summaries. Enter a local or hosted endpoint and its credentials."))
                        }.disabled(busy)
                    }
                }
                ForEach(groups(snapshot), id: \.self) { group in
                    Section(L10n.key(optionsTitle(group))) {
                        ForEach(snapshot.fields.filter { $0.group == group && !isConnectionField($0) && matches($0) }) { field in
                            setting(field, snapshot: snapshot)
                        }
                    }.disabled(busy)
                }
                Section {
                    Button(L10n.text("Save server settings")) { Task { await save(snapshot) } }
                        .disabled(busy).accessibilityIdentifier("settings.saveServer")
                    Text(L10n.text("Settings marked restart required take effect the next time your server starts."))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }.navigationTitle(L10n.text("Server settings")).navigationBarTitleDisplayMode(.inline)
            .searchable(text: $search, prompt: L10n.text("Find a server setting"))
            .scrollDismissesKeyboard(.interactively).task { await load() }
            .onChange(of: values) { _, _ in connectionRevision = UUID() }
            .onChange(of: secrets) { _, _ in connectionRevision = UUID() }
            .onChange(of: routes) { _, _ in connectionRevision = UUID() }
            .onChange(of: snapshot?.revision) { _, _ in connectionRevision = UUID() }
    }

    private func groups(_ snapshot: ServerConfiguration) -> [String] {
        snapshot.fields.filter { !isConnectionField($0) && matches($0) }.reduce(into: [String]()) { result, field in
            if !result.contains(field.group) { result.append(field.group) }
        }
    }
    // New backend fields require an explicit product decision before appearing here.
    // Import and forwarding credentials are managed in the native connections page.
    private let advancedKeys: Set<String> = [
        "stt_base_url", "stt_api_key", "stt_model", "stt_routes", "stt_chunk_seconds", "stt_filter_speech",
        "stt_enhancement_mode", "stt_enhancement_timeout_seconds", "stt_segment_timestamps",
        "llm_base_url", "llm_api_key", "llm_model", "llm_revision", "llm_routes", "llm_enable_thinking",
        "inference_token", "inference_timeout_seconds", "chat_query_expansion", "chat_query_expansion_timeout_seconds",
        "embedding_enabled", "embedding_base_url", "embedding_token", "embedding_model", "embedding_revision",
        "embedding_dimension", "embedding_timeout_seconds", "embedding_min_score",
        "diarization_enabled", "diarization_base_url", "diarization_timeout_seconds",
        "voice_recognition_enabled", "voice_recognition_report", "worker_enabled",
    ]
    private func isVisible(_ field: ServerSettingField) -> Bool {
        let key = field.key
        guard advancedKeys.contains(key) else { return false }
        if key.hasPrefix("embedding_"), key != "embedding_enabled" {
            return values["embedding_enabled"] == .bool(true)
        }
        if key.hasPrefix("diarization_"), key != "diarization_enabled" {
            return values["diarization_enabled"] == .bool(true)
        }
        if key == "voice_recognition_enabled" { return values["diarization_enabled"] == .bool(true) }
        if key == "voice_recognition_report" {
            return values["diarization_enabled"] == .bool(true) && values["voice_recognition_enabled"] == .bool(true)
        }
        if key == "chat_query_expansion_timeout_seconds" { return values["chat_query_expansion"] == .bool(true) }
        if key == "stt_enhancement_timeout_seconds" { return values["stt_enhancement_mode"] != .string("off") }
        return true
    }
    private func matches(_ field: ServerSettingField) -> Bool {
        isVisible(field) && (search.isEmpty || "\(field.label) \(field.key) \(field.description) \(L10n.key(field.label)) \(L10n.key(field.description))".localizedCaseInsensitiveContains(search))
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
        let title = L10n.key(field.label) + (field.restart_required ? L10n.text(" · restart required") : "")
        if field.kind == "routes" {
            Text(L10n.key(field.label)).font(.headline)
            ForEach(routes[field.key] ?? []) { row in
                ServerRouteControl(route: Binding(get: {
                    routes[field.key]?.first(where: { $0.id == row.id }) ?? row
                }, set: { updated in
                    routes[field.key] = routes[field.key]?.map { $0.id == row.id ? updated : $0 }
                    status = nil
                }), llm: field.key == "llm_routes", configured: snapshot.secrets["\(field.key).\(row.name.lowercased()).token"] ?? false) {
                    routes[field.key]?.removeAll { $0.id == row.id }; status = nil
                }
                let provider = field.key == "llm_routes" ? "llm" : "stt"
                ServerModelConnectionView(provider: provider, modelName: Binding(get: {
                    routes[field.key]?.first(where: { $0.id == row.id })?.model ?? row.model
                }, set: { name in
                    if let index = routes[field.key]?.firstIndex(where: { $0.id == row.id }) {
                        routes[field.key]?[index].model = name; status = nil
                    }
                }), revision: connectionRevision, inheritedModel: values[provider + "_model"]?.text ?? "") { includeModel in
                    try connectionRequest(provider: provider, route: routes[field.key]?.first(where: { $0.id == row.id }) ?? row, includeModel: includeModel)
                }
            }
            Button(field.key == "stt_routes" ? L10n.text("Add language route") : L10n.text("Add task route")) {
                let name = field.key == "llm_routes" ? tasks.first(where: { task in
                    !(routes[field.key] ?? []).contains(where: { $0.name == task })
                }) ?? "chat" : ""
                routes[field.key, default: []].append(ServerModelRoute(name: name)); status = nil
            }.disabled(field.key == "llm_routes" && (routes[field.key]?.count ?? 0) >= tasks.count)
        } else if field.kind == "secret" {
            ServerSecretControl(label: L10n.key(field.label), configured: snapshot.secrets[field.key] ?? false,
                nullable: field.nullable,
                edit: Binding(get: { secrets[field.key] ?? ServerSecretEdit() }, set: { secrets[field.key] = $0; status = nil }))
        } else if field.kind == "boolean" {
            if field.nullable {
                Picker(title, selection: Binding(get: {
                    if case .bool(let value) = values[field.key] { return value ? "true" : "false" }
                    return "default"
                }, set: { values[field.key] = $0 == "default" ? .null : .bool($0 == "true"); status = nil })) {
                    Text(L10n.text("Model default")).tag("default"); Text(L10n.text("Enabled")).tag("true"); Text(L10n.text("Disabled")).tag("false")
                }
            } else {
                Toggle(title, isOn: Binding(get: { values[field.key] == .bool(true) }, set: { values[field.key] = .bool($0); status = nil }))
            }
        } else {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.subheadline)
                TextField(field.nullable ? L10n.text("Default") : L10n.key(field.label), text: valueBinding(field.key))
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .keyboardType(field.kind == "number" || field.kind == "integer" ? .numbersAndPunctuation : field.key.contains("base_url") ? .URL : .default)
                    .accessibilityLabel(title)
            }
        }
        if !field.description.isEmpty {
            Text(L10n.key(field.description)).font(.caption).foregroundStyle(.secondary)
        }
    }

    private func connectionRequest(provider: String, route: ServerModelRoute? = nil, includeModel: Bool) throws -> [String: Any] {
        var changes: [String: JSONValue] = [provider + "_base_url": .string((values[provider + "_base_url"]?.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines))]
        if includeModel {
            changes[provider + "_model"] = .string((values[provider + "_model"]?.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines))
            if provider == "llm", let thinking = values["llm_enable_thinking"] { changes["llm_enable_thinking"] = thinking }
        }
        for key in ["inference_token", provider + "_api_key"] {
            if let value = try (secrets[key] ?? ServerSecretEdit()).change() { changes[key] = value }
        }
        var request: [String: JSONValue] = ["provider": .string(provider)]
        if let route {
            let name = route.name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            guard name.range(of: "^[a-z-]{2,32}$", options: .regularExpression) != nil else {
                throw BackendFailure(message: L10n.text("Enter a language code or task name before checking this route."))
            }
            var item: [String: JSONValue] = [
                "model": includeModel && !route.model.isEmpty ? .string(route.model.trimmingCharacters(in: .whitespacesAndNewlines)) : .null,
                "base_url": route.endpoint.isEmpty ? .null : .string(route.endpoint.trimmingCharacters(in: .whitespacesAndNewlines))
            ]
            if provider == "llm" { item["revision"] = .null }
            if let token = try route.secret.change() { item["token"] = token }
            changes[provider + "_routes"] = .object([name: .object(item)])
            request["route"] = .string(name)
        }
        request["changes"] = .object(changes)
        return try JSONSerialization.jsonObject(with: JSONEncoder().encode(JSONValue.object(request))) as! [String: Any]
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
        catch { self.error = L10n.error(error) }
    }
    private func save(_ snapshot: ServerConfiguration) async {
        busy = true; error = nil; status = nil; defer { busy = false }
        do {
            var changes: [String: JSONValue] = [:]
            for field in snapshot.fields where isVisible(field) {
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
                              map[name] == nil else { throw BackendFailure(message: L10n.text("Enter a unique language code or task for every route.")) }
                        var route: [String: JSONValue] = ["model": row.model.isEmpty ? .null : .string(row.model), "base_url": row.endpoint.isEmpty ? .null : .string(row.endpoint)]
                        if field.key == "llm_routes" { route["revision"] = row.revision.isEmpty ? .null : .string(row.revision) }
                        if let token = try row.secret.change() { route["token"] = token }
                        map[name] = .object(route)
                    }
                    value = .object(map)
                } else if field.kind == "integer" || field.kind == "number" {
                    if case .string(let text) = value {
                        guard let number = Double(text), number.isFinite else { throw BackendFailure(message: L10n.text("Enter a number for \(L10n.key(field.label)).")) }
                        value = .number(number)
                    }
                } else if field.nullable && value == .string("") { value = .null }
                if value != snapshot.values[field.key] { changes[field.key] = value }
            }
            if changes.isEmpty { status = L10n.text("Server settings are up to date"); return }
            let payload = try JSONSerialization.jsonObject(with: JSONEncoder().encode(JSONValue.object(changes)))
            let result = try await model.client().decoded("v1/configuration", method: "PATCH", body: ["revision": snapshot.revision, "changes": payload], as: ServerConfiguration.self)
            apply(result); status = L10n.text("Server settings saved")
            await model.refreshStatus()
        } catch { self.error = L10n.error(error) }
    }
}


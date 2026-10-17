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
            guard !value.isEmpty else { throw BackendFailure(message: "Enter the replacement key or choose another key action.") }
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
                            ServerModelConnectionView(provider: provider, modelName: valueBinding(provider + "_model"),
                                                      revision: connectionRevision, inheritedModel: "") { includeModel in
                                try connectionRequest(provider: provider, includeModel: includeModel)
                            }
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
                throw BackendFailure(message: "Enter a language code or task name before checking this route.")
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


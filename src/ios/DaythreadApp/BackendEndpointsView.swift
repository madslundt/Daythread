import SwiftUI
import OmiCore

private struct BackendEndpointDraft: Identifiable {
    let id = UUID()
    let endpoint: BackendEndpoint?
}

struct BackendEndpointsView: View {
    @Environment(AppModel.self) private var model
    @State private var draft: BackendEndpointDraft?
    @State private var error: String?

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            if model.usesManagedHosting {
                NavigationLink(L10n.text("Account & connection")) { ManagedAccountView() }
                Text(L10n.text("The managed service address is fixed by this app."))
            } else {
            Section {
                ForEach(model.backendEndpoints) { endpoint in
                    Button {
                        draft = BackendEndpointDraft(endpoint: endpoint)
                    } label: {
                        BackendEndpointRow(
                            endpoint: endpoint,
                            isActive: model.backendConfiguration.activeEndpoint?.id == endpoint.id
                        )
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint(L10n.text("Edit URL and connection options"))
                    .accessibilityIdentifier("endpoints.row.\(endpoint.id)")
                    .deleteDisabled(model.backendEndpoints.count <= 1)
                    .contextMenu {
                        Button {
                            draft = BackendEndpointDraft(endpoint: endpoint)
                        } label: {
                            Label(L10n.text("Edit endpoint"), systemImage: "pencil")
                        }
                        Button {
                            model.activateEndpoint(endpoint.id)
                        } label: {
                            Label(L10n.text("Use this endpoint"), systemImage: "checkmark.circle")
                        }
                        .disabled(model.backendConfiguration.activeEndpoint?.id == endpoint.id)
                    }
                }
                .onDelete(perform: remove)
                .onMove { model.moveEndpoints(from: $0, to: $1) }
            } header: {
                Text(L10n.text("Endpoints"))
            } footer: {
                Text(L10n.text("Tap an endpoint to edit its URL or test its connection. Use Edit to delete or reorder the list. Keep at least one endpoint."))
            }
            Section {
                Button {
                    draft = BackendEndpointDraft(endpoint: nil)
                } label: {
                    Label(L10n.text("Add endpoint"), systemImage: "plus")
                }
                .accessibilityIdentifier("endpoints.add")
            } footer: {
                Text(L10n.text("Requests start with the active endpoint, then try the remaining endpoints in order if it is unavailable. A successful endpoint becomes active automatically."))
            }
            ErrorNote(message: error)
            }
        }
        .listStyle(.insetGrouped)
        .omiScreen()
        .navigationTitle(L10n.text("Backend endpoints"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                EditButton().accessibilityIdentifier("endpoints.edit")
            }
        }
        .sheet(item: $draft) { value in
            BackendEndpointEditor(endpoint: value.endpoint)
        }
    }

    private func remove(at offsets: IndexSet) {
        let endpoints = offsets.map { model.backendEndpoints[$0] }
        guard endpoints.count < model.backendEndpoints.count else { return }
        do {
            for endpoint in endpoints { try model.removeEndpoint(endpoint.id) }
            error = nil
        } catch { self.error = L10n.error(error) }
    }
}

private struct BackendEndpointRow: View {
    let endpoint: BackendEndpoint
    let isActive: Bool

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        HStack(spacing: 12) {
            Image(systemName: isActive ? "checkmark.circle.fill" : "network")
                .foregroundStyle(isActive ? Theme.accent : .secondary)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(endpoint.address)
                    .font(.body)
                    .foregroundStyle(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(isActive ? L10n.text("Active") : L10n.text("Fallback"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

private struct BackendEndpointEditor: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let endpoint: BackendEndpoint?
    @State private var address: String
    @State private var error: String?
    @State private var connectionResult: String?
    @State private var testing = false
    @FocusState private var addressFocused: Bool

    init(endpoint: BackendEndpoint?) {
        self.endpoint = endpoint
        _address = State(initialValue: endpoint?.address ?? "")
    }

    private var trimmedAddress: String { address.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var hasChanges: Bool { trimmedAddress != endpoint?.address }
    private var isHTTP: Bool { URL(string: trimmedAddress)?.scheme?.lowercased() == "http" }
    private var isActive: Bool { endpoint?.id == model.backendConfiguration.activeEndpoint?.id }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            Form {
                Section {
                    TextField("https://daythread.home.arpa", text: $address)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .focused($addressFocused)
                        .submitLabel(.done)
                        .onSubmit { save() }
                        .accessibilityLabel(L10n.text("Backend endpoint URL"))
                        .accessibilityIdentifier("endpoints.url")
                } header: {
                    Text(L10n.text("Endpoint URL"))
                } footer: {
                    Text(L10n.text("Include http:// or https:// and a port if needed. Do not include credentials, a query or a fragment."))
                }
                if isHTTP {
                    Section {
                        Label(L10n.text("HTTP is unencrypted. Use it only on a trusted LAN or private VPN. HTTPS is recommended."), systemImage: "exclamationmark.triangle")
                            .font(.callout).foregroundStyle(.orange)
                    }
                }
                if let endpoint, !hasChanges {
                    Section(L10n.text("Connection")) {
                        if isActive {
                            Label(L10n.text("Active endpoint"), systemImage: "checkmark.circle.fill")
                                .foregroundStyle(Theme.accent)
                        } else {
                            Button(L10n.text("Use this endpoint")) { model.activateEndpoint(endpoint.id) }
                                .accessibilityIdentifier("endpoints.activate")
                        }
                        Button { testing = true } label: {
                            HStack {
                                Text(testing ? L10n.text("Testing…") : L10n.text("Test connection"))
                                Spacer()
                                if testing { ProgressView() }
                            }
                        }
                        .disabled(testing)
                        .accessibilityIdentifier("endpoints.test")
                        if let connectionResult {
                            Label(connectionResult, systemImage: "checkmark.circle")
                                .font(.callout).foregroundStyle(.green)
                        }
                    }
                }
                ErrorNote(message: error)
            }
            .omiScreen()
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle(endpoint == nil ? L10n.text("Add endpoint") : L10n.text("Edit endpoint"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(L10n.text("Cancel")) { dismiss() }.accessibilityIdentifier("endpoints.cancel")
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(endpoint == nil ? L10n.text("Add") : L10n.text("Save"), action: save)
                        .disabled(trimmedAddress.isEmpty || !hasChanges)
                        .accessibilityIdentifier("endpoints.save")
                }
            }
            .onAppear { addressFocused = endpoint == nil }
            .onChange(of: address) { _, _ in
                error = nil
                connectionResult = nil
                testing = false
            }
            .task(id: testing) {
                guard testing, let endpoint else { return }
                error = nil; connectionResult = nil
                do {
                    try await model.testEndpoint(endpoint)
                    guard !Task.isCancelled else { return }
                    connectionResult = L10n.text("Reachable")
                } catch {
                    guard !Task.isCancelled else { return }
                    self.error = L10n.error(error)
                }
                testing = false
            }
        }
    }

    private func save() {
        guard !trimmedAddress.isEmpty, hasChanges else { return }
        do {
            let normalized = try BackendClient.validatedURL(trimmedAddress).absoluteString
            let withoutTrailingSlash = normalized.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            guard !model.backendEndpoints.contains(where: {
                $0.id != endpoint?.id && $0.address.trimmingCharacters(in: CharacterSet(charactersIn: "/")) == withoutTrailingSlash
            }) else {
                error = L10n.text("This endpoint is already in your list.")
                return
            }
            try model.saveEndpoint(address: normalized, replacing: endpoint?.id)
            dismiss()
        } catch {
            self.error = L10n.error(error)
        }
    }
}

import SwiftUI
import OmiCore

struct OmiConnectionsView: View {
    @Environment(AppModel.self) private var model
    @State private var configured = false
    @State private var loaded = false
    @State private var importing = false
    @State private var error: String?
    @State private var result: JSONValue = .null
    @State private var nextPage: ImportPage?
    @State private var requestID = UUID()
    @State private var operation: Task<Void, Never>?
    private struct ImportPage { let end: String; let offset: Int }
    private var available: Bool { model.hasSelectedProfile && loaded && configured && !importing }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Section {
                Text(L10n.text("Your pendant can send recordings through this iPhone over Bluetooth without an Omi API key."))
                    .foregroundStyle(.secondary)
            } header: { Text(L10n.text("Local capture")) }
            Section {
                Text(L10n.text("Import existing conversations from Omi's cloud into the selected profile. Imports include transcripts only, without original audio."))
                    .font(.callout).foregroundStyle(.secondary)
                if !model.hasSelectedProfile {
                    Text(L10n.text("Choose a profile in Settings → Profiles to import conversations.")).font(.callout)
                } else if loaded {
                    Label(configured ? L10n.text("Omi API configured on home-core") : L10n.text("Omi API key needed"), systemImage: configured ? "checkmark.circle" : "key")
                        .font(.callout)
                    if !configured { NavigationLink(L10n.text("Set up Omi API key")) { ServerConfigurationView() } }
                } else { ProgressView(L10n.text("Checking Omi connection…")) }
                if case .object = result {
                    Text(L10n.text("Last page: \(result["imported"].integer) imported, \(result["existing"].integer) already in your archive, \(result["skipped"].integer) skipped."))
                        .font(.callout)
                    if nextPage == nil { Text(L10n.text("No more conversations in this import.")).font(.caption).foregroundStyle(.secondary) }
                }
                Button(importing ? L10n.text("Importing…") : L10n.text("Import latest conversations"), systemImage: "square.and.arrow.down") { importPage(next: false) }
                    .disabled(!available).accessibilityIdentifier("connections.importOmi")
                if nextPage != nil {
                    Button(L10n.text("Load next 25")) { importPage(next: true) }.disabled(!available)
                        .accessibilityIdentifier("connections.importNext")
                }
                Button(L10n.text("Check connection again")) { Task { await load() } }.disabled(importing || !model.hasSelectedProfile)
                ErrorNote(message: error)
            } header: { Text(L10n.text("Import from Omi cloud")) } footer: {
                Text(L10n.text("Up to 25 conversations per page, newest first. Existing imports are skipped to preserve your corrections. Keys stay on home-core."))
            }
            Section {
                NavigationLink { AudioForwardingSettingsView() } label: {
                    Label(L10n.text("Official app audio forwarding"), systemImage: "antenna.radiowaves.left.and.right")
                }.disabled(!model.hasSelectedProfile)
            } header: { Text(L10n.text("Advanced connection")) } footer: { Text(L10n.text("An optional receiver for audio sent from Omi's backend.")) }
        }.omiScreen().navigationTitle(L10n.text("Omi connections")).navigationBarTitleDisplayMode(.inline)
            .task(id: model.archiveIdentity) { reset(); await load() }
            .onDisappear { requestID = UUID(); operation?.cancel(); operation = nil; importing = false }
    }
    private func reset() {
        requestID = UUID(); operation?.cancel(); operation = nil
        configured = false; loaded = false; importing = false; result = .null; nextPage = nil; error = nil
    }
    private func load() async {
        guard model.hasSelectedProfile else { loaded = true; return }
        let identity = model.archiveIdentity, request = UUID()
        requestID = request; error = nil
        do {
            let result = try await model.client().request("v1/integrations/omi")
            guard !Task.isCancelled, model.archiveIdentity == identity, requestID == request else { return }
            configured = result["configured"] == .bool(true); loaded = true
        } catch {
            guard !Task.isCancelled, model.archiveIdentity == identity, requestID == request else { return }
            self.error = L10n.error(error); loaded = true
        }
    }
    private func importPage(next: Bool) {
        guard available, !next || nextPage != nil else { return }
        let page = next ? nextPage! : ImportPage(end: DurableStore.stamp(Date()), offset: 0)
        let identity = model.archiveIdentity, request = UUID()
        requestID = request; importing = true; error = nil; result = .null
        if !next { nextPage = nil }
        operation = Task {
            defer { if requestID == request { importing = false; operation = nil } }
            do {
                let response = try await model.client().request("v1/integrations/omi/import", method: "POST", body: ["end_date": page.end, "limit": 25, "offset": page.offset])
                guard !Task.isCancelled, model.archiveIdentity == identity, requestID == request else { return }
                result = response
                nextPage = response["has_more"] == .bool(true) ? ImportPage(end: page.end, offset: response["next_offset"].integer) : nil
                await model.refreshStatus()
            } catch {
                guard !Task.isCancelled, model.archiveIdentity == identity, requestID == request else { return }
                self.error = L10n.error(error)
            }
        }
    }
}

private struct AudioForwardingSettingsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var status: JSONValue = .null
    @State private var origin = ""
    @State private var busy = false
    @State private var error: String?
    @State private var requestID = UUID()
    private var receiverURL: URL? {
        guard var components = URLComponents(string: origin.trimmingCharacters(in: .whitespacesAndNewlines)),
              components.scheme == "https", components.host != nil, components.user == nil, components.password == nil else { return nil }
        components.path = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        components.path = "/" + (components.path.isEmpty ? "" : components.path + "/") + "v1/integrations/omi/audio"
        components.queryItems = [URLQueryItem(name: "profile_id", value: model.selectedProfileID), URLQueryItem(name: "token", value: "REPLACE_WITH_SERVER_TOKEN")]
        components.fragment = nil
        return components.url
    }
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        Form {
            Section {
                Text(L10n.text("Omi's backend sends these requests and blocks private destinations. A phone on your LAN or VPN alone cannot deliver them. Forwarding uses the cloud; no network exposure is configured here."))
                    .font(.callout).foregroundStyle(.secondary)
            }
            Section(L10n.text("Receiver status")) {
                if case .object = status {
                    LabeledContent(L10n.text("Receiver"), value: status["ready"] == .bool(true) ? L10n.text("Ready") : status["enabled"] == .bool(true) ? L10n.text("Missing token") : L10n.text("Disabled"))
                    LabeledContent(L10n.text("Accepted deliveries"), value: "\(status["accepted_requests"].integer)")
                    LabeledContent(L10n.text("Last received"), value: AppModel.date(status["last_received_at"].text)?.formatted(.dateTime.locale(L10n.locale).day().month(.abbreviated).year().hour().minute()) ?? L10n.text("Never"))
                    Text(L10n.text("A receipt confirms that chunk arrived. It does not prove continuous capture or a connected pendant.")).font(.caption).foregroundStyle(.secondary)
                    if !status["last_source_id"].text.isEmpty {
                        NavigationLink(L10n.text("Inspect latest recording")) { SourceDetail(source: .object(["id": status["last_source_id"]])) }
                    }
                    if status["worker_enabled"] == .bool(false) {
                        Text(L10n.text("The transcription worker is disabled. Enable it in server settings and restart home-core to process accepted audio.")).font(.callout)
                    }
                    if case .object = status["last_failure"] {
                        Text(L10n.text("Last receiver failure (\(status["last_failure"]["status"].integer)): \(status["last_failure"]["detail"].text)")).font(.callout)
                    }
                    Button(status["enabled"] == .bool(true) ? L10n.text("Disable audio receiver") : L10n.text("Enable audio receiver")) { Task { await toggle() } }
                        .disabled(busy || (status["configured"] != .bool(true) && status["enabled"] != .bool(true)))
                        .accessibilityIdentifier("connections.toggleForwarding")
                } else { ProgressView(L10n.text("Checking receiver…")) }
                Button(L10n.text("Refresh status")) { Task { await load() } }.disabled(busy)
                ErrorNote(message: error)
            }
            Section(L10n.text("1. Configure a server token")) {
                NavigationLink(L10n.text("Open models & server settings")) { ServerConfigurationView() }
                Text(L10n.text("Set Audio forwarding token to a random URL-safe secret of at least 32 characters. If you choose hosted delivery, use a dedicated HTTPS ingress allowing only this POST route; keep the archive UI and other APIs private."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section(L10n.text("2. Choose a receiver address")) {
                TextField(L10n.text("HTTPS origin reachable by Omi"), text: $origin).textContentType(.URL).keyboardType(.URL)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                if let receiverURL {
                    Text(receiverURL.absoluteString).font(.caption.monospaced()).textSelection(.enabled)
                    ShareLink(item: receiverURL.absoluteString) { Label(L10n.text("Share URL template"), systemImage: "square.and.arrow.up") }
                } else { Text(L10n.text("Enter an HTTPS receiver origin.")).font(.caption).foregroundStyle(.secondary) }
                Text(L10n.text("This URL selects the current profile. Replace the token privately in the official app. Do not add sample_rate or uid. The token and receiver switch are shared by all profiles."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section(L10n.text("3. Connect the official app")) {
                Text(L10n.text("In Omi Settings → Developer Mode → Realtime audio bytes, save the URL, set Every x seconds to 10 and enable delivery. Speak a test phrase, then refresh status and inspect the recording."))
                Text(L10n.text("Input is PCM16 mono at 8 or 16 kHz, up to 120 seconds per request. Times estimate receipt; missing capture, order, offline replay and device identity remain unknown. Capture pause rejects new webhooks while accepted audio keeps processing."))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.omiScreen().navigationTitle(L10n.text("Audio forwarding")).navigationBarTitleDisplayMode(.inline)
            .task(id: model.archiveIdentity + String(describing: scenePhase)) {
                guard scenePhase == .active else { return }
                status = .null; origin = model.backendAddress
                await load()
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(5))
                    if !Task.isCancelled && scenePhase == .active && !busy { await load() }
                }
            }
    }
    private func load() async {
        let identity = model.archiveIdentity, request = UUID()
        requestID = request
        do {
            let response = try await model.client().request("v1/integrations/omi/audio/status")
            guard !Task.isCancelled, model.archiveIdentity == identity, requestID == request else { return }
            status = response; error = nil
        } catch {
            guard !Task.isCancelled, model.archiveIdentity == identity, requestID == request else { return }
            self.error = L10n.error(error)
        }
    }
    private func toggle() async {
        let identity = model.archiveIdentity
        busy = true; error = nil; defer { busy = false }
        do {
            _ = try await model.client().request("v1/settings", method: "PATCH", body: ["audio_forwarding_enabled": status["enabled"] != .bool(true)])
            guard !Task.isCancelled, model.archiveIdentity == identity else { return }
            await load(); await model.refreshStatus()
        } catch {
            guard !Task.isCancelled, model.archiveIdentity == identity else { return }
            self.error = L10n.error(error)
        }
    }
}

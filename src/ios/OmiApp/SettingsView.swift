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
    var body: some View {
        @Bindable var model = model
        Form {
            Section("Appearance") {
                Picker("Appearance", selection: $appearance) {
                    ForEach(AppAppearance.allCases) { option in
                        Text(option.title).tag(option)
                    }
                }.accessibilityIdentifier("settings.appearance")
            }
            Section {
                TextField("http://home-core.local:8787", text: $address)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL).accessibilityIdentifier("settings.backend")
                Button("Save & test connection") { Task { await saveConnection() } }.disabled(loading)
                if loading { ProgressView() }
                if let status { Text(status).font(.callout).foregroundStyle(.secondary) }
                ErrorNote(message: error)
                if let url = try? model.client().base { Link("Open web app", destination: url) }
            } header: { Text("Home-core") } footer: { Text("Use your home-core LAN address or private VPN. No account or authentication is required.") }
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
            }
            Section("Pendant information") {
                LabeledContent("Model", value: model.bluetooth.model)
                LabeledContent("Firmware", value: model.bluetooth.firmware)
                LabeledContent("Codec", value: model.bluetooth.codec.map(String.init) ?? "Unknown")
                LabeledContent("Button notifications", value: model.bluetooth.buttonAvailable ? "Available" : "Not discovered")
                if model.bluetooth.connected { Button("Disconnect and forget Omi", role: .destructive) { model.flush(); model.bluetooth.disconnect() } }
            }
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
        }.navigationTitle("Settings").navigationBarTitleDisplayMode(.inline)
            .task { address = model.backendAddress; timeout = model.meetingMinutes; await loadSettings() }
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
        do {
            let settings = try await model.client().request("v1/settings")
            if settings["meeting_max_minutes"].integer > 0 {
                model.meetingMinutes = settings["meeting_max_minutes"].integer; timeout = model.meetingMinutes
            }
            if !settings["language"].text.isEmpty { language = settings["language"].text }
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

import SwiftUI

/// Tests the current draft through home-core; provider credentials never come back to the phone.
struct ServerModelConnectionView: View {
    let provider: String
    @Binding var modelName: String
    let revision: UUID
    let inheritedModel: String
    let requestBody: (Bool) throws -> [String: Any]
    @Environment(AppModel.self) private var model
    @State private var models: [String] = []
    @State private var lookupFinished = false
    @State private var busy = false
    @State private var testing = false
    @State private var error: String?
    @State private var testResult: JSONValue = .null
    @State private var requestID = UUID()
    @State private var operation: Task<Void, Never>?
    private var hasModel: Bool {
        !(modelName.isEmpty ? inheritedModel : modelName).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 10) {
            if !models.isEmpty {
                Menu(L10n.text("Choose an available model"), systemImage: "list.bullet") {
                    if !inheritedModel.isEmpty { Button(L10n.text("Use default (\(inheritedModel))")) { modelName = "" } }
                    ForEach(models, id: \.self) { name in Button(name) { modelName = name } }
                }.accessibilityIdentifier("settings.availableModels.\(provider)")
                Text(L10n.text("\(models.count) models returned. Choose one that supports \(provider == "stt" ? L10n.text("transcription") : L10n.text("chat"))."))
                    .font(.caption).foregroundStyle(.secondary)
            } else if lookupFinished {
                Text(L10n.text("The server returned no models. Enter a custom model name above.")).font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button(busy && !testing ? L10n.text("Looking up…") : L10n.text("Find models")) { run(test: false) }
                    .disabled(busy).accessibilityIdentifier("settings.findModels.\(provider)")
                Spacer()
                Button(busy && testing ? L10n.text("Testing…") : L10n.text("Test connection")) { run(test: true) }
                    .disabled(busy || !hasModel).accessibilityIdentifier("settings.testConnection.\(provider)")
            }.buttonStyle(.borderless)
            Text(L10n.text("Tests use your current entries without saving. \(provider == "stt" ? L10n.text("A short synthetic audio clip is sent; your recordings are not used.") : L10n.text("A short test message is sent to the selected model."))"))
                .font(.caption).foregroundStyle(.secondary)
            ErrorNote(message: error)
            if case .object = testResult {
                Label(L10n.key(testResult["message"].text), systemImage: testResult["ok"] == .bool(true) ? "checkmark.circle" : "exclamationmark.circle")
                    .font(.callout).foregroundStyle(testResult["ok"] == .bool(true) ? Color.primary : Color.red)
            }
        }.padding(.vertical, 4)
            .onChange(of: revision) { _, _ in invalidate() }
            .onChange(of: model.archiveIdentity) { _, _ in invalidate() }
            .onDisappear { invalidate() }
    }
    private func invalidate() {
        requestID = UUID(); operation?.cancel(); operation = nil
        models = []; lookupFinished = false; busy = false; error = nil; testResult = .null
    }
    private func run(test: Bool) {
        guard !busy else { return }
        let body: [String: Any], client: BackendClient
        do { body = try requestBody(test); client = try model.client() }
        catch { self.error = L10n.error(error); return }
        let identity = model.archiveIdentity, request = UUID(), currentRevision = revision
        requestID = request; busy = true; testing = test; error = nil; testResult = .null
        operation = Task {
            defer { if requestID == request { busy = false; operation = nil } }
            do {
                let result = try await client.request(test ? "v1/configuration/test" : "v1/configuration/models", method: "POST", body: body)
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, revision == currentRevision else { return }
                if test { testResult = result }
                else { models = result["models"].array.map(\.text); lookupFinished = true }
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, revision == currentRevision else { return }
                self.error = L10n.error(error)
            }
        }
    }
}

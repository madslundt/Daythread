import SwiftUI
import OmiCore

/// A presentation choice starts the existing durable recap job; closing this view only stops polling.
struct RecapTemplateControl: View {
    @Environment(AppModel.self) private var model
    @Environment(\.locale) private var interfaceLocale
    let day: String
    var onGenerated: (() -> Void)?
    @State private var expanded = false
    @State private var catalog: JSONValue = .null
    @State private var template = "general"
    @State private var instructions = ""
    @State private var makeDefault = false
    @State private var busy = false
    @State private var error: String?
    @State private var operation: Task<Void, Never>?
    private var context: String { model.archiveIdentity + ":" + day }

    var body: some View {
        let _ = interfaceLocale
        DisclosureGroup(L10n.text("Summary template"), isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 12) {
                Text(L10n.text("Choose how this day is summarized. Original recordings and citations stay available."))
                    .font(.caption).foregroundStyle(.secondary)
                if catalog != .null {
                    Picker(L10n.text("Summary template"), selection: $template) {
                        ForEach(Array(catalog["presets"].array.enumerated()), id: \.offset) { _, preset in
                            Text(L10n.key(preset["label"].text)).tag(preset["id"].text)
                        }
                    }
                    TextField(L10n.text("Presentation preferences"), text: $instructions, axis: .vertical)
                        .lineLimit(3...6).textFieldStyle(.roundedBorder)
                        .onChange(of: instructions) { _, value in
                            if value.count > 2000 { instructions = String(value.prefix(2000)) }
                        }
                    Toggle(L10n.text("Use for new days"), isOn: $makeDefault)
                    Button(L10n.text("Generate with template")) { generate() }
                        .accessibilityIdentifier("recap.template.generate")
                } else if error == nil { ProgressView(L10n.text("Loading templates…")) }
                ErrorNote(message: error)
                if error != nil && catalog == .null {
                    Button(L10n.text("Retry")) { operation = Task { await load() } }
                }
                if busy { ProgressView(L10n.text("Generating recap…")) }
            }.disabled(busy).padding(.vertical, 8)
        }
        .task(id: expanded ? context : "") { if expanded { await load() } }
        .onDisappear { operation?.cancel(); operation = nil }
        .onChange(of: context) { _, _ in
            operation?.cancel(); operation = nil; catalog = .null; busy = false; error = nil
        }
    }

    private func load() async {
        let key = context
        error = nil
        do {
            let value = try await model.client().request("v1/summary-templates", query: ["day": day])
            guard !Task.isCancelled, key == context else { return }
            catalog = value
            template = value["selection"]["template"].text
            instructions = value["selection"]["instructions"].text
        } catch { if !Task.isCancelled, key == context { self.error = L10n.error(error) } }
    }

    private func generate() {
        guard !busy else { return }
        let key = context, selectedTemplate = template, selectedInstructions = instructions
        busy = true; error = nil
        operation = Task {
            defer { if key == context { busy = false; operation = nil } }
            do {
                let client = try model.client()
                if makeDefault {
                    _ = try await client.request("v1/settings", method: "PATCH", body: [
                        "summary_template": selectedTemplate,
                        "summary_template_instructions": selectedInstructions
                    ])
                }
                try Task.checkCancellation()
                guard key == context else { return }
                _ = try await client.request("v1/recaps/\(day)", method: "POST", body: [
                    "template": selectedTemplate, "instructions": selectedInstructions
                ])
                guard !Task.isCancelled, key == context else { return }
                onGenerated?()
            } catch { if !Task.isCancelled, key == context { self.error = L10n.error(error) } }
        }
    }
}

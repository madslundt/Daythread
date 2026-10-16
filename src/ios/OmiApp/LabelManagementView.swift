import SwiftUI

struct LabelManagementView: View {
    let entityID: String
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var response: LabelTimelineResponse?
    @State private var entries: [LabelTimelineEntry] = []
    @State private var directory: LabelDirectory?
    @State private var target = ""
    @State private var preview: LabelTimelineResponse?
    @State private var splitDraft = LabelDraft()
    @State private var splitID = UUID().uuidString.lowercased()
    @State private var selected = Set<String>()
    @State private var writer = LabelWriter()
    @State private var error: String?
    @State private var confirmingDelete = false
    @State private var confirmingMerge = false
    var body: some View {
        Form {
            if let response {
                Section("Merge") {
                    Picker("Destination", selection: $target) {
                        Text("Choose a label").tag("")
                        ForEach((directory?.items ?? []).filter { $0.id != entityID && $0.kind == response.entity.kind }) { entity in Text(entity.name + " · " + entity.description).tag(entity.id) }
                    }
                    if let preview {
                        Text("Keep \(preview.entity.name). Combine its \(preview.total) entries with all \(response.total) entries from \(response.entity.name). Different association meanings stay distinct. The former name becomes an alias.")
                        Text("Destination aliases: " + preview.entity.aliases.joined(separator: ", ")).font(.caption)
                        Button("Review and confirm merge") { confirmingMerge = true }.disabled(writer.busy)
                    }
                }
                Section("Split selected assignments") {
                    LabelDraftFields(draft: $splitDraft, fixedKind: true)
                    Text("Only checked source associations and note assignments move. Existing aliases stay here; set aliases for the new label explicitly.").font(.caption)
                    ForEach(entries) { entry in
                        Toggle(isOn: Binding(get: { selected.contains(entry.id) }, set: { if $0 { selected.insert(entry.id) } else { selected.remove(entry.id) } })) {
                            VStack(alignment: .leading) { Text(entry.title ?? associationMeaning(entry.relation ?? "")); Text(entry.started_at).font(.caption) }
                        }
                    }
                    Button("Move checked assignments into new label") { Task { await split() } }.disabled(writer.busy || selected.isEmpty || splitDraft.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                Section("Delete label") {
                    Text("Deletes this label, its associations and attached annotations. Recordings remain, including any names in their transcripts.").font(.caption)
                    Button("Delete label", role: .destructive) { confirmingDelete = true }.disabled(writer.busy)
                }
            }
            ErrorNote(message: error ?? writer.error)
            Button("Reload labels and assignments") { Task { await load() } }.disabled(writer.busy)
        }.navigationTitle("Manage label").navigationBarTitleDisplayMode(.inline)
            .task(id: model.backendAddress) { await load() }
            .task(id: target) {
                preview = nil
                guard !target.isEmpty else { return }
                do { preview = try await model.client().labels("v1/labels/\(target)/timeline") }
                catch { self.error = error.localizedDescription }
            }
            .confirmationDialog("Merge these labels and all their assignments?", isPresented: $confirmingMerge, titleVisibility: .visible) { Button("Merge labels") { Task { await merge() } } }
            .confirmationDialog("Delete label and attached annotations? Recordings are kept.", isPresented: $confirmingDelete, titleVisibility: .visible) { Button("Delete label", role: .destructive) { Task { await remove() } } }
    }
    private func load() async {
        let address = model.backendAddress
        do {
            let client = try model.client()
            let first: LabelTimelineResponse = try await client.labels("v1/labels/\(entityID)/timeline", query: ["limit": "200"])
            var all = first.items
            while all.count < first.total {
                let next: LabelTimelineResponse = try await client.labels("v1/labels/\(entityID)/timeline", query: ["limit": "200", "offset": String(all.count), "revision": first.revision])
                all += next.items
            }
            let labels: LabelDirectory = try await client.labels()
            guard address == model.backendAddress, !Task.isCancelled else { return }
            response = first; entries = all; directory = labels; splitDraft.kind = first.entity.kind; error = nil
        } catch { if address == model.backendAddress { self.error = error.localizedDescription } }
    }
    private func merge() async {
        guard let response else { return }
        var command = LabelCommand(action: "entity.merge", id: entityID, expected_revision: response.revision); command.target_id = target
        await save(command)
    }
    private func split() async {
        guard let response else { return }
        var command = LabelCommand(action: "entity.split", id: entityID, expected_revision: response.revision)
        var clean = splitDraft; clean.aliases = clean.aliases.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        command.value = clean; command.new_id = splitID
        command.link_ids = entries.filter { $0.entry_type == "source" && selected.contains($0.id) }.map(\.id)
        command.decision_link_ids = entries.filter { $0.entry_type == "decision" && selected.contains($0.id) }.map(\.id)
        command.note_ids = entries.filter { $0.entry_type == "note" && selected.contains($0.id) }.map(\.id)
        await save(command)
    }
    private func remove() async {
        guard let response else { return }
        await save(LabelCommand(action: "entity.delete", id: entityID, expected_revision: response.revision))
    }
    private func save(_ command: LabelCommand) async {
        do { if await writer.save(command, client: try model.client()) { dismiss(); await model.refreshStatus() } }
        catch { self.error = error.localizedDescription }
    }
}

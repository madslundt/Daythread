import SwiftUI

struct SourceLabelsView: View {
    let sourceID: String
    @Environment(AppModel.self) private var model
    @State private var response: SourceLabelsResponse?
    @State private var directory: LabelDirectory?
    @State private var entityID = ""
    @State private var relation = "related"
    @State private var query = ""
    @State private var error: String?
    @State private var writer = LabelWriter()
    @State private var linkID = UUID().uuidString.lowercased()
    var selected: MemoryLabel? { directory?.items.first { $0.id == entityID } }
    var body: some View {
        Form {
            Section {
                Text("Imported speaker names and speaker numbers are unverified upstream attributions. Numbers apply only to that source. Name mentions never confirm participation.").font(.caption).foregroundStyle(.secondary)
                if let source = response?.source { Text(source.text ?? "Transcript expired").textSelection(.enabled) }
            }
            Section("Explicit associations") {
                ForEach(response?.links ?? []) { link in
                    VStack(alignment: .leading, spacing: 8) {
                        NavigationLink(directory?.items.first { $0.id == link.entity_id }?.name ?? "Label") { LabelTimelineView(entityID: link.entity_id) }
                        Text(associationMeaning(link.relation)).font(.caption)
                        if link.needs_review {
                            Text("Needs review · transcript changed").foregroundStyle(.orange)
                            Button("Confirm after reviewing transcript") { Task { await saveLink(id: link.id, entity: link.entity_id, meaning: link.relation) } }.disabled(writer.busy)
                        }
                        Button("Detach", role: .destructive) { Task { await detach(link) } }.disabled(writer.busy)
                    }
                }
            }
            Section("Add association") {
                TextField("Find name or alias", text: $query)
                Picker("Label", selection: $entityID) {
                    Text("Choose a label").tag("")
                    ForEach((directory?.items ?? []).filter { query.isEmpty || ([$0.name] + $0.aliases).contains(where: { $0.localizedCaseInsensitiveContains(query) }) }) { entity in
                        Text(entity.name + " · " + entity.kind + (entity.description.isEmpty ? "" : " · " + entity.description)).tag(entity.id)
                    }
                }
                if let selected {
                    Text(selected.description)
                    if selected.kind == "person" {
                        Picker("Meaning", selection: $relation) {
                            ForEach(["related", "mentioned", "participant"], id: \.self) { value in Text(associationMeaning(value)).tag(value) }
                        }
                    } else { Text("About project") }
                    Button("Add explicit association") { Task { await saveLink(id: linkID, entity: entityID, meaning: selected.kind == "project" ? "about_project" : relation) } }.disabled(writer.busy || response?.source.text == nil)
                }
                NavigationLink("Create / manage people & projects") { LabelsView() }
            }
            ErrorNote(message: error ?? writer.error)
            Button("Refresh evidence and labels") { Task { await load() } }
        }.navigationTitle("Source labels").navigationBarTitleDisplayMode(.inline)
            .task(id: model.archiveIdentity + model.labelRevision) { await load() }.refreshable { await load() }
    }
    private func load() async {
        let address = model.archiveIdentity
        do {
            let client = try model.client()
            async let source: SourceLabelsResponse = client.labels("v1/labels/sources/\(sourceID)")
            async let labels: LabelDirectory = client.labels()
            let (s, d) = try await (source, labels)
            guard address == model.archiveIdentity, !Task.isCancelled else { return }
            response = s; directory = d; error = nil
        } catch { if address == model.archiveIdentity { self.error = error.localizedDescription } }
    }
    private func saveLink(id: String, entity: String, meaning: String) async {
        guard let response else { return }
        var command = LabelCommand(action: "link.save", id: id, expected_revision: response.revision)
        command.create = id == linkID
        command.entity_id = entity; command.source = response.source.ref; command.relation = meaning
        do { if await writer.save(command, client: try model.client()) { linkID = UUID().uuidString.lowercased(); await load(); await model.refreshStatus() } }
        catch { self.error = error.localizedDescription }
    }
    private func detach(_ link: LabelAssociation) async {
        guard let response else { return }
        let command = LabelCommand(action: "link.delete", id: link.id, expected_revision: response.revision)
        do { if await writer.save(command, client: try model.client()) { await load(); await model.refreshStatus() } }
        catch { self.error = error.localizedDescription }
    }
}

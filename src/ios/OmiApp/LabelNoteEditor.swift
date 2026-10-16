import SwiftUI
import OmiCore

struct LabelNoteEditor: View {
    let entityID: String
    let revision: String
    let entry: LabelTimelineEntry?
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var id = UUID().uuidString.lowercased()
    @State private var kind = "conversation"
    @State private var title = ""
    @State private var bodyText = ""
    @State private var query = ""
    @State private var page = 0
    @State private var total = 0
    @State private var searching = false
    @State private var searchEpoch = RequestEpoch()
    @State private var available: [LabelSource] = []
    @State private var selected: [LabelSource] = []
    @State private var currentRevision = ""
    @State private var writer = LabelWriter()
    @State private var error: String?
    var body: some View {
        Form {
            Section("Written by you") {
                Picker("Type", selection: $kind) { ForEach(["conversation", "decision", "open_question"], id: \.self) { Text(annotationMeaning($0)).tag($0) } }
                TextField("Title", text: $title)
                TextField("Your note", text: $bodyText, axis: .vertical)
                Text("Select source recordings explicitly. No automatic conversation boundaries or resolution status are inferred.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Selected evidence · \(selected.count)") {
                ForEach(selected) { source in
                    VStack(alignment: .leading) {
                        Text(source.started_at).font(.caption)
                        Text(source.text ?? "Transcript expired").lineLimit(5)
                        Button("Remove from selection", role: .destructive) { selected.removeAll { $0.id == source.id } }
                    }
                }
            }
            Section("Find evidence") {
                TextField("Search transcripts", text: $query)
                Button("Find recordings") { Task { await search() } }
                ForEach(available.filter { item in !selected.contains { $0.id == item.id } }) { source in
                    Button { selected.append(source) } label: {
                        VStack(alignment: .leading) { Text(source.started_at).font(.caption); Text(source.text ?? "No transcript").lineLimit(3).foregroundStyle(.primary) }
                    }.disabled(source.text == nil)
                }
                HStack {
                    Button("Previous recordings") { Task { await search(requestedPage: page - 1) } }.disabled(page == 0 || searching)
                    Text("Page \(page + 1) · \(total) matches").font(.caption)
                    Button("Next recordings") { Task { await search(requestedPage: page + 1) } }.disabled(searching || (page + 1) * 50 >= total)
                }
            }
            if entry?.needs_review == true { Text("Evidence changed. Read each current source before saving to confirm this annotation again.").foregroundStyle(.orange) }
            ErrorNote(message: error ?? writer.error)
            Button("Refresh selected evidence and version for review") { Task { await refresh() } }.disabled(writer.busy)
        }.navigationTitle(entry == nil ? "New annotation" : "Edit annotation")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(writer.busy) }
                ToolbarItem(placement: .confirmationAction) { Button("Save") { Task { await save() } }.disabled(writer.busy || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || selected.isEmpty) }
            }
            .task {
                id = entry?.id ?? id; kind = entry?.kind ?? kind; title = entry?.title ?? ""; bodyText = entry?.body ?? ""
                selected = entry?.sources ?? []; currentRevision = revision
                await search()
            }
    }
    private func search(requestedPage: Int = 0) async {
        let token = searchEpoch.replace(), address = model.archiveIdentity, requestedQuery = query
        searching = true; available = []
        defer { if searchEpoch.accepts(token) { searching = false } }
        do {
            let result = try await model.client().request("v1/transcripts", query: ["q": requestedQuery, "limit": "50", "offset": String(requestedPage * 50)])
            guard !Task.isCancelled, searchEpoch.accepts(token), address == model.archiveIdentity else { return }
            available = try JSONDecoder().decode([LabelSource].self, from: JSONEncoder().encode(result["items"]))
            page = requestedPage; total = result["total"].integer; error = nil
        } catch { if searchEpoch.accepts(token), !Task.isCancelled { self.error = error.localizedDescription } }
    }
    private func refresh() async {
        do {
            let client = try model.client()
            var updated: [LabelSource] = []
            for source in selected {
                let result: SourceLabelsResponse = try await client.labels("v1/labels/sources/\(source.id)")
                if result.source.text != nil { updated.append(result.source) }
            }
            let directory: LabelDirectory = try await client.labels()
            selected = updated; currentRevision = directory.revision; error = nil
        } catch { self.error = error.localizedDescription }
    }
    private func save() async {
        var command = LabelCommand(action: "note.save", id: id, expected_revision: currentRevision)
        command.create = entry == nil
        command.kind = kind; command.title = title; command.body = bodyText
        command.sources = selected.map(\.ref); command.entity_ids = entry?.entity_ids ?? [entityID]
        do { if await writer.save(command, client: try model.client()) { dismiss(); await model.refreshStatus() } }
        catch { self.error = error.localizedDescription }
    }
}

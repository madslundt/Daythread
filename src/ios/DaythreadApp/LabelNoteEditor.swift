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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        Form {
            Section(L10n.text("Written by you")) {
                Picker(L10n.text("Type"), selection: $kind) { ForEach(["conversation", "decision", "open_question"], id: \.self) { Text(annotationMeaning($0)).tag($0) } }
                TextField(L10n.text("Title"), text: $title)
                TextField(L10n.text("Your note"), text: $bodyText, axis: .vertical)
                Text(L10n.text("Select source recordings explicitly. No automatic conversation boundaries or resolution status are inferred.")).font(.caption).foregroundStyle(.secondary)
            }
            Section(L10n.text("Selected evidence · \(selected.count)")) {
                ForEach(selected) { source in
                    VStack(alignment: .leading) {
                        Text(source.started_at).font(.caption)
                        Text(source.text ?? L10n.text("Transcript expired")).lineLimit(5)
                        Button(L10n.text("Remove from selection"), role: .destructive) { selected.removeAll { $0.id == source.id } }
                    }
                }
            }
            Section(L10n.text("Find evidence")) {
                TextField(L10n.text("Search transcripts"), text: $query)
                Button(L10n.text("Find recordings")) { Task { await search() } }
                ForEach(available.filter { item in !selected.contains { $0.id == item.id } }) { source in
                    Button { selected.append(source) } label: {
                        VStack(alignment: .leading) { Text(source.started_at).font(.caption); Text(source.text ?? L10n.text("No transcript")).lineLimit(3).foregroundStyle(.primary) }
                    }.disabled(source.text == nil)
                }
                HStack {
                    Button(L10n.text("Previous recordings")) { Task { await search(requestedPage: page - 1) } }.disabled(page == 0 || searching)
                    Text(L10n.text("Page \(page + 1) · \(total) matches")).font(.caption)
                    Button(L10n.text("Next recordings")) { Task { await search(requestedPage: page + 1) } }.disabled(searching || (page + 1) * 50 >= total)
                }
            }
            if entry?.needs_review == true { Text(L10n.text("Evidence changed. Read each current source before saving to confirm this annotation again.")).foregroundStyle(.orange) }
            ErrorNote(message: error ?? writer.error)
            Button(L10n.text("Refresh selected evidence and version for review")) { Task { await refresh() } }.disabled(writer.busy)
        }.navigationTitle(entry == nil ? L10n.text("New annotation") : L10n.text("Edit annotation"))
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() }.disabled(writer.busy) }
                ToolbarItem(placement: .confirmationAction) { Button(L10n.text("Save")) { Task { await save() } }.disabled(writer.busy || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || selected.isEmpty) }
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
        } catch { if searchEpoch.accepts(token), !Task.isCancelled { self.error = L10n.error(error) } }
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
        } catch { self.error = L10n.error(error) }
    }
    private func save() async {
        var command = LabelCommand(action: "note.save", id: id, expected_revision: currentRevision)
        command.create = entry == nil
        command.kind = kind; command.title = title; command.body = bodyText
        command.sources = selected.map(\.ref); command.entity_ids = entry?.entity_ids ?? [entityID]
        do { if await writer.save(command, client: try model.client()) { dismiss(); await model.refreshStatus() } }
        catch { self.error = L10n.error(error) }
    }
}

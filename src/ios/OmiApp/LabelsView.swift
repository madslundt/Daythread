import SwiftUI
import OmiCore

struct LabelsView: View {
    @Environment(AppModel.self) private var model
    @State private var directory: LabelDirectory?
    @State private var query = ""
    @State private var error: String?
    @State private var creating = false
    private var filteredLabels: [MemoryLabel] {
        (directory?.items ?? []).filter { query.isEmpty || ([$0.name] + $0.aliases).contains(where: { $0.localizedCaseInsensitiveContains(query) }) }
    }
    var body: some View {
        List {
            Section {
                EditorialTitle(eyebrow: "Your library", title: "People & projects.", subtitle: "Keep related conversations close together.").editorialRow()
            }
            Section {
                Button { creating = true } label: { Label("New person or project", systemImage: "plus") }.buttonStyle(PrimaryActionStyle())
            } footer: { Text("Names and aliases can match more than one person. All source associations are chosen by you.") }
            Section("Your labels") {
                ForEach(filteredLabels) { entity in
                    NavigationLink { LabelTimelineView(entityID: entity.id) } label: {
                        VStack(alignment: .leading) {
                            Text(entity.name).font(.headline)
                            Text(entity.kind.capitalized + (entity.description.isEmpty ? "" : " · " + entity.description)).font(.caption).foregroundStyle(.secondary)
                            if !entity.aliases.isEmpty { Text(entity.aliases.joined(separator: " · ")).font(.caption) }
                        }
                    }
                }
                if directory?.items.isEmpty == true { Text("Create a label, then associate recordings from their source details.").foregroundStyle(.secondary) }
                else if directory != nil && filteredLabels.isEmpty { ContentUnavailableView.search(text: query) }
            }
            ErrorNote(message: error)
        }.listStyle(.insetGrouped).omiScreen().navigationTitle("People & projects").navigationBarTitleDisplayMode(.inline).searchable(text: $query, prompt: "Find a person or project")
            .task(id: model.backendAddress + model.labelRevision) { await load() }.refreshable { await load() }
            .sheet(isPresented: $creating) {
                NavigationStack { LabelEditView(entity: nil, revision: directory?.revision ?? "", onSaved: { Task { await load() } }) }
            }
    }
    private func load() async {
        let address = model.backendAddress
        do {
            let result: LabelDirectory = try await model.client().labels()
            guard address == model.backendAddress, !Task.isCancelled else { return }
            directory = result; error = nil
        } catch { if address == model.backendAddress { self.error = error.localizedDescription } }
    }
}

struct LabelTimelineView: View {
    let entityID: String
    @Environment(AppModel.self) private var model
    @State private var response: LabelTimelineResponse?
    @State private var entries: [LabelTimelineEntry] = []
    @State private var start = ""
    @State private var end = ""
    @State private var error: String?
    @State private var loading = false
    @State private var epoch = RequestEpoch()
    @State private var editing: LabelEditSelection?
    @State private var annotation: AnnotationSelection?
    @State private var writer = LabelWriter()
    var body: some View {
        List {
            if let data = response {
                Section {
                    Text(data.entity.name).font(.title2)
                    Text(data.entity.description)
                    Text(data.entity.aliases.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                    Button("Edit label") { editing = LabelEditSelection(entity: data.entity, revision: data.revision) }
                    NavigationLink("Associate existing decision register record") { LabelDecisionLinkView(entityID: data.entity.id, revision: data.revision) }
                    NavigationLink("Merge / split / delete") { LabelManagementView(entityID: data.entity.id) }
                    Button("Group conversation / add decision or question") { annotation = AnnotationSelection(entry: nil, entityID: data.entity.id, revision: data.revision) }
                }
            }
            Section("Dates in " + (model.serverTimeZoneIdentifier ?? "server calendar")) {
                TextField("From · YYYY-MM-DD", text: $start).keyboardType(.numbersAndPunctuation)
                TextField("Through · YYYY-MM-DD", text: $end).keyboardType(.numbersAndPunctuation)
                Button("Apply dates") { Task { await load() } }.disabled(loading)
                Button("All dates") { start = ""; end = ""; Task { await load() } }
            }
            ForEach(entries) { entry in
                Section(entry.entry_type == "decision" ? "Decision register" : entry.entry_type == "note" ? annotationMeaning(entry.kind ?? "") : associationMeaning(entry.relation ?? "")) {
                    if let title = entry.title { Text(title).font(.headline) }
                    if let decision = entry.decision {
                        Text("\(decision.review_status) · evidence \(decision.evidence_condition)").font(.caption)
                        NavigationLink("Open register record and exact evidence") { DecisionDetail(id: decision.id) }
                    }
                    if let body = entry.body, !body.isEmpty { Text(body).textSelection(.enabled) }
                    if entry.needs_review { Label("Needs review · transcript changed", systemImage: "exclamationmark.circle").foregroundStyle(.orange) }
                    SourceList(sources: entry.sources.map(\.json))
                    if entry.entry_type == "note" { Button("Edit / review annotation") { annotation = AnnotationSelection(entry: entry, entityID: response!.entity.id, revision: response!.revision) } }
                    else if entry.needs_review { Button("Confirm association after reviewing sources") { Task { await confirm(entry) } }.disabled(writer.busy) }
                    Button(entry.entry_type == "note" ? "Delete annotation" : "Detach association", role: .destructive) { Task { await remove(entry) } }.disabled(writer.busy)
                }
            }
            if entries.isEmpty && !loading { Text("No linked evidence in these dates. Add associations from source details or select evidence in a new annotation.").foregroundStyle(.secondary) }
            if let response, entries.count < response.total { Button("Load more · \(entries.count) of \(response.total)") { Task { await load(more: true) } }.disabled(loading) }
            if loading { ProgressView("Reading timeline…") }
            ErrorNote(message: error ?? writer.error)
            Text("Recordings retain source-level precision. Imported Omi speaker names and numbers are unverified and do not establish identity across sources.").font(.caption).foregroundStyle(.secondary)
        }.omiScreen().navigationTitle("Timeline").navigationBarTitleDisplayMode(.inline)
            .task(id: model.backendAddress + model.labelRevision) { await load() }.refreshable { await load() }
            .sheet(item: $editing, onDismiss: { Task { await load() } }) { selection in
                NavigationStack { LabelEditView(entity: selection.entity, revision: selection.revision, onSaved: {}) }
            }
            .sheet(item: $annotation, onDismiss: { Task { await load() } }) { selection in
                NavigationStack { LabelNoteEditor(entityID: selection.entityID, revision: selection.revision, entry: selection.entry) }
            }
    }
    private func load(more: Bool = false) async {
        guard !more || !loading else { return }
        let token = epoch.replace()
        if !more { response = nil; entries = [] }
        let address = model.backendAddress
        loading = true; error = nil
        defer { if epoch.accepts(token) { loading = false } }
        do {
            var query = ["limit": "50"]
            if !start.isEmpty { query["start_day"] = start }; if !end.isEmpty { query["end_day"] = end }
            if more, let response { query["offset"] = String(entries.count); query["revision"] = response.revision }
            let result: LabelTimelineResponse = try await model.client().labels("v1/labels/\(entityID)/timeline", query: query)
            guard address == model.backendAddress, !Task.isCancelled, epoch.accepts(token) else { return }
            response = result; entries = more ? entries + result.items : result.items
        } catch { if address == model.backendAddress, epoch.accepts(token), !Task.isCancelled { self.error = error.localizedDescription; if !more { response = nil; entries = [] } } }
    }
    private func confirm(_ entry: LabelTimelineEntry) async {
        guard let response, let source = entry.sources.first else { return }
        var command = LabelCommand(action: "link.save", id: entry.id, expected_revision: response.revision)
        command.entity_id = response.entity.id
        if let decision = entry.decision {
            command.action = "decision_link.save"; command.decision_id = decision.id; command.decision_version = decision.version
        } else { command.source = source.ref; command.relation = entry.relation }
        do { if await writer.save(command, client: try model.client()) { await load(); await model.refreshStatus() } }
        catch { self.error = error.localizedDescription }
    }
    private func remove(_ entry: LabelTimelineEntry) async {
        guard let response else { return }
        let command = LabelCommand(action: entry.entry_type == "decision" ? "decision_link.delete" : entry.entry_type == "note" ? "note.delete" : "link.delete", id: entry.id, expected_revision: response.revision)
        do { if await writer.save(command, client: try model.client()) { await load(); await model.refreshStatus() } }
        catch { self.error = error.localizedDescription }
    }
}
struct LabelEditSelection: Identifiable { let id = UUID(); let entity: MemoryLabel; let revision: String }
struct AnnotationSelection: Identifiable { let id = UUID(); let entry: LabelTimelineEntry?; let entityID: String; let revision: String }

struct LabelDraftFields: View {
    @Binding var draft: LabelDraft
    let fixedKind: Bool
    var body: some View {
        Picker("Kind", selection: $draft.kind) { Text("Person").tag("person"); Text("Project").tag("project") }.disabled(fixedKind)
        TextField("Name", text: $draft.name)
        TextField("Distinguishing description", text: $draft.description, axis: .vertical)
        TextField("Aliases · one per line", text: Binding(get: { draft.aliases.joined(separator: "\n") }, set: { draft.aliases = $0.components(separatedBy: "\n") }), axis: .vertical)
        Text("On rename, add the former name as an alias only if you want to keep it. Identical names and aliases are allowed.").font(.caption).foregroundStyle(.secondary)
    }
}
struct LabelEditView: View {
    let entity: MemoryLabel?
    let revision: String
    let onSaved: () -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var draft = LabelDraft()
    @State private var id = UUID().uuidString.lowercased()
    @State private var writer = LabelWriter()
    @State private var currentRevision = ""
    @State private var error: String?
    var body: some View {
        Form {
            LabelDraftFields(draft: $draft, fixedKind: entity != nil)
            Text("Metadata edits require a connection. Failed saves keep your draft on this screen.").font(.caption)
            ErrorNote(message: error ?? writer.error)
            Button("Reload metadata version before reviewing draft") { Task { await refreshRevision() } }
        }.omiScreen().navigationTitle(entity == nil ? "New label" : "Edit label")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(writer.busy) }
                ToolbarItem(placement: .confirmationAction) { Button("Save") { Task { await save() } }.disabled(writer.busy || draft.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || currentRevision.isEmpty) }
            }
            .onAppear { draft = entity?.draft ?? LabelDraft(); id = entity?.id ?? id; currentRevision = revision }
    }
    private func refreshRevision() async {
        do { let directory: LabelDirectory = try await model.client().labels(); currentRevision = directory.revision; error = nil }
        catch { self.error = error.localizedDescription }
    }
    private func save() async {
        var clean = draft; clean.aliases = draft.aliases.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        var command = LabelCommand(action: "entity.save", id: id, expected_revision: currentRevision); command.create = entity == nil; command.value = clean
        do { if await writer.save(command, client: try model.client()) { onSaved(); dismiss(); await model.refreshStatus() } }
        catch { self.error = error.localizedDescription }
    }
}

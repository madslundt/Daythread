import SwiftUI

/// All preparation state belongs to this sheet; it never enters LocalCache.
struct ConversationPrepView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @State private var search = PrepSearch()
    @State private var aliases = ""
    @State private var context = ""
    @State private var from = ""
    @State private var through = ""
    @State private var found: PrepSearchResult?
    @State private var selected: [PrepSource] = []
    @State private var latest = ""
    @State private var result: PrepResult?
    @State private var source: SourceSelection?
    @State private var error: String?
    @State private var busy = false
    @State private var checking = false
    @State private var discard = false
    @State private var requestID = UUID()
    @State private var task: Task<Void, Never>?
    private var hasDraft: Bool { !search.subject.isEmpty || !context.isEmpty || !aliases.isEmpty || !search.qualifier.isEmpty || !from.isEmpty || !through.isEmpty }
    private var zone: TimeZone { TimeZone(identifier: found?.timezone ?? model.serverTimeZoneIdentifier ?? "") ?? .current }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            List {
                Section {
                    Text(L10n.text("Find recordings, confirm relevance, then prepare a cited brief. A mention does not verify participation or identity."))
                    Picker(L10n.text("Subject type"), selection: $search.kind) {
                        Text(L10n.text("Person")).tag("person"); Text(L10n.text("Project")).tag("project"); Text(L10n.text("Topic")).tag("topic")
                    }
                    TextField(L10n.text("Name or literal phrase"), text: $search.subject).autocorrectionDisabled()
                    TextField(L10n.text("Alternate names · one per line"), text: $aliases, axis: .vertical).lineLimit(2...4)
                    TextField(L10n.text("Qualifier · must also appear"), text: $search.qualifier)
                    TextField(L10n.text("From · YYYY-MM-DD (optional)"), text: $from).autocorrectionDisabled()
                    TextField(L10n.text("Through · YYYY-MM-DD (optional)"), text: $through).autocorrectionDisabled()
                    Text(L10n.text("Both dates or neither, inclusive in \(zone.identifier). All retained dates by default. Literal search can miss unnamed discussions; add alternate phrases or browse Rewind.")).font(.caption).foregroundStyle(.secondary)
                    Button(L10n.text("Find discussions")) { launch { await find(offset: 0) } }.disabled(busy || search.subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                Section(L10n.text("My context and goals · user-authored")) {
                    TextEditor(text: $context).frame(minHeight: 100).accessibilityLabel(L10n.text("My context and goals"))
                    Text(L10n.text("Not recorded evidence. This draft stays in this screen only and is sent separately to your local model.")).font(.caption).foregroundStyle(.secondary)
                }
                if busy { Section { ProgressView(checking ? L10n.text("Checking current evidence…") : L10n.text("Working…")); Button(L10n.text("Cancel request")) { cancel() } } }
                if let error { Section { ErrorNote(message: error) } }
                if let found, !checking {
                    Section(L10n.text("Matching recordings · \(found.coverage.total_matches)")) {
                        Text(L10n.text("\(found.coverage.pending_chunks) pending chunks · \(found.coverage.capture_gaps) known gaps in searched dates. Uploads not received are unknown. This page omits \(found.coverage.omitted_matches) matches.")).font(.caption)
                        if found.sources.isEmpty { Text(L10n.text("No matching transcribed recordings. Try alternate names or different dates.")) }
                        ForEach(found.sources) { row in candidate(row) }
                        HStack {
                            Button(L10n.text("Previous")) { launch { await find(offset: max(0, found.search.offset - 10)) } }.disabled(busy || found.search.offset == 0)
                            Spacer()
                            Button(L10n.text("Next")) { launch { await find(offset: found.search.offset + 10) } }.disabled(busy || found.search.offset + 10 >= found.coverage.total_matches)
                        }
                    }
                    Section(L10n.text("Confirm latest relevant recording")) {
                        Picker(L10n.text("Latest recording"), selection: $latest) {
                            Text(L10n.text("Choose after checking evidence")).tag("")
                            ForEach(selected) { row in Text(label(row.started_at) + " · " + row.text.prefix(60)).tag(row.id) }
                        }
                        Text(L10n.text("\(selected.count) selected. Excerpts are bounded to 4,000 characters each and 16,000 total; select fewer if exceeded.")).font(.caption)
                        Button(L10n.text("Prepare from confirmed evidence")) { launch { await prepare() } }.disabled(busy || latest.isEmpty || selected.isEmpty)
                    }
                }
                if let result, !checking {
                    Section { Text(L10n.text("Generated \(label(result.generated_at)) · \(result.model). Evidence checked \(label(result.as_of)). Interpretation of selected excerpts; review citations.")).font(.caption) }
                    items(L10n.text("Latest confirmed recording"), result.latest_discussion, result)
                    items(L10n.text("Promises to check · completion and owner unknown"), result.promises_to_check, result)
                    items(L10n.text("Recorded questions"), result.recorded_questions, result)
                    items(L10n.text("Suggested questions · proposals based on evidence"), result.suggested_questions, result)
                    Section(L10n.text("Evidence coverage and limits")) {
                        ForEach(result.coverage.limitations, id: \.self) { Text($0).font(.caption) }
                        Text(L10n.text("\(result.coverage.omitted_matches) matching recordings not selected.")).font(.caption)
                        ForEach(result.reviewed_excerpts) { excerpt in
                            DisclosureGroup(label(excerpt.started_at) + L10n.text(" · reviewed excerpt · \(excerpt.omitted_characters) characters omitted")) {
                                Text(excerpt.text).font(.caption).textSelection(.enabled)
                            }
                        }
                    }
                }
            }.omiScreen().navigationTitle(L10n.text("Prepare")).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L10n.text("Close")) { if hasDraft { discard = true } else { dismiss() } } } }
                .interactiveDismissDisabled(hasDraft)
                .confirmationDialog(L10n.text("Discard preparation and unsaved context?"), isPresented: $discard, titleVisibility: .visible) { Button(L10n.text("Discard"), role: .destructive) { dismiss() } }
                .sheet(item: $source) { selection in SourceDetail(source: selection.source, onCorrect: { changedEvidence() }) }
                .onChange(of: search) { _, _ in invalidate() }
                .onChange(of: aliases) { _, _ in invalidate() }
                .onChange(of: from) { _, _ in invalidate() }
                .onChange(of: through) { _, _ in invalidate() }
                .onChange(of: context) { _, _ in cancel(); result = nil }
                .onChange(of: latest) { _, _ in cancel(); result = nil }
                .onChange(of: model.archiveIdentity) { _, _ in changedEvidence() }
                .onChange(of: model.serverTimeZoneIdentifier) { _, _ in launch { await validate() } }
                .onChange(of: scenePhase) { _, phase in if phase == .active { launch { await validate() } } }
                .onDisappear { cancel() }
        }
    }

    private func label(_ stamp: String) -> String {
        guard let date = AppModel.date(stamp) else { return stamp }
        let format = DateFormatter(); format.locale = L10n.locale; format.timeZone = zone; format.dateStyle = .medium; format.timeStyle = .short
        return format.string(from: date)
    }
    private func candidate(_ row: PrepSource) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle(isOn: Binding(get: { selected.contains { $0.id == row.id } }, set: { include in
                cancel(); result = nil
                if include { selected.append(row) } else { selected.removeAll { $0.id == row.id }; if latest == row.id { latest = "" } }
            })) { Text(L10n.text("Include ") + label(row.started_at)) }
                .disabled(busy || (selected.count >= 40 && !selected.contains { $0.id == row.id }))
            if let date = AppModel.date(row.started_at), Date().timeIntervalSince(date) > 30 * 86400 { Text(L10n.text("Over 30 days old")).font(.caption).foregroundStyle(.secondary) }
            Text(row.text).lineLimit(5)
            Button(L10n.text("Inspect transcript and audio")) { source = SourceSelection(source: row.json) }
        }
    }
    private func items(_ title: String, _ items: [PrepItem], _ result: PrepResult) -> some View {
        Section(title) {
            if items.isEmpty { Text(L10n.text("No supported items in reviewed excerpts.")).foregroundStyle(.secondary) }
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                VStack(alignment: .leading, spacing: 8) {
                    Text(item.text).textSelection(.enabled)
                    ForEach(Array(item.citations.enumerated()), id: \.offset) { _, citation in
                        Button { if let row = result.sources.first(where: { $0.id == citation.source_id }) { source = SourceSelection(source: row.json) } } label: {
                            Text(label(citation.started_at) + " · “" + citation.excerpt + "”").font(.caption).multilineTextAlignment(.leading)
                        }
                    }
                }
            }
        }
    }
    private func cancel() { task?.cancel(); requestID = UUID(); busy = false; checking = false }
    private func invalidate() { cancel(); found = nil; selected = []; latest = ""; result = nil; source = nil; error = nil }
    private func changedEvidence() { invalidate(); error = L10n.text("Evidence or backend changed. Find discussions again; your draft is retained.") }
    private func launch(_ action: @escaping @MainActor () async -> Void) { cancel(); task = Task { await action() } }
    private func perform(_ operation: (BackendClient) async throws -> Void) async {
        let id = UUID(), address = model.archiveIdentity
        requestID = id; busy = true; error = nil
        defer { if requestID == id { busy = false; checking = false } }
        do { try await operation(model.client()) }
        catch {
            guard !Task.isCancelled, requestID == id, model.archiveIdentity == address else { return }
            self.error = L10n.error(error)
            if let failure = error as? BackendFailure, failure.status == 409 { found = nil; result = nil; selected = []; latest = "" }
        }
    }
    private func find(offset: Int) async {
        result = nil; source = nil
        var query = search
        query.aliases = aliases.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        query.start_day = from.isEmpty ? nil : from; query.end_day = through.isEmpty ? nil : through; query.offset = offset
        await perform { client in
            let id = requestID, address = model.archiveIdentity
            let value: PrepSearchResult = try await client.prep("search", body: query)
            guard !Task.isCancelled, requestID == id, model.archiveIdentity == address else { return }
            if found?.snapshot != value.snapshot { selected = []; latest = "" }
            found = value
        }
    }
    private func prepare() async {
        guard let found else { return }
        result = nil
        let body = PrepGenerate(search: found.search, snapshot: found.snapshot, selected: selected.map { PrepSelection(id: $0.id, version: $0.version) }, latest_source_id: latest, user_context: context)
        await perform { client in
            let id = requestID, address = model.archiveIdentity
            let value: PrepResult = try await client.prep("generate", body: body)
            guard !Task.isCancelled, requestID == id, model.archiveIdentity == address else { return }
            result = value
        }
    }
    private func validate() async {
        guard let found else { return }
        source = nil
        await perform { client in
            checking = true
            let id = requestID, address = model.archiveIdentity
            do {
                let value: PrepValidity = try await client.prep("validate", body: PrepValidation(search: found.search, snapshot: found.snapshot))
                guard !Task.isCancelled, requestID == id, model.archiveIdentity == address else { return }
                if !value.valid { changedEvidence() }
            } catch {
                guard !Task.isCancelled, requestID == id, model.archiveIdentity == address else { return }
                changedEvidence(); self.error = L10n.text("Could not verify current evidence. Find discussions again. \(L10n.error(error))")
            }
        }
    }
}

import OmiCore
import SwiftUI

/// Reviewed interpretations stay separate from transcript-backed timeline entries.
struct LabelFactsSection: View {
    let entityID: String
    let revision: String
    @Environment(AppModel.self) private var model
    @State private var current: LabelTimelineResponse?
    @State private var history: LabelTimelineResponse?
    @State private var currentEntries: [LabelTimelineEntry] = []
    @State private var historyEntries: [LabelTimelineEntry] = []
    @State private var showHistory = false
    @State private var editing: FactSelection?
    @State private var forgetting: LabelTimelineEntry?
    @State private var currentEpoch = RequestEpoch()
    @State private var historyEpoch = RequestEpoch()
    @State private var writer = LabelWriter()
    @State private var error: String?
    @State private var busy = false

    private struct FactSelection: Identifiable {
        let id = UUID()
        let entry: LabelTimelineEntry?
        let revision: String
    }

    var body: some View {
        Section(L10n.text("Persistent facts")) {
            Text(L10n.text("Reviewed facts are your interpretation, kept separately from original transcript evidence. Source changes require another review."))
                .font(.caption).foregroundStyle(.secondary)
            Button(L10n.text("Add a reviewed fact")) {
                editing = FactSelection(entry: nil, revision: current?.revision ?? revision)
            }
            ForEach(currentEntries) { factRow($0, revision: current?.revision ?? revision) }
            if current != nil && currentEntries.isEmpty { Text(L10n.text("No current reviewed facts.")) }
            if let current, currentEntries.count < current.total {
                Button(L10n.text("Load more")) { Task { await load(more: true) } }.disabled(busy)
            }
            DisclosureGroup(L10n.text("Fact history"), isExpanded: $showHistory) {
                ForEach(historyEntries) { factRow($0, revision: history?.revision ?? revision) }
                if history != nil && historyEntries.isEmpty { Text(L10n.text("No fact history yet.")) }
                if let history, historyEntries.count < history.total {
                    Button(L10n.text("Load more")) { Task { await load(history: true, more: true) } }.disabled(busy)
                }
            }
            ErrorNote(message: error ?? writer.error)
        }
        .task(id: model.archiveIdentity + revision) {
            await load()
            if showHistory { await load(history: true) }
        }
        .task(id: showHistory) { if showHistory { await load(history: true) } }
        .sheet(item: $editing, onDismiss: { Task { await refresh() } }) { selection in
            NavigationStack {
                LabelNoteEditor(entityID: entityID, revision: selection.revision,
                                entry: selection.entry, fact: true)
            }
        }
        .confirmationDialog(L10n.text("Forget this fact and its entire replacement history? Recordings are kept."),
                            isPresented: Binding(get: { forgetting != nil }, set: { if !$0 { forgetting = nil } })) {
            Button(L10n.text("Forget fact history"), role: .destructive) {
                guard let entry = forgetting else { return }
                let version = entry.fact?.status == "confirmed" ? current?.revision : history?.revision
                Task { await mutate("note.delete", entry: entry, revision: version ?? revision) }
                forgetting = nil
            }
        }
    }

    private func factRow(_ entry: LabelTimelineEntry, revision: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(entry.fact?.status == "superseded" ? L10n.text("Replaced fact") :
                 entry.fact?.status == "rejected" ? L10n.text("Rejected fact") : L10n.text("Confirmed by you"))
                .font(.caption).foregroundStyle(.secondary)
            Text(entry.title ?? "").font(.headline)
            if let body = entry.body, !body.isEmpty { Text(body).textSelection(.enabled) }
            if entry.needs_review { Text(L10n.text("Needs review · transcript changed")).foregroundStyle(.orange) }
            SourceList(sources: entry.sources.map(\.json))
            if entry.fact?.status != "superseded" {
                Button(L10n.text("Review and replace fact")) { editing = FactSelection(entry: entry, revision: revision) }
            }
            if entry.fact?.status == "confirmed" {
                Button(L10n.text("Reject fact")) { Task { await mutate("fact.reject", entry: entry, revision: revision) } }
                    .disabled(writer.busy)
            }
            Button(L10n.text("Forget fact history"), role: .destructive) { forgetting = entry }.disabled(writer.busy)
        }
    }

    private func load(history isHistory: Bool = false, more: Bool = false) async {
        let address = model.archiveIdentity
        let token = isHistory ? historyEpoch.replace() : currentEpoch.replace()
        let snapshot = isHistory ? history : current
        let entries = isHistory ? historyEntries : currentEntries
        busy = true
        defer { busy = false }
        do {
            var query = ["scope": isHistory ? "history" : "current", "limit": "50"]
            if more, let snapshot {
                query["revision"] = snapshot.revision; query["offset"] = String(entries.count)
            }
            let result: LabelTimelineResponse = try await model.client().labels("v1/labels/\(entityID)/facts", query: query)
            guard address == model.archiveIdentity, !Task.isCancelled,
                  isHistory ? historyEpoch.accepts(token) : currentEpoch.accepts(token) else { return }
            if isHistory { history = result; historyEntries = more ? entries + result.items : result.items }
            else { current = result; currentEntries = more ? entries + result.items : result.items }
            error = nil
        } catch { if address == model.archiveIdentity, !Task.isCancelled { self.error = L10n.error(error) } }
    }

    private func mutate(_ action: String, entry: LabelTimelineEntry, revision: String) async {
        do {
            if await writer.save(LabelCommand(action: action, id: entry.id, expected_revision: revision), client: try model.client()) {
                await refresh(); await model.refreshStatus()
            }
        } catch { self.error = L10n.error(error) }
    }

    private func refresh() async {
        await load()
        if showHistory { await load(history: true) }
    }
}

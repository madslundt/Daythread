import SwiftUI
import OmiCore

struct DecisionsSection: View {
    let day: String
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @State private var items: [DecisionRecord] = []
    @State private var total = 0
    @State private var error: String?
    @State private var busy = false
    @State private var allDays = false
    @State private var query = ""
    @State private var submitted = ""
    @State private var review = ""
    @State private var revision = 0
    @State private var requestID = UUID()

    var body: some View {
        Section("Decisions") {
            Text("Decisions are found automatically from new transcripts. Review original evidence before confirming a record. Confirmation records accuracy; it does not make a tentative choice final.")
                .font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("Search & filter") {
                TextField("Search decisions", text: $query).submitLabel(.search)
                    .onSubmit { submitted = String(query.prefix(200)) }
                    .accessibilityIdentifier("decisions.search")
                Button("Search") { submitted = String(query.prefix(200)) }.disabled(busy)
                Toggle("Across all days", isOn: $allDays).accessibilityIdentifier("decisions.allDays")
                Picker("Review status", selection: $review) {
                    Text("Active records").tag("")
                    Text("Proposed").tag("proposed")
                    Text("Confirmed").tag("confirmed")
                    Text("Dismissed").tag("dismissed")
                }.accessibilityIdentifier("decisions.reviewStatus")
            }
            ForEach(items) { item in
                NavigationLink { DecisionDetail(id: item.id) } label: { DecisionRow(record: item) }
            }
            Text("Showing \(items.count) of \(total) records").font(.caption).foregroundStyle(.secondary)
            if items.count < total {
                Button("Load more decisions") { Task { await loadMore() } }.disabled(busy)
            }
            if items.isEmpty && !busy { Text(allDays || !submitted.isEmpty || !review.isEmpty ? "No matching decision records." : "No decision records for this day.").foregroundStyle(.secondary) }
            Button("Refresh decisions") { revision += 1 }.disabled(busy)
            if busy { ProgressView("Reading decisions…") }
            ErrorNote(message: error)
        }
        .task(id: "\(model.archiveIdentity).\(model.archiveRevision).\(day).\(allDays).\(submitted).\(review).\(revision)") { await load() }
        .task(id: model.archiveIdentity + day + ".automatic-decisions") {
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                guard !Task.isCancelled else { return }
                if !busy { revision += 1 }
            }
        }
        .onChange(of: phase) { _, value in
            if value == .active { revision += 1 }
            else { requestID = UUID(); items = [] }
        }
        .onDisappear { requestID = UUID(); items = [] }
    }
    private func filters(offset: Int) -> [String: String] {
        var result = ["q": submitted, "include_dismissed": review == "dismissed" ? "true" : "false", "limit": "50", "offset": String(offset)]
        if !allDays { result["day"] = day }
        if !review.isEmpty { result["review_status"] = review }
        return result
    }
    private func load() async {
        let token = UUID(); requestID = token; busy = true; error = nil; items = []; total = 0
        defer { if requestID == token { busy = false } }
        do {
            let result: DecisionList = try await model.client().decisionRequest("v1/decisions", query: filters(offset: 0))
            guard !Task.isCancelled, requestID == token else { return }
            items = result.items; total = result.total
        } catch { if !Task.isCancelled, requestID == token { self.error = error.localizedDescription } }
    }
    private func loadMore() async {
        guard !busy, items.count < total else { return }
        let token = requestID; busy = true; error = nil
        defer { if requestID == token { busy = false } }
        do {
            let result: DecisionList = try await model.client().decisionRequest("v1/decisions", query: filters(offset: items.count))
            guard !Task.isCancelled, requestID == token else { return }
            let existing = Set(items.map(\.id))
            items.append(contentsOf: result.items.filter { !existing.contains($0.id) }); total = result.total
        } catch { if !Task.isCancelled, requestID == token { self.error = error.localizedDescription } }
    }

}

struct DecisionRow: View {
    let record: DecisionRecord
    @Environment(AppModel.self) private var model
    private var dateLabel: String {
        guard let date = AppModel.date(record.started_at) else { return record.started_at }
        return DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current).label(date, template: "d MMM yyyy HHmm")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(record.title).foregroundStyle(.primary)
            Text("\(record.display_state.capitalized) · \(record.review_status) · evidence \(record.evidence_condition)")
                .font(.caption).foregroundStyle(.secondary)
            Text(dateLabel).font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct DecisionDetail: View {
    let id: String
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var phase
    @State private var record: DecisionRecord?
    @State private var error: String?
    @State private var busy = false
    @State private var editor: DecisionEditSelection?
    @State private var followUpActionActive = false
    @State private var savedFollowUps: Set<String> = []
    @State private var predecessor: DecisionRecord?
    @State private var choosingPredecessor = false
    @State private var deleting = false
    @State private var requestID = UUID()
    @State private var actionKey = ""
    @State private var commandID = UUID().uuidString
    var body: some View {
        List {
            if let record {
                Section {
                    VStack(alignment: .leading, spacing: 10) {
                        DecisionRow(record: record)
                        if record.canSuggestFollowUps {
                            DecisionFollowUpLinks(record: record, saved: $savedFollowUps, nativeActionActive: $followUpActionActive)
                                .id(model.archiveIdentity + model.archiveRevision + record.id + "\(record.version)")
                        }
                    }
                }
                if let content = record.content {
                    DecisionClaims(content: content, historical: record.evidence_condition != "current")
                    if !content.owner_notes.isEmpty {
                        Section("Owner note · not recorded speech") { Text(content.owner_notes).textSelection(.enabled) }
                    }
                    Section("Review") {
                        Text("Confirm that the record represents its sources. Missing rationale must remain unstated.").font(.caption).foregroundStyle(.secondary)
                        Button("Edit record and evidence") { editor = DecisionEditSelection(record: record) }
                        if record.review_status != "confirmed" && record.predecessor_id == nil && record.successor_id == nil {
                            Button(predecessor == nil ? "Choose earlier decision this revises…" : "Revises: \(predecessor!.title)") { choosingPredecessor = true }
                            if predecessor != nil { Button("Clear earlier decision") { predecessor = nil } }
                        }
                        if record.review_status != "confirmed" {
                            Button(predecessor == nil ? "Confirm accuracy" : "Confirm accuracy and revision") { Task { await event("confirm") } }
                                .disabled(record.evidence_condition != "current")
                        }
                        if record.review_status != "confirmed" && record.predecessor_id == nil && record.successor_id == nil {
                            Button("Dismiss proposal") { Task { await event("dismiss") } }
                        }
                        Button(record.evidence_condition == "conflicted" ? "Resolve conflict after review" : "Mark contradictory evidence") {
                            Task { await event(record.evidence_condition == "conflicted" ? "resolve" : "conflict") }
                        }.disabled(record.evidence_condition == "stale")
                        Button("Delete record", role: .destructive) { deleting = true }
                    }.disabled(busy)
                }
                Section("Decision thread") {
                    if let previous = record.predecessor_id { NavigationLink("Earlier decision") { DecisionDetail(id: previous) } }
                    if let next = record.successor_id { NavigationLink("Later revision") { DecisionDetail(id: next) } }
                    Text(record.thread_id).font(.caption.monospaced()).textSelection(.enabled)
                }
                Section("History") {
                    ForEach(Array((record.history ?? []).enumerated()), id: \.offset) { _, entry in
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Version \(entry.version) · \(entry.event)").font(.headline)
                            Text(entry.created_at).font(.caption).foregroundStyle(.secondary)
                            if let content = entry.content {
                                NavigationLink(content.choice.text) {
                                    List {
                                        Text("Historical version · may no longer reflect current evidence").font(.caption).foregroundStyle(.secondary)
                                        DecisionClaims(content: content, historical: true)
                                        if !content.owner_notes.isEmpty { Section("Historical owner note") { Text(content.owner_notes) } }
                                    }.navigationTitle("Version \(entry.version)")
                                }
                            }
                        }
                    }
                }
            }
            if busy { ProgressView("Reading decision…") }
            ErrorNote(message: error)
            Button("Refresh record") { Task { await load() } }.disabled(busy)
        }
        .navigationTitle("Decision").navigationBarTitleDisplayMode(.inline)
        .task(id: model.archiveIdentity + model.archiveRevision + id) { await load() }
        .refreshable { await load() }
        .sheet(item: $editor, onDismiss: { Task { await load() } }) { selection in
            NavigationStack { DecisionEditor(record: selection.record, source: nil) }
        }
        .sheet(isPresented: $choosingPredecessor) { NavigationStack { DecisionPredecessorPicker(excluding: id, before: record?.started_at ?? "", selection: $predecessor) } }
        .confirmationDialog("Delete this decision and its content history?", isPresented: $deleting, titleVisibility: .visible) {
            Button("Delete decision", role: .destructive) { Task { await delete() } }
        }
        .onChange(of: phase) { _, value in
            if value == .active, !followUpActionActive { Task { await load() } }
            else if value == .background { requestID = UUID(); record = nil; predecessor = nil; editor = nil; followUpActionActive = false; choosingPredecessor = false }
        }
        .onChange(of: model.archiveIdentity) { _, _ in followUpActionActive = false; savedFollowUps = [] }
        .onChange(of: model.archiveRevision) { _, _ in followUpActionActive = false; savedFollowUps = [] }
        .onDisappear { requestID = UUID(); record = nil; predecessor = nil }
    }
    private func load() async {
        let token = UUID(); requestID = token; busy = true; error = nil; record = nil; predecessor = nil
        defer { if requestID == token { busy = false } }
        do {
            let result: DecisionRecord = try await model.client().decisionRequest("v1/decisions/\(id)")
            guard !Task.isCancelled, requestID == token else { return }
            record = result
        } catch { if !Task.isCancelled, requestID == token { self.error = error.localizedDescription } }
    }
    private func event(_ type: String) async {
        guard let record else { return }; busy = true; error = nil
        let token = requestID
        defer { if requestID == token { busy = false } }
        do {
            let key = "\(record.version).\(type).\(predecessor?.id ?? "").\(predecessor?.version ?? 0)"
            if actionKey != key { actionKey = key; commandID = UUID().uuidString }
            var body = DecisionEvent(command_id: commandID, expected_version: record.version, type: type)
            if type == "confirm", let predecessor { body.predecessor_id = predecessor.id; body.predecessor_version = predecessor.version }
            let result: DecisionRecord = try await model.client().decisionRequest("v1/decisions/\(id)/events", method: "POST", body: body)
            guard !Task.isCancelled, requestID == token else { return }
            self.record = result; predecessor = nil
        } catch {
            if !Task.isCancelled, requestID == token {
                if (error as? BackendFailure)?.requiresDecisionReload == true { clearMutationState() }
                self.error = error.localizedDescription
            }
        }
    }
    private func clearMutationState() {
        record = nil; predecessor = nil; editor = nil; followUpActionActive = false; choosingPredecessor = false; deleting = false
    }
    private func delete() async {
        guard let record else { return }; busy = true; error = nil
        defer { busy = false }
        do { try await model.client().deleteDecision(record); dismiss() }
        catch {
            if (error as? BackendFailure)?.requiresDecisionReload == true { clearMutationState() }
            self.error = error.localizedDescription
        }
    }
}
struct DecisionEditSelection: Identifiable { let id = UUID(); let record: DecisionRecord }

struct DecisionClaims: View {
    let content: DecisionContent
    var historical = false
    var body: some View {
        claim("Choice", content.choice)
        if let context = content.context { claim("Recorded context", context) }
        if let rationale = content.rationale { claim("Recorded rationale", rationale) }
        else { Section("Recorded rationale") { Text("Not stated").foregroundStyle(.secondary) } }
        ForEach(Array(content.alternatives.enumerated()), id: \.offset) { index, alternative in claim("Discussed alternative \(index + 1)", alternative) }
        ForEach(Array(content.contradictions.enumerated()), id: \.offset) { _, opposing in claim("Contradictory evidence for \(opposing.field)", opposing.claim) }
        if !content.relationships.isEmpty {
            Section("Related labels") {
                ForEach(Array(content.relationships.enumerated()), id: \.offset) { _, relation in Text("\(relation.kind.capitalized): \(relation.label)") }
            }
        }
    }
    private func claim(_ title: String, _ claim: DecisionClaim) -> some View {
        Section(title) {
            Text(claim.text).textSelection(.enabled)
            ForEach(Array(claim.evidence.enumerated()), id: \.offset) { _, anchor in
                VStack(alignment: .leading) {
                    Text("“\(anchor.quote)”").textSelection(.enabled)
                    if historical { Text("Historical evidence · inspect the current transcript before relying on it.").font(.caption).foregroundStyle(.secondary) }
                    NavigationLink("Inspect passage in source") { DecisionEvidenceSource(anchor: anchor) }
                    Text("Source \(anchor.source_id) · characters \(anchor.start)–\(anchor.end)").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct DecisionEvidenceSource: View {
    let anchor: DecisionAnchor
    @Environment(AppModel.self) private var model
    @State private var source: JSONValue?
    @State private var error: String?
    @State private var loading = false
    private var passage: TranscriptPassage? {
        guard let source else { return nil }
        return TranscriptEvidence.passage(text: source["text"].text, sourceID: source["id"].text,
                                          sourceVersion: source["source_version"].text, expectedID: anchor.source_id,
                                          expectedVersion: anchor.source_version, start: anchor.start, end: anchor.end, quote: anchor.quote)
    }
    var body: some View {
        List {
            Section("Saved quotation") {
                Text("“\(anchor.quote)”").textSelection(.enabled)
                Text("Transcript characters \(anchor.start)–\(anchor.end)").font(.caption).foregroundStyle(.secondary)
            }
            if let source {
                Section("Current source transcript") {
                    if let passage {
                        (Text(passage.before) + Text(passage.quote).bold().foregroundColor(Theme.accent) + Text(passage.after))
                            .textSelection(.enabled)
                    } else {
                        Label("The transcript or passage changed. Edit the decision and attach current evidence before confirming.", systemImage: "exclamationmark.circle")
                            .font(.callout).foregroundStyle(.orange)
                        Text(source["text"].text.isEmpty ? "No transcript remains." : source["text"].text).textSelection(.enabled)
                    }
                    NavigationLink("Open recording, audio & correction tools") { SourceDetail(source: source) }
                }
            }
            if loading { ProgressView("Loading source…") }
            ErrorNote(message: error)
            if error != nil { Button("Try again") { Task { await load() } } }
        }.omiScreen().navigationTitle("Decision evidence").navigationBarTitleDisplayMode(.inline)
            .task(id: model.archiveIdentity + model.archiveRevision + anchor.source_id) { await load() }
            .onDisappear { source = nil }
    }
    private func load() async {
        let identity = model.archiveIdentity
        source = nil; error = nil; loading = true; defer { loading = false }
        do {
            let result: JSONValue = try await model.client().decisionRequest("v1/transcripts/\(anchor.source_id)")
            guard !Task.isCancelled, identity == model.archiveIdentity else { return }
            source = result
        } catch {
            guard !Task.isCancelled, identity == model.archiveIdentity else { return }
            self.error = error.localizedDescription
        }
    }
}

struct DecisionPredecessorPicker: View {
    let excluding: String
    let before: String
    @Binding var selection: DecisionRecord?
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var items: [DecisionRecord] = []
    @State private var error: String?
    @State private var total = 0
    @State private var busy = false
    @State private var requestID = UUID()
    var body: some View {
        List {
            Text("Select the earlier choice this decision explicitly replaces. Check the source and date; later capture alone is insufficient.").font(.caption)
            ForEach(items.filter { $0.id != excluding && $0.review_status == "confirmed" && $0.successor_id == nil && $0.evidence_condition == "current" && $0.content != nil && (AppModel.date($0.started_at) ?? .distantFuture) < (AppModel.date(before) ?? .distantPast) }) { record in
                VStack(alignment: .leading) {
                    DecisionRow(record: record)
                    NavigationLink("Inspect evidence") { DecisionDetail(id: record.id) }
                    Button("Use this earlier decision") { selection = record; dismiss() }
                }
            }
            Text("Checked \(items.count) of \(total) matching records").font(.caption).foregroundStyle(.secondary)
            if items.count < total { Button("Load more earlier decisions") { Task { await load(reset: false) } }.disabled(busy) }
            if busy { ProgressView("Finding earlier decisions…") }
            ErrorNote(message: error)
            if error != nil { Button("Retry earlier decisions") { Task { await load(reset: false) } }.disabled(busy) }
        }.navigationTitle("Earlier decision").searchable(text: $query)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            .task(id: model.archiveIdentity + query) { await load(reset: true) }
            .onDisappear { requestID = UUID(); items = []; total = 0; busy = false }
    }
    private func load(reset: Bool) async {
        guard reset || !busy else { return }
        let token = UUID(); requestID = token; busy = true; error = nil
        if reset { items = []; total = 0 }
        defer { if requestID == token { busy = false } }
        do {
            if reset { try await Task.sleep(for: .milliseconds(250)) }
            let result: DecisionList = try await model.client().decisionRequest("v1/decisions", query: ["q": query, "review_status": "confirmed", "limit": "50", "offset": "\(items.count)"])
            guard !Task.isCancelled, requestID == token else { return }
            let existing = Set(items.map(\.id))
            items.append(contentsOf: result.items.filter { !existing.contains($0.id) }); total = result.total
        } catch { if !Task.isCancelled, requestID == token { self.error = error.localizedDescription } }
    }
}

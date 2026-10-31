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

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        Section(L10n.text("Decisions")) {
            Text(L10n.text("Decisions are found automatically from new transcripts. Review original evidence before confirming a record. Confirmation records accuracy; it does not make a tentative choice final."))
                .font(.caption).foregroundStyle(.secondary)
            DisclosureGroup(L10n.text("Search & filter")) {
                TextField(L10n.text("Search decisions"), text: $query).submitLabel(.search)
                    .onSubmit { submitted = String(query.prefix(200)) }
                    .accessibilityIdentifier("decisions.search")
                Button(L10n.text("Search")) { submitted = String(query.prefix(200)) }.disabled(busy)
                Toggle(L10n.text("Across all days"), isOn: $allDays).accessibilityIdentifier("decisions.allDays")
                Picker(L10n.text("Review status"), selection: $review) {
                    Text(L10n.text("Active records")).tag("")
                    Text(L10n.text("Proposed")).tag("proposed")
                    Text(L10n.text("Confirmed")).tag("confirmed")
                    Text(L10n.text("Dismissed")).tag("dismissed")
                }.accessibilityIdentifier("decisions.reviewStatus")
            }
            ForEach(items) { item in
                NavigationLink { DecisionDetail(id: item.id) } label: { DecisionRow(record: item) }
            }
            Text(L10n.text("Showing \(items.count) of \(total) records")).font(.caption).foregroundStyle(.secondary)
            if items.count < total {
                Button(L10n.text("Load more decisions")) { Task { await loadMore() } }.disabled(busy)
            }
            if items.isEmpty && !busy { Text(allDays || !submitted.isEmpty || !review.isEmpty ? L10n.text("No matching decision records.") : L10n.text("No decision records for this day.")).foregroundStyle(.secondary) }
            Button(L10n.text("Refresh decisions")) { revision += 1 }.disabled(busy)
            if busy { ProgressView(L10n.text("Reading decisions…")) }
            ErrorNote(message: error)
        }
        .task(id: "\(model.archiveIdentity).\(model.archiveRevision).\(day).\(allDays).\(submitted).\(review).\(revision)") { await load() }
        .task(id: model.archiveIdentity + day + ".automatic-decisions." + String(describing: phase)) {
            guard phase == .active else { return }
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
        } catch { if !Task.isCancelled, requestID == token { self.error = L10n.error(error) } }
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
        } catch { if !Task.isCancelled, requestID == token { self.error = L10n.error(error) } }
    }

}

struct DecisionRow: View {
    let record: DecisionRecord
    var displayTitle: String? = nil
    var showsDate = true
    @Environment(AppModel.self) private var model
    private var dateLabel: String {
        guard let date = AppModel.date(record.started_at) else { return record.started_at }
        return DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current).label(date, template: showsDate ? "d MMM yyyy HHmm" : "HHmm")
    }
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 5) {
            Text(displayTitle ?? record.title).foregroundStyle(.primary)
            Text(L10n.text("\(L10n.status(record.display_state)) · \(L10n.status(record.review_status)) · evidence \(L10n.status(record.evidence_condition))"))
                .font(.caption).foregroundStyle(record.evidence_condition != "current" || record.review_status == "proposed" ? Color.orange : Color.secondary)
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
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
                        Section(L10n.text("Owner note · not recorded speech")) { Text(content.owner_notes).textSelection(.enabled) }
                    }
                    Section(L10n.text("Review")) {
                        Text(L10n.text("Confirm that the record represents its sources. Missing rationale must remain unstated.")).font(.caption).foregroundStyle(.secondary)
                        Button(L10n.text("Edit record and evidence")) { editor = DecisionEditSelection(record: record) }
                        if record.review_status != "confirmed" && record.predecessor_id == nil && record.successor_id == nil {
                            Button(predecessor == nil ? L10n.text("Choose earlier decision this revises…") : L10n.text("Revises: \(predecessor!.title)")) { choosingPredecessor = true }
                            if predecessor != nil { Button(L10n.text("Clear earlier decision")) { predecessor = nil } }
                        }
                        if record.review_status != "confirmed" {
                            Button(predecessor == nil ? L10n.text("Confirm accuracy") : L10n.text("Confirm accuracy and revision")) { Task { await event("confirm") } }
                                .disabled(record.evidence_condition != "current")
                        }
                        if record.review_status != "confirmed" && record.predecessor_id == nil && record.successor_id == nil {
                            Button(L10n.text("Dismiss proposal")) { Task { await event("dismiss") } }
                        }
                        Button(record.evidence_condition == "conflicted" ? L10n.text("Resolve conflict after review") : L10n.text("Mark contradictory evidence")) {
                            Task { await event(record.evidence_condition == "conflicted" ? "resolve" : "conflict") }
                        }.disabled(record.evidence_condition == "stale")
                        Button(L10n.text("Delete record"), role: .destructive) { deleting = true }
                    }.disabled(busy)
                }
                Section(L10n.text("Decision thread")) {
                    if let previous = record.predecessor_id { NavigationLink(L10n.text("Earlier decision")) { DecisionDetail(id: previous) } }
                    if let next = record.successor_id { NavigationLink(L10n.text("Later revision")) { DecisionDetail(id: next) } }
                    Text(record.thread_id).font(.caption.monospaced()).textSelection(.enabled)
                }
                Section(L10n.text("History")) {
                    ForEach(Array((record.history ?? []).enumerated()), id: \.offset) { _, entry in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(L10n.text("Version \(entry.version) · \(entry.event)")).font(.headline)
                            Text(entry.created_at).font(.caption).foregroundStyle(.secondary)
                            if let content = entry.content {
                                NavigationLink(content.choice.text) {
                                    List {
                                        Text(L10n.text("Historical version · may no longer reflect current evidence")).font(.caption).foregroundStyle(.secondary)
                                        DecisionClaims(content: content, historical: true)
                                        if !content.owner_notes.isEmpty { Section(L10n.text("Historical owner note")) { Text(content.owner_notes) } }
                                    }.navigationTitle(L10n.text("Version \(entry.version)"))
                                }
                            }
                        }
                    }
                }
            }
            if busy { ProgressView(L10n.text("Reading decision…")) }
            ErrorNote(message: error)
            Button(L10n.text("Refresh record")) { Task { await load() } }.disabled(busy)
        }
        .navigationTitle(L10n.text("Decision")).navigationBarTitleDisplayMode(.inline)
        .task(id: model.archiveIdentity + model.archiveRevision + id) { await load() }
        .refreshable { await load() }
        .sheet(item: $editor, onDismiss: { Task { await load() } }) { selection in
            NavigationStack { DecisionEditor(record: selection.record, source: nil) }
        }
        .sheet(isPresented: $choosingPredecessor) { NavigationStack { DecisionPredecessorPicker(excluding: id, before: record?.started_at ?? "", selection: $predecessor) } }
        .confirmationDialog(L10n.text("Delete this decision and its content history?"), isPresented: $deleting, titleVisibility: .visible) {
            Button(L10n.text("Delete decision"), role: .destructive) { Task { await delete() } }
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
        } catch { if !Task.isCancelled, requestID == token { self.error = L10n.error(error) } }
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
                self.error = L10n.error(error)
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
            self.error = L10n.error(error)
        }
    }
}
struct DecisionEditSelection: Identifiable { let id = UUID(); let record: DecisionRecord }

struct DecisionClaims: View {
    let content: DecisionContent
    var historical = false
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        claim(L10n.text("Choice"), content.choice)
        if let context = content.context { claim(L10n.text("Recorded context"), context) }
        if let rationale = content.rationale { claim(L10n.text("Recorded rationale"), rationale) }
        else { Section(L10n.text("Recorded rationale")) { Text(L10n.text("Not stated")).foregroundStyle(.secondary) } }
        ForEach(Array(content.alternatives.enumerated()), id: \.offset) { index, alternative in claim(L10n.text("Discussed alternative \(index + 1)"), alternative) }
        ForEach(Array(content.contradictions.enumerated()), id: \.offset) { _, opposing in claim(L10n.text("Contradictory evidence for \(opposing.field)"), opposing.claim) }
        if !content.relationships.isEmpty {
            Section(L10n.text("Related labels")) {
                ForEach(Array(content.relationships.enumerated()), id: \.offset) { _, relation in Text("\(L10n.status(relation.kind)): \(relation.label)") }
            }
        }
    }
    private func claim(_ title: String, _ claim: DecisionClaim) -> some View {
        Section(title) {
            Text(claim.text).textSelection(.enabled)
            ForEach(Array(claim.evidence.enumerated()), id: \.offset) { _, anchor in
                VStack(alignment: .leading) {
                    Text("“\(anchor.quote)”").textSelection(.enabled)
                    if historical { Text(L10n.text("Historical evidence · inspect the current transcript before relying on it.")).font(.caption).foregroundStyle(.secondary) }
                    NavigationLink(L10n.text("Inspect passage in source")) { DecisionEvidenceSource(anchor: anchor) }
                    Text(L10n.text("Source \(anchor.source_id) · characters \(anchor.start)–\(anchor.end)")).font(.caption).foregroundStyle(.secondary)
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Section(L10n.text("Saved quotation")) {
                Text("“\(anchor.quote)”").textSelection(.enabled)
                Text(L10n.text("Transcript characters \(anchor.start)–\(anchor.end)")).font(.caption).foregroundStyle(.secondary)
            }
            if let source {
                Section(L10n.text("Current source transcript")) {
                    if let passage {
                        (Text(passage.before) + Text(passage.quote).bold().foregroundColor(Theme.accent) + Text(passage.after))
                            .textSelection(.enabled)
                    } else {
                        Label(L10n.text("The transcript or passage changed. Edit the decision and attach current evidence before confirming."), systemImage: "exclamationmark.circle")
                            .font(.callout).foregroundStyle(.orange)
                        Text(source["text"].text.isEmpty ? L10n.text("No transcript remains.") : source["text"].text).textSelection(.enabled)
                    }
                    NavigationLink(L10n.text("Open recording, audio & correction tools")) { SourceDetail(source: source) }
                }
            }
            if loading { ProgressView(L10n.text("Loading source…")) }
            ErrorNote(message: error)
            if error != nil { Button(L10n.text("Try again")) { Task { await load() } } }
        }.omiScreen().navigationTitle(L10n.text("Decision evidence")).navigationBarTitleDisplayMode(.inline)
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
            self.error = L10n.error(error)
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Text(L10n.text("Select the earlier choice this decision explicitly replaces. Check the source and date; later capture alone is insufficient.")).font(.caption)
            ForEach(items.filter { $0.id != excluding && $0.review_status == "confirmed" && $0.successor_id == nil && $0.evidence_condition == "current" && $0.content != nil && (AppModel.date($0.started_at) ?? .distantFuture) < (AppModel.date(before) ?? .distantPast) }) { record in
                VStack(alignment: .leading) {
                    DecisionRow(record: record)
                    NavigationLink(L10n.text("Inspect evidence")) { DecisionDetail(id: record.id) }
                    Button(L10n.text("Use this earlier decision")) { selection = record; dismiss() }
                }
            }
            Text(L10n.text("Checked \(items.count) of \(total) matching records")).font(.caption).foregroundStyle(.secondary)
            if items.count < total { Button(L10n.text("Load more earlier decisions")) { Task { await load(reset: false) } }.disabled(busy) }
            if busy { ProgressView(L10n.text("Finding earlier decisions…")) }
            ErrorNote(message: error)
            if error != nil { Button(L10n.text("Retry earlier decisions")) { Task { await load(reset: false) } }.disabled(busy) }
        }.navigationTitle(L10n.text("Earlier decision")).searchable(text: $query)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() } } }
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
        } catch { if !Task.isCancelled, requestID == token { self.error = L10n.error(error) } }
    }
}

import SwiftUI

struct DecisionsSection: View {
    let day: String
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @State private var items: [DecisionRecord] = []
    @State private var total = 0
    @State private var extraction: DecisionExtraction?
    @State private var error: String?
    @State private var busy = false
    @State private var showDismissed = false
    @State private var revision = 0
    @State private var requestID = UUID()
    @State private var extractionRequest: DecisionExtractionRequest?

    var body: some View {
        Section("Decisions") {
            Text("Review original evidence before confirming a record. Confirmation records accuracy; it does not make a tentative choice final.")
                .font(.caption).foregroundStyle(.secondary)
            Toggle("Include dismissed", isOn: $showDismissed)
            ForEach(items) { item in
                NavigationLink { DecisionDetail(id: item.id) } label: { DecisionRow(record: item) }
            }
            Text("Showing \(items.count) of \(total) records").font(.caption).foregroundStyle(.secondary)
            if items.count < total {
                Button("Load more decisions") { Task { await loadMore() } }.disabled(busy)
            }
            if items.isEmpty && !busy { Text("No decision records for this day.").foregroundStyle(.secondary) }
            if let extraction {
                Text("\(extraction.processed_sources)/\(extraction.total_sources) sources reviewed · \(extraction.pending_chunks) pending · \(extraction.gaps) capture gaps")
                    .font(.caption).foregroundStyle(.secondary)
                if let count = extraction.suppressed_sources, count > 0 {
                    Text("\(count) previously removed sources excluded from automatic extraction. You can still create an explicit manual record.").font(.caption).foregroundStyle(.secondary)
                }
                if let offset = extraction.processed_source_offset, offset > 0 {
                    Text("Current source: \(offset.formatted()) characters reviewed").font(.caption).foregroundStyle(.secondary)
                }
                if extraction.next_run_id != nil {
                    Button("Continue finding decisions") { Task { await extract(continuing: true) } }.disabled(busy)
                }
            }
            Button("Find decisions in this day") { Task { await extract(continuing: false) } }.disabled(busy)
            Button("Refresh decisions") { revision += 1 }.disabled(busy)
            if busy { ProgressView("Reading decisions…") }
            ErrorNote(message: error)
        }
        .task(id: "\(model.backendAddress).\(day).\(showDismissed).\(revision)") { await load() }
        .onChange(of: phase) { _, value in
            if value == .active { revision += 1 }
            else { requestID = UUID(); items = []; extraction = nil }
        }
        .onDisappear { requestID = UUID(); items = []; extraction = nil }
    }
    private func load() async {
        let token = UUID(); requestID = token; busy = true; error = nil; items = []; total = 0; extraction = nil
        defer { if requestID == token { busy = false } }
        do {
            let result: DecisionList = try await model.client().decisionRequest("v1/decisions", query: ["day": day, "include_dismissed": "\(showDismissed)", "limit": "50", "offset": "0"])
            guard !Task.isCancelled, requestID == token else { return }
            items = result.items; total = result.total
        } catch { if !Task.isCancelled, requestID == token { self.error = error.localizedDescription } }
    }
    private func loadMore() async {
        guard !busy, items.count < total else { return }
        let token = requestID; busy = true; error = nil
        defer { if requestID == token { busy = false } }
        do {
            let result: DecisionList = try await model.client().decisionRequest("v1/decisions", query: ["day": day, "include_dismissed": "\(showDismissed)", "limit": "50", "offset": "\(items.count)"])
            guard !Task.isCancelled, requestID == token else { return }
            let existing = Set(items.map(\.id))
            items.append(contentsOf: result.items.filter { !existing.contains($0.id) }); total = result.total
        } catch { if !Task.isCancelled, requestID == token { self.error = error.localizedDescription } }
    }
    private func extract(continuing: Bool) async {
        let token = UUID(); requestID = token; busy = true; error = nil
        defer { if requestID == token { busy = false } }
        do {
            let previous = extractionRequest
            let body: DecisionExtractionRequest
            if let previous, (continuing && previous.run_id == extraction?.next_run_id) || (!continuing && previous.day == day) { body = previous }
            else { body = continuing ? DecisionExtractionRequest(run_id: extraction?.next_run_id) : DecisionExtractionRequest(day: day) }
            extractionRequest = body
            let result: DecisionExtraction = try await model.client().decisionRequest("v1/decision-extractions", method: "POST", body: body)
            guard !Task.isCancelled, requestID == token else { return }
            extraction = result; extractionRequest = nil
            let records: DecisionList = try await model.client().decisionRequest("v1/decisions", query: ["day": day, "include_dismissed": "\(showDismissed)", "limit": "50", "offset": "0"])
            guard !Task.isCancelled, requestID == token else { return }
            items = records.items; total = records.total
        } catch { if !Task.isCancelled, requestID == token { self.error = error.localizedDescription } }
    }
}

struct DecisionRow: View {
    let record: DecisionRecord
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(record.title).foregroundStyle(.primary)
            Text("\(record.display_state.capitalized) · \(record.review_status) · evidence \(record.evidence_condition)")
                .font(.caption).foregroundStyle(.secondary)
            Text(record.started_at).font(.caption).foregroundStyle(.secondary)
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
    @State private var predecessor: DecisionRecord?
    @State private var choosingPredecessor = false
    @State private var deleting = false
    @State private var requestID = UUID()
    @State private var actionKey = ""
    @State private var commandID = UUID().uuidString
    var body: some View {
        List {
            if let record {
                Section { DecisionRow(record: record) }
                if let content = record.content {
                    DecisionClaims(content: content)
                    if !content.owner_notes.isEmpty {
                        Section("Owner note · not recorded speech") { Text(content.owner_notes).textSelection(.enabled) }
                    }
                    Section("Review") {
                        Text("Confirm that the record represents its sources. Missing rationale must remain unstated.").font(.caption).foregroundStyle(.secondary)
                        Button("Edit record and evidence") { editor = DecisionEditSelection(record: record) }
                        if record.predecessor_id == nil && record.successor_id == nil {
                            Button(predecessor == nil ? "Choose earlier decision this revises…" : "Revises: \(predecessor!.title)") { choosingPredecessor = true }
                            if predecessor != nil { Button("Clear earlier decision") { predecessor = nil } }
                        }
                        Button(predecessor == nil ? "Confirm accuracy" : "Confirm accuracy and revision") { Task { await event("confirm") } }
                            .disabled(record.evidence_condition != "current")
                        if record.predecessor_id == nil && record.successor_id == nil {
                            Button("Dismiss proposal") { Task { await event("dismiss") } }
                        }
                        Button(record.evidence_condition == "conflicted" ? "Resolve conflict after review" : "Mark contradictory evidence") {
                            Task { await event(record.evidence_condition == "conflicted" ? "resolve" : "conflict") }
                        }
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
                                        DecisionClaims(content: content)
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
        .task(id: model.backendAddress + id) { await load() }
        .refreshable { await load() }
        .sheet(item: $editor, onDismiss: { Task { await load() } }) { selection in
            NavigationStack { DecisionEditor(record: selection.record, source: nil) }
        }
        .sheet(isPresented: $choosingPredecessor) { NavigationStack { DecisionPredecessorPicker(excluding: id, selection: $predecessor) } }
        .confirmationDialog("Delete this decision and its content history?", isPresented: $deleting, titleVisibility: .visible) {
            Button("Delete decision", role: .destructive) { Task { await delete() } }
        }
        .onChange(of: phase) { _, value in
            if value == .active { Task { await load() } }
            else { requestID = UUID(); record = nil; predecessor = nil; editor = nil; choosingPredecessor = false }
        }
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
        record = nil; predecessor = nil; editor = nil; choosingPredecessor = false; deleting = false
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
                    NavigationLink("Open original source") { DecisionEvidenceSource(id: anchor.source_id, expectedVersion: anchor.source_version) }
                    Text("Source \(anchor.source_id) · characters \(anchor.start)–\(anchor.end)").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct DecisionEvidenceSource: View {
    let id: String
    let expectedVersion: String
    @Environment(AppModel.self) private var model
    @State private var source: JSONValue?
    @State private var error: String?
    var body: some View {
        Group {
            if let source { SourceDetail(source: source) }
            else { List { ProgressView(); ErrorNote(message: error) } }
        }.task {
            do {
                let result: JSONValue = try await model.client().decisionRequest("v1/transcripts/\(id)")
                guard !Task.isCancelled else { return }
                guard result["source_version"].text == expectedVersion else { throw BackendFailure(message: "This source changed. Refresh the decision and review its evidence again.") }
                source = result
            } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
        }.onDisappear { source = nil }
    }
}

struct DecisionPredecessorPicker: View {
    let excluding: String
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
            ForEach(items.filter { $0.id != excluding && $0.review_status == "confirmed" && $0.successor_id == nil && $0.evidence_condition == "current" }) { record in
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
            .task(id: model.backendAddress + query) { await load(reset: true) }
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

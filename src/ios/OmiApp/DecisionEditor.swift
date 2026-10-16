import SwiftUI

struct DecisionEditor: View {
    let record: DecisionRecord?
    let source: DecisionSource?
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var phase
    @State private var content: DecisionContent
    @State private var state: String
    @State private var busy = false
    @State private var error: String?
    @State private var commandID = UUID().uuidString
    @State private var submittedContent: DecisionContent?
    @State private var submittedState: String?
    @State private var invalidated = false

    init(record: DecisionRecord?, source: DecisionSource?) {
        self.record = record; self.source = source
        _content = State(initialValue: record?.content ?? DecisionContent(choice: DecisionClaim(text: "", evidence: [])))
        _state = State(initialValue: record?.state ?? "tentative")
    }
    var body: some View {
        Form {
            if invalidated {
                Text("This record or its evidence changed. Close this editor and refresh the source or decision before editing again.")
            } else {
            Section {
                Text("Describe an explicitly selected choice, not a suggestion or disagreement. Cite each recorded claim. Owner notes remain separate from speech. Each claim supports up to 4,000 characters and needs an exact source passage.").font(.caption).foregroundStyle(.secondary)
                Picker("Recorded state", selection: $state) { Text("Tentative").tag("tentative"); Text("Final").tag("final") }
            }
            DecisionClaimEditor(title: "Choice", claim: $content.choice, initialSource: source)
            if content.context != nil {
                DecisionClaimEditor(title: "Recorded context", claim: Binding(get: { content.context ?? DecisionClaim(text: "", evidence: []) }, set: { content.context = $0 }), initialSource: source)
                Button("Remove recorded context", role: .destructive) { content.context = nil }
            } else { Button("Add recorded context") { content.context = DecisionClaim(text: "", evidence: []) } }
            if content.rationale != nil {
                DecisionClaimEditor(title: "Recorded rationale", claim: Binding(get: { content.rationale ?? DecisionClaim(text: "", evidence: []) }, set: { content.rationale = $0 }), initialSource: source)
                Button("Remove recorded rationale", role: .destructive) { content.rationale = nil }
            } else {
                Section { Text("Rationale: not stated").foregroundStyle(.secondary); Button("Add explicitly stated rationale") { content.rationale = DecisionClaim(text: "", evidence: []) } }
            }
            ForEach(content.alternatives.indices, id: \.self) { index in
                DecisionClaimEditor(title: "Discussed alternative \(index + 1)", claim: $content.alternatives[index], initialSource: source)
                Button("Remove alternative \(index + 1)", role: .destructive) { content.alternatives.remove(at: index) }
            }
            Button("Add explicitly discussed alternative") { content.alternatives.append(DecisionClaim(text: "", evidence: [])) }
            Section("Owner note · not recorded speech") { TextField("Your explanation", text: $content.owner_notes, axis: .vertical).lineLimit(3...10) }
            Section("Optional related labels") {
                ForEach(content.relationships.indices, id: \.self) { index in
                    Picker("Kind", selection: $content.relationships[index].kind) {
                        ForEach(["project", "person", "commitment", "meeting"], id: \.self) { kind in Text(kind.capitalized).tag(kind) }
                    }
                    TextField("Label", text: $content.relationships[index].label)
                    Button("Remove label", role: .destructive) { content.relationships.remove(at: index) }
                }
                Button("Add related label") { content.relationships.append(DecisionRelationship(kind: "project", label: "")) }
            }
            }
            ErrorNote(message: error)
            if busy { ProgressView("Saving…") }
        }.navigationTitle(record == nil ? "Create decision" : "Edit decision")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(busy) }
                ToolbarItem(placement: .confirmationAction) { Button("Save") { Task { await save() } }.disabled(busy || invalidated || !valid) }
            }
            .interactiveDismissDisabled(busy)
            .onChange(of: phase) { _, value in if value != .active { dismiss() } }
    }
    private var valid: Bool {
        let claims = [content.choice] + [content.context, content.rationale].compactMap { $0 } + content.alternatives
        return claims.allSatisfy {
            !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.text.unicodeScalars.count <= 4_000
                && !$0.evidence.isEmpty && $0.evidence.count <= 20
        } && content.alternatives.count <= 20 && content.owner_notes.unicodeScalars.count <= 8_000
            && content.relationships.count <= 30 && content.relationships.allSatisfy {
                !$0.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.label.unicodeScalars.count <= 200
            }
    }
    private func save() async {
        busy = true; error = nil; defer { busy = false }
        if submittedContent != content || submittedState != state {
            commandID = UUID().uuidString; submittedContent = content; submittedState = state
        }
        do {
            let client = try model.client()
            if let record {
                _ = try await client.decisionRequest("v1/decisions/\(record.id)/events", method: "POST", body: DecisionEvent(command_id: commandID, expected_version: record.version, type: "edit", content: content, state: state)) as DecisionRecord
            } else {
                _ = try await client.decisionRequest("v1/decisions", method: "POST", body: DecisionCreate(command_id: commandID, state: state, content: content)) as DecisionRecord
            }
            dismiss()
        } catch {
            if (error as? BackendFailure)?.requiresDecisionReload == true {
                content = DecisionContent(choice: DecisionClaim(text: "", evidence: [])); invalidated = true
            }
            self.error = error.localizedDescription
        }
    }
}

private struct DecisionClaimEditor: View {
    let title: String
    @Binding var claim: DecisionClaim
    let initialSource: DecisionSource?
    @Environment(AppModel.self) private var model
    @State private var sourceID = ""
    @State private var source: DecisionSource?
    @State private var quote = ""
    @State private var occurrence = 0
    @State private var error: String?
    @State private var busy = false
    var body: some View {
        Section(title) {
            TextField("Recorded claim", text: $claim.text, axis: .vertical).lineLimit(2...8)
            ForEach(Array(claim.evidence.enumerated()), id: \.offset) { index, anchor in
                VStack(alignment: .leading) {
                    Text("“\(anchor.quote)”").font(.callout).textSelection(.enabled)
                    Text("\(anchor.source_id) · characters \(anchor.start)–\(anchor.end)").font(.caption).foregroundStyle(.secondary)
                    Button("Remove passage", role: .destructive) { claim.evidence.remove(at: index) }
                }
            }
            DisclosureGroup("Attach original passage") {
                TextField("Source UUID", text: $sourceID).textInputAutocapitalization(.never).autocorrectionDisabled()
                Button("Load current source") { Task { await loadSource() } }.disabled(busy || UUID(uuidString: sourceID) == nil)
                if let source {
                    Text(source.text).font(.callout).textSelection(.enabled)
                    TextField("Exact quotation", text: $quote, axis: .vertical).lineLimit(2...8)
                    Text("Paste an exact passage of up to 20,000 characters. Long sources start with an empty quotation.").font(.caption).foregroundStyle(.secondary)
                    Stepper("Occurrence: \(occurrence + 1)", value: $occurrence, in: 0...100)
                    Text("If the quotation repeats, choose its occurrence in this source.").font(.caption).foregroundStyle(.secondary)
                    Button("Attach passage") {
                        if let anchor = source.anchor(quote: quote, occurrence: occurrence), !claim.evidence.contains(anchor) {
                            claim.evidence.append(anchor); error = nil
                        }
                    }.disabled(source.anchor(quote: quote, occurrence: occurrence) == nil)
                }
                ErrorNote(message: error)
            }
        }.task {
            if let initialSource { source = initialSource; sourceID = initialSource.id; quote = initialSource.initialQuotation }
            else if let anchor = claim.evidence.first { sourceID = anchor.source_id; quote = anchor.quote }
        }
        .onChange(of: sourceID) { _, value in if source?.id != value { source = nil }; occurrence = 0 }
    }
    private func loadSource() async {
        busy = true; source = nil; error = nil; defer { busy = false }
        do {
            let result: DecisionSource = try await model.client().decisionRequest("v1/transcripts/\(sourceID)")
            guard !Task.isCancelled else { return }; source = result; quote = result.initialQuotation; occurrence = 0
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

struct SourceDecisionCreation: View {
    let sourceID: String
    @Environment(AppModel.self) private var model
    @State private var source: DecisionSource?
    @State private var error: String?
    var body: some View {
        Group {
            if let source { DecisionEditor(record: nil, source: source) }
            else { List { if error == nil { ProgressView("Loading current evidence…") }; ErrorNote(message: error) } }
        }.task {
            do {
                let result: DecisionSource = try await model.client().decisionRequest("v1/transcripts/\(sourceID)")
                guard !Task.isCancelled else { return }; source = result
            } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
        }.onDisappear { source = nil }
    }
}

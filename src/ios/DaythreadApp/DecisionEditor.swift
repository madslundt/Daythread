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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        Form {
            if invalidated {
                Text(L10n.text("This record or its evidence changed. Close this editor and refresh the source or decision before editing again."))
            } else {
            Section {
                Text(L10n.text("Describe an explicitly selected choice, not a suggestion or disagreement. Cite each recorded claim. Owner notes remain separate from speech. Each claim supports up to 4,000 characters and needs an exact source passage.")).font(.caption).foregroundStyle(.secondary)
                Picker(L10n.text("Recorded state"), selection: $state) { Text(L10n.text("Tentative")).tag("tentative"); Text(L10n.text("Final")).tag("final") }
            }
            DecisionClaimEditor(title: L10n.text("Choice"), claim: $content.choice, initialSource: source)
            if content.context != nil {
                DecisionClaimEditor(title: L10n.text("Recorded context"), claim: Binding(get: { content.context ?? DecisionClaim(text: "", evidence: []) }, set: { content.context = $0 }), initialSource: source)
                Button(L10n.text("Remove recorded context"), role: .destructive) { content.context = nil }
            } else { Button(L10n.text("Add recorded context")) { content.context = DecisionClaim(text: "", evidence: []) } }
            if content.rationale != nil {
                DecisionClaimEditor(title: L10n.text("Recorded rationale"), claim: Binding(get: { content.rationale ?? DecisionClaim(text: "", evidence: []) }, set: { content.rationale = $0 }), initialSource: source)
                Button(L10n.text("Remove recorded rationale"), role: .destructive) { content.rationale = nil }
            } else {
                Section { Text(L10n.text("Rationale: not stated")).foregroundStyle(.secondary); Button(L10n.text("Add explicitly stated rationale")) { content.rationale = DecisionClaim(text: "", evidence: []) } }
            }
            ForEach(content.alternatives.indices, id: \.self) { index in
                DecisionClaimEditor(title: L10n.text("Discussed alternative \(index + 1)"), claim: $content.alternatives[index], initialSource: source)
                Button(L10n.text("Remove alternative \(index + 1)"), role: .destructive) { content.alternatives.remove(at: index) }
            }
            Button(L10n.text("Add explicitly discussed alternative")) { content.alternatives.append(DecisionClaim(text: "", evidence: [])) }
            ForEach(content.contradictions.indices, id: \.self) { index in
                Picker(L10n.text("Contradicted field"), selection: $content.contradictions[index].field) {
                    ForEach(["choice", "context", "rationale", "alternatives"], id: \.self) { field in Text(L10n.status(field)).tag(field) }
                }
                DecisionClaimEditor(title: L10n.text("Opposing statement \(index + 1)"), claim: $content.contradictions[index].claim, initialSource: source)
                Button(L10n.text("Remove opposing statement \(index + 1)"), role: .destructive) { content.contradictions.remove(at: index) }
            }
            Button(L10n.text("Add contradictory evidence")) { content.contradictions.append(DecisionContradiction()) }
            if !content.contradictions.isEmpty {
                Text(L10n.text("Save the opposing passages, explain the resolution in owner notes, then explicitly resolve the conflict after review. The passages remain in the record.")).font(.caption).foregroundStyle(.secondary)
            }
            Section(L10n.text("Owner note · not recorded speech")) { TextField(L10n.text("Your explanation"), text: $content.owner_notes, axis: .vertical).lineLimit(3...10) }
            Section(L10n.text("Optional related labels")) {
                ForEach(content.relationships.indices, id: \.self) { index in
                    Picker(L10n.text("Kind"), selection: $content.relationships[index].kind) {
                        ForEach(["project", "person", "commitment", "meeting"], id: \.self) { kind in Text(L10n.status(kind)).tag(kind) }
                    }
                    TextField(L10n.text("Label"), text: $content.relationships[index].label)
                    Button(L10n.text("Remove label"), role: .destructive) { content.relationships.remove(at: index) }
                }
                Button(L10n.text("Add related label")) { content.relationships.append(DecisionRelationship(kind: "project", label: "")) }
            }
            }
            ErrorNote(message: error)
            if busy { ProgressView(L10n.text("Saving…")) }
        }.navigationTitle(record == nil ? L10n.text("Create decision") : L10n.text("Edit decision"))
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() }.disabled(busy) }
                ToolbarItem(placement: .confirmationAction) { Button(L10n.text("Save")) { Task { await save() } }.disabled(busy || invalidated || !valid) }
            }
            .interactiveDismissDisabled(busy)
            .onChange(of: phase) { _, value in if value != .active { dismiss() } }
    }
    private var valid: Bool {
        let claims = [content.choice] + [content.context, content.rationale].compactMap { $0 } + content.alternatives + content.contradictions.map(\.claim)
        return claims.allSatisfy {
            !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.text.unicodeScalars.count <= 4_000
                && !$0.evidence.isEmpty && $0.evidence.count <= 20
        } && content.alternatives.count <= 20 && content.contradictions.count <= 20 && content.owner_notes.unicodeScalars.count <= 8_000
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
            self.error = L10n.error(error)
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
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        Section(title) {
            TextField(L10n.text("Recorded claim"), text: $claim.text, axis: .vertical).lineLimit(2...8)
            ForEach(Array(claim.evidence.enumerated()), id: \.offset) { index, anchor in
                VStack(alignment: .leading) {
                    Text("“\(anchor.quote)”").font(.callout).textSelection(.enabled)
                    Text(L10n.text("\(anchor.source_id) · characters \(anchor.start)–\(anchor.end)")).font(.caption).foregroundStyle(.secondary)
                    Button(L10n.text("Remove passage"), role: .destructive) { claim.evidence.remove(at: index) }
                }
            }
            DisclosureGroup(L10n.text("Attach original passage")) {
                TextField(L10n.text("Source UUID"), text: $sourceID).textInputAutocapitalization(.never).autocorrectionDisabled()
                Button(L10n.text("Load current source")) { Task { await loadSource() } }.disabled(busy || UUID(uuidString: sourceID) == nil)
                if let source {
                    Text(source.text).font(.callout).textSelection(.enabled)
                    TextField(L10n.text("Exact quotation"), text: $quote, axis: .vertical).lineLimit(2...8)
                    Text(L10n.text("Paste an exact passage of up to 20,000 characters. Long sources start with an empty quotation.")).font(.caption).foregroundStyle(.secondary)
                    Stepper(L10n.text("Occurrence: \(occurrence + 1)"), value: $occurrence, in: 0...100)
                    Text(L10n.text("If the quotation repeats, choose its occurrence in this source.")).font(.caption).foregroundStyle(.secondary)
                    Button(L10n.text("Attach passage")) {
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
        } catch { if !Task.isCancelled { self.error = L10n.error(error) } }
    }
}

struct SourceDecisionCreation: View {
    let sourceID: String
    @Environment(AppModel.self) private var model
    @State private var source: DecisionSource?
    @State private var error: String?
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        Group {
            if let source { DecisionEditor(record: nil, source: source) }
            else { List { if error == nil { ProgressView(L10n.text("Loading current evidence…")) }; ErrorNote(message: error) } }
        }.task {
            do {
                let result: DecisionSource = try await model.client().decisionRequest("v1/transcripts/\(sourceID)")
                guard !Task.isCancelled else { return }; source = result
            } catch { if !Task.isCancelled { self.error = L10n.error(error) } }
        }.onDisappear { source = nil }
    }
}

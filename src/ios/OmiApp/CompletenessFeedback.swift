import SwiftUI
import OmiCore

/// Evidence diagnostics are scoped to the saved output; source counts are not accuracy.
struct EvidenceFeedbackView: View {
    let outputID: String
    let evidence: JSONValue
    var outputText = ""
    @Environment(AppModel.self) private var model
    @State private var current: JSONValue = .null
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !outputID.isEmpty {
                let value = current == .null ? evidence : current
                Text("\(value["selected"].array.count) of \(value["eligible_transcripts"].integer) eligible transcript sources selected · \(value["cited_ids"].array.count) cited · \(value["retrieval_mode"].text)").font(.caption)
                Text("\(value["pending_chunks"].integer) pending and \(value["blocked_chunks"].integer) blocked when generated. Evidence may pass through summarization. Selection and citations do not verify accuracy.").font(.caption).foregroundStyle(.secondary)
                if !value["changed_source_ids"].array.isEmpty { Text("Sources changed. Regenerate or ask again.").font(.caption).foregroundStyle(.orange) }
                ErrorNote(message: error)
                NavigationLink("Report incorrect or missing information") { FeedbackEditor(outputID: outputID, outputText: outputText) }
            } else { Text("No saved evidence diagnostics for this older output.").font(.caption).foregroundStyle(.secondary) }
        }.task(id: model.archiveIdentity + outputID) {
            current = .null
            guard !outputID.isEmpty else { return }
            let address = model.archiveIdentity
            do {
                let result = try await model.client().request("v1/outputs/\(outputID)/evidence")
                guard !Task.isCancelled, model.archiveIdentity == address else { return }
                current = result; error = nil
            } catch { if !Task.isCancelled, model.archiveIdentity == address { self.error = error.localizedDescription } }
        }
    }
}

struct CompletenessView: View {
    let value: JSONValue
    var body: some View {
        if value != .null {
            DisclosureGroup("Evidence available in this period") {
                Text("Observed \(value["as_of"].text). Future time is excluded.")
                Text("\(value["seconds"]["available_audio"].integer / 60) min playable audio · \(value["seconds"]["transcript"].integer / 60) min transcript intervals")
                Text("\(value["seconds"]["intentional_pause"].integer / 60) min intentional pauses · \(value["seconds"]["reported_interruption"].integer / 60) min reported interruptions")
                Text("\(value["counts"]["pending"].integer) awaiting transcription · \(value["counts"]["blocked"].integer) blocked")
                Text("\(value["counts"]["transcript_only_imports"].integer) transcript-only imports · \(value["counts"]["expired_audio"].integer) expired audio · \(value["counts"]["missing_audio"].integer) missing files")
                Text("\(value["counts"]["expired_transcripts"].integer) transcripts unavailable after retention")
                Text("\(value["counts"]["silence_gate"].integer) silence-gated · \(value["counts"]["empty_stt"].integer) empty STT. Neither proves no speech.")
                Text("\(value["unknown_count"].integer) intervals have no known coverage. Transcripts do not establish continuous capture. Overlaps count once per track.")
                ForEach(Array(value["capture_intervals"].array.enumerated()), id: \.offset) { _, interval in
                    Text(interval["state"].text + ": " + interval["start"].text + " – " + interval["end"].text + " · " + interval["cause"].text + (interval["open"] == .bool(true) ? " (no closing transition reported)" : ""))
                }
                ForEach(Array(value["capture_gaps"].array.enumerated()), id: \.offset) { _, gap in
                    Text("\(gap["started_at"].text) – \(gap["ended_at"].text): \(gap["cause"].text) · \(gap["reason"].text)")
                }
                NavigationLink("Report missing information") { FeedbackEditor(start: value["start"].text, end: value["end"].text) }
            }.font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct FeedbackEditor: View {
    var sourceID = ""
    var outputID = ""
    var start = ""
    var end = ""
    var outputText = ""
    @Environment(AppModel.self) private var model
    @State private var issue = "incorrect"
    @State private var stage = "unknown"
    @State private var note = ""
    @State private var excerpt = ""
    @State private var expected = ""
    @State private var search = ""
    @State private var recordings: [JSONValue] = []
    @State private var id = UUID().uuidString
    @State private var busy = false
    @State private var message: String?
    @State private var draftKey = ""
    @State private var submittedSignature = ""
    private var key: String { model.archiveIdentity + ".feedback-draft." + sourceID + outputID + start + end }
    var body: some View {
        Form {
            Picker("Problem", selection: $issue) { Text("Incorrect information").tag("incorrect"); Text("Missing information").tag("missing") }
            Picker("Suspected stage", selection: $stage) {
                Text("I don’t know").tag("unknown"); Text("Capture").tag("capture"); Text("Transcription (STT)").tag("stt"); Text("Retrieval").tag("retrieval"); Text("Generation").tag("generation")
            }
            Text("Capture: absent audio. STT: audio differs from text. Retrieval: expected source not selected. Generation: supplied evidence misrepresented. Leave unknown if unsure.").font(.caption)
            TextField("Find an expected recording (optional)", text: $search)
            Button("Search recordings") { Task { await searchRecordings() } }.disabled(busy || search.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || search.count > 200)
            ForEach(Array(recordings.enumerated()), id: \.offset) { _, source in
                Toggle(isOn: Binding(get: { expected.split(separator: ",").map(String.init).contains(source["id"].text) }, set: { selected in
                    var ids = expected.split(separator: ",").map(String.init)
                    ids.removeAll { $0 == source["id"].text }
                    if selected && ids.count < 30 { ids.append(source["id"].text) }
                    expected = ids.joined(separator: ",")
                })) { Text(source["started_at"].text + " · " + String(source["text"].text.prefix(160))).font(.caption) }
            }
            if !expected.isEmpty { Button("Clear expected recordings") { expected = "" } }
            if !outputText.isEmpty {
                Button("Use beginning of shown answer as passage") { excerpt = String(outputText.prefix(1000)) }
                TextField("Passage to include (optional)", text: $excerpt, axis: .vertical).lineLimit(2...6)
            }
            TextField("What was wrong or missing?", text: $note, axis: .vertical).lineLimit(3...8)
            Button("Save on phone") { saveDraft(); message = "Saved on this phone; backend receipt not confirmed." }
            Button("Send feedback to backend") { Task { await send() } }.disabled(busy || note.count > 2000 || expected.count > 1200 || excerpt.count > 1000)
            Button("Delete phone draft", role: .destructive) { UserDefaults.standard.removeObject(forKey: "cache." + key); note = ""; excerpt = ""; expected = ""; id = UUID().uuidString; message = "Phone draft deleted" }
            if let message { Text(message).font(.caption).textSelection(.enabled) }
            Text("Feedback stores references and your note locally. It does not edit evidence or train a model. Phone drafts stay here until sent or deleted.").font(.caption).foregroundStyle(.secondary)
        }.disabled(busy).navigationTitle("Report a problem").task(id: key) {
            guard draftKey != key else { return }; draftKey = key
            id = UUID().uuidString; issue = start.isEmpty ? "incorrect" : "missing"; stage = "unknown"; note = ""; excerpt = ""; expected = ""; submittedSignature = ""; message = nil; search = ""; recordings = []
            if let saved = LocalCache.load(key) { issue = saved["issue"].text; stage = saved["stage"].text; note = saved["note"].text; excerpt = saved["excerpt"].text; expected = saved["expected"].text; id = saved["id"].text; submittedSignature = saved["submitted_signature"].text }
        }
    }
    private func searchRecordings() async {
        let address = model.archiveIdentity
        busy = true; defer { busy = false }
        do {
            let result = try await model.client().request("v1/transcripts", query: ["q": String(search.prefix(200)), "limit": "10"])
            guard model.archiveIdentity == address else { return }
            recordings = result["items"].array; message = nil
        } catch { if model.archiveIdentity == address { message = error.localizedDescription } }
    }
    private func saveDraft() {
        LocalCache.save(.object(["id": .string(id), "issue": .string(issue), "stage": .string(stage), "note": .string(String(note.prefix(2000))), "excerpt": .string(String(excerpt.prefix(1000))), "expected": .string(String(expected.prefix(1200))), "submitted_signature": .string(submittedSignature)]), key: key)
    }
    private func send() async {
        let signature = issue + "\n" + stage + "\n" + note + "\n" + expected + "\n" + excerpt
        if !submittedSignature.isEmpty && submittedSignature != signature { id = UUID().uuidString }
        submittedSignature = signature
        saveDraft(); busy = true; defer { busy = false }
        var body: [String: Any] = ["issue": issue, "stage": stage, "note": note, "excerpt": excerpt,
                                  "expected_source_ids": expected.split(whereSeparator: { $0 == "," || $0.isWhitespace }).map(String.init)]
        if !sourceID.isEmpty { body["source_id"] = sourceID }
        if !outputID.isEmpty { body["output_id"] = outputID }
        if !start.isEmpty { body["start"] = start; body["end"] = end }
        let address = model.archiveIdentity, savedKey = key
        do {
            let receipt = try await model.client().request("v1/feedback/\(id)", method: "PUT", body: body)
            guard UUID(uuidString: receipt["id"].text) == UUID(uuidString: id) else { throw BackendFailure(message: "Backend did not confirm this report") }
            UserDefaults.standard.removeObject(forKey: "cache." + savedKey)
            if model.archiveIdentity == address { message = "Received by backend. Review it from Capture → Feedback and repairs." }
        } catch { if model.archiveIdentity == address { message = "Saved on phone; backend receipt not confirmed. Retry safely: \(error.localizedDescription)" } }
    }
}

struct FeedbackReviewView: View {
    @Environment(AppModel.self) private var model
    @State private var items: [JSONValue] = []
    @State private var error: String?
    var body: some View {
        List {
            Text("Sync pending audio on Capture; listen and correct the transcript; then regenerate the recap or ask again with a narrower scope. Feedback does not change evidence.").font(.caption)
            ErrorNote(message: error)
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                FeedbackReviewRow(item: item) { await load() }.id(item["id"].text)
            }
            if items.isEmpty { Text("No saved reports") }
        }.navigationTitle("Feedback and repairs").task(id: model.archiveIdentity) { items = []; error = nil; await load() }.refreshable { await load() }
    }
    private func load() async {
        let address = model.archiveIdentity
        do {
            let result = try await model.client().request("v1/feedback")
            guard model.archiveIdentity == address, !Task.isCancelled else { return }
            items = result["items"].array; error = nil
        }
        catch { if model.archiveIdentity == address, !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

private struct FeedbackReviewRow: View {
    let item: JSONValue
    let refresh: () async -> Void
    @Environment(AppModel.self) private var model
    @State private var stage = "unknown"
    @State private var note = ""
    @State private var error: String?
    @State private var busy = false
    @State private var source: SourceSelection?
    var body: some View {
        Section(item["issue"].text + " · " + item["status"].text) {
            Text(item["note"].text).textSelection(.enabled)
            if !item["excerpt"].text.isEmpty { Text(item["excerpt"].text).font(.callout).textSelection(.enabled) }
            Text("User stage: \(item["stage"].text) · suggestion: \(item["suggested_stage"].text.replacingOccurrences(of: "_", with: " "))").font(.caption)
            if !item["source_id"].text.isEmpty {
                Text("Source \(item["source_id"].text)").font(.caption).textSelection(.enabled)
                Button("Inspect or correct source") { Task { await inspectSource() } }.disabled(busy)
            }
            if !item["output_id"].text.isEmpty { Text("Output \(item["output_id"].text)").font(.caption).textSelection(.enabled) }
            ForEach(Array(item["events"].array.enumerated()), id: \.offset) { _, event in
                Text(event["status"].text + " (" + event["stage"].text + "): " + event["note"].text).font(.caption)
            }
            Picker("Reviewed stage", selection: $stage) {
                ForEach(["unknown","capture","stt","retrieval","generation"], id: \.self) { Text($0).tag($0) }
            }
            TextField("Repair checked", text: $note, axis: .vertical)
            Button(item["status"].text == "open" ? "Mark resolved after checking" : "Reopen") { Task { await update() } }.disabled(busy || note.count > 2000)
            Button("Delete report", role: .destructive) { Task { await update(remove: true) } }.disabled(busy)
            ErrorNote(message: error)
        }.task { stage = item["stage"].text }
            .sheet(item: $source) { selection in SourceDetail(source: selection.source) }
    }
    private func inspectSource() async {
        let address = model.archiveIdentity
        busy = true; defer { busy = false }
        do {
            let result = try await model.client().request("v1/sources/\(item["source_id"].text)")
            guard model.archiveIdentity == address else { return }
            source = SourceSelection(source: result); error = nil
        } catch { if model.archiveIdentity == address { self.error = error.localizedDescription } }
    }
    private func update(remove: Bool = false) async {
        let address = model.archiveIdentity
        busy = true; defer { busy = false }
        do {
            if remove { _ = try await model.client().request("v1/feedback/\(item["id"].text)", method: "DELETE") }
            else { _ = try await model.client().request("v1/feedback/\(item["id"].text)/events", method: "POST", body: ["event_id": UUID().uuidString, "status": item["status"].text == "open" ? "resolved" : "open", "stage": stage, "note": note]) }
            guard model.archiveIdentity == address else { return }
            error = nil; await refresh()
        } catch { if model.archiveIdentity == address { self.error = error.localizedDescription } }
    }
}

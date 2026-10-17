import SwiftUI
import OmiCore

struct InboxDetail: View {
    let id: String
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var base: InboxItem?
    @State private var latest: InboxItem?
    @State private var title = ""
    @State private var owner = ""
    @State private var kind = "commitment"
    @State private var status = "pending"
    @State private var precision = "unknown"
    @State private var day = ""
    @State private var instant = ""
    @State private var timezone = ""
    @State private var duplicate = ""
    @State private var alternatives: [InboxItem] = []
    @State private var resets: Set<String> = []
    @State private var operation: (fingerprint: String, id: String)?
    @State private var busy = false
    @State private var error: String?
    @State private var saved: String?
    @State private var requestID = UUID()
    @State private var mutationID = UUID()
    private var conflict: Bool { base?.version != latest?.version }

    var body: some View {
        Form {
            if let base {
                Section("Review") {
                    Text("Unsaved edits remain here if saving fails. Leaving this screen discards them.").font(.caption)
                    ErrorNote(message: error)
                    if let saved { Text(saved).font(.caption).foregroundStyle(Theme.accent) }
                    if conflict, let latest {
                        Text("Saved version changed: \(latest.title) · \(latest.owner ?? "Owner unknown") · \(latest.deadline?.label ?? "Deadline unknown") · \(latest.status)").font(.caption)
                        Button("Use latest version as save base (keep draft)") { self.base = latest }
                    }
                    TextField("Title", text: $title, axis: .vertical)
                    Picker("Kind", selection: $kind) {
                        Text("Commitment").tag("commitment"); Text("Deadline").tag("deadline"); Text("Question").tag("question")
                    }
                    TextField("Owner unknown", text: $owner)
                    Picker("Deadline precision", selection: $precision) {
                        Text("Unknown / unresolved").tag("unknown"); Text("Date only").tag("date"); Text("Exact instant").tag("datetime")
                    }
                    if precision == "date" { TextField("YYYY-MM-DD", text: $day).textInputAutocapitalization(.never) }
                    if precision == "datetime" { TextField("ISO instant with UTC offset", text: $instant).textInputAutocapitalization(.never) }
                    TextField("Deadline timezone", text: $timezone).textInputAutocapitalization(.never)
                    Picker("Status", selection: $status) {
                        Text("Pending").tag("pending"); Text(kind == "question" ? "Answered" : "Completed").tag("completed"); Text("Dismissed").tag("dismissed")
                    }
                    if status == "dismissed" {
                        Picker("Duplicate of", selection: $duplicate) {
                            Text("Ordinary dismissal").tag("")
                            ForEach(alternatives.filter { $0.id != id && $0.duplicate_of == nil }) { Text($0.title).tag($0.id) }
                        }
                    }
                    Button(busy ? "Saving…" : "Save review") { Task { await save() } }.disabled(busy || conflict || latest == nil)
                }
                Section("Extracted values / reset overrides") {
                    Text("\(base.proposal.kind): \(base.proposal.title)\n\(base.proposal.owner ?? "Owner unknown") · \(base.proposal.deadline?.label ?? "Deadline unknown")").font(.caption)
                    ForEach(["title", "kind", "owner", "deadline"], id: \.self) { field in
                        Button(resets.contains(field) ? "\(field.capitalized) resets on save" : "Use extracted \(field)") { reset(field, from: base.proposal) }
                    }
                }
                Section("Transcript evidence") {
                    if base.health != "current" { Text("Evidence changed or was withdrawn. Prior excerpts are historical; review before relying on this item.").foregroundStyle(Theme.accent) }
                    if kind == "question" { Text("No answer found in the scanned context; other recordings may contain one.").font(.caption) }
                    ForEach(Array((latest?.evidence ?? []).enumerated()), id: \.offset) { _, span in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(span.quote).textSelection(.enabled)
                            Text("\(span.started_at) · \(span.current ? "Current transcript" : "Prior transcript") · chunk timing\n\(span.source_id)").font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            if span.audio_available {
                                Button("Play source audio") { Task { await play(span.source_id) } }.disabled(busy)
                            } else { Text("Original audio unavailable").font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                }
                Section("Change history") {
                    ForEach(latest?.history ?? []) { event in Text("\(event.created_at) · \(event.kind)").font(.caption) }
                }
            } else {
                ProgressView("Loading inbox item…"); ErrorNote(message: error)
                Button("Retry") { Task { await load() } }
            }
        }.omiScreen().disabled(busy).navigationTitle("Review item").navigationBarTitleDisplayMode(.inline)
            .task(id: model.archiveIdentity) {
                base = nil; latest = nil; alternatives = []; await load()
                let identity = model.archiveIdentity
                let items = (try? await model.client().decoded("v1/inbox", query: ["limit": "200"], as: InboxPage.self).items) ?? []
                guard !Task.isCancelled, model.archiveIdentity == identity else { return }
                alternatives = items
            }
            .onChange(of: model.archiveRevision) { _, _ in Task { await load() } }
            .onDisappear { requestID = UUID(); mutationID = UUID(); busy = false }
            .onChange(of: scenePhase) { _, phase in if phase == .active { Task { await load() } } }
            .refreshable { await load() }
            .onChange(of: title) { _, value in if value != base?.proposal.title { resets.remove("title") } }
            .onChange(of: kind) { _, value in if value != base?.proposal.kind { resets.remove("kind") } }
            .onChange(of: owner) { _, value in if value != (base?.proposal.owner ?? "") { resets.remove("owner") } }
            .onChange(of: precision) { _, _ in clearDeadlineResetIfEdited() }
            .onChange(of: day) { _, _ in clearDeadlineResetIfEdited() }
            .onChange(of: instant) { _, _ in clearDeadlineResetIfEdited() }
            .onChange(of: timezone) { _, _ in clearDeadlineResetIfEdited() }
    }
    private func fill(_ item: InboxItem) {
        title = item.title; owner = item.owner ?? ""; kind = item.kind; status = item.status; duplicate = item.duplicate_of ?? ""
        precision = item.deadline?.precision ?? "unknown"; day = item.deadline?.day ?? ""; instant = item.deadline?.instant ?? ""
        timezone = item.deadline?.timezone ?? model.serverTimeZoneIdentifier ?? "UTC"
    }
    private func load() async {
        let address = model.archiveIdentity, token = UUID()
        requestID = token
        do {
            let item = try await model.client().decoded("v1/inbox/\(id)", as: InboxItem.self)
            guard !Task.isCancelled, address == model.archiveIdentity, requestID == token else { return }
            latest = item; error = nil
            if base == nil { base = item; fill(item) }
        } catch {
            guard !Task.isCancelled, address == model.archiveIdentity, requestID == token else { return }
            self.error = error.localizedDescription
            if (error as? BackendFailure)?.status == 404 { latest = nil }
        }
    }
    private func reset(_ field: String, from proposal: InboxFields) {
        resets.insert(field)
        switch field {
        case "title": title = proposal.title
        case "kind": kind = proposal.kind
        case "owner": owner = proposal.owner ?? ""
        default: precision = proposal.deadline?.precision ?? "unknown"; day = proposal.deadline?.day ?? ""; instant = proposal.deadline?.instant ?? ""; timezone = proposal.deadline?.timezone ?? model.serverTimeZoneIdentifier ?? "UTC"
        }
    }
    private func clearDeadlineResetIfEdited() {
        let original = base?.proposal.deadline
        if precision != (original?.precision ?? "unknown") || day != (original?.day ?? "") || instant != (original?.instant ?? "") || timezone != (original?.timezone ?? model.serverTimeZoneIdentifier ?? "UTC") { resets.remove("deadline") }
    }
    private func save() async {
        guard let base else { return }
        let identity = model.archiveIdentity, token = UUID()
        let client: BackendClient
        do { client = try model.client() } catch { self.error = error.localizedDescription; return }
        mutationID = token; busy = true; saved = nil; error = nil
        defer { if mutationID == token { busy = false } }
        var changes: [String: Any] = [:]
        if title != base.title { changes["title"] = title }
        if kind != base.kind { changes["kind"] = kind }
        if owner != (base.owner ?? "") { changes["owner"] = owner.isEmpty ? NSNull() : owner as Any }
        if precision != (base.deadline?.precision ?? "unknown") || day != (base.deadline?.day ?? "") || instant != (base.deadline?.instant ?? "") || timezone != (base.deadline?.timezone ?? model.serverTimeZoneIdentifier ?? "UTC") {
            changes["deadline"] = ["precision": precision, "day": precision == "date" ? day as Any : NSNull(), "instant": precision == "datetime" ? instant as Any : NSNull(), "timezone": timezone, "basis": "user edit"]
        }
        for field in resets { changes.removeValue(forKey: field) }
        var body: [String: Any] = ["expected_version": base.version, "changes": changes, "status": status, "reset": resets.sorted()]
        body["duplicate_of"] = status == "dismissed" && !duplicate.isEmpty ? duplicate as Any : NSNull()
        do {
            let fingerprint = String(data: try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys]), encoding: .utf8)!
            if operation?.fingerprint != fingerprint { operation = (fingerprint, UUID().uuidString) }
            body["operation_id"] = operation!.id
            let result = try await client.decoded("v1/inbox/\(id)", method: "PATCH", body: body, as: InboxItem.self)
            guard !Task.isCancelled, identity == model.archiveIdentity, mutationID == token else { return }
            accept(result)
        } catch {
            guard !Task.isCancelled, identity == model.archiveIdentity, mutationID == token else { return }
            if let operation, let result = try? await client.decoded("v1/inbox/\(id)", as: InboxItem.self),
               result.history?.contains(where: { $0.id.caseInsensitiveCompare(operation.id) == .orderedSame }) == true {
                guard !Task.isCancelled, identity == model.archiveIdentity, mutationID == token else { return }
                accept(result); return
            }
            guard !Task.isCancelled, identity == model.archiveIdentity, mutationID == token else { return }
            await load()
            guard !Task.isCancelled, identity == model.archiveIdentity, mutationID == token else { return }
            self.error = (error as? BackendFailure)?.status == 409 ? "Saved version changed. Your draft is preserved. Refresh and compare before applying it." : error.localizedDescription
        }
    }
    private func accept(_ result: InboxItem) {
        self.base = result; latest = result; fill(result); resets = []; operation = nil
        saved = "Saved to your local backend."
    }
    private func play(_ source: String) async {
        let identity = model.archiveIdentity, token = UUID()
        mutationID = token; busy = true
        defer { if mutationID == token { busy = false } }
        do {
            let file = try await model.client().download("v1/audio/\(source)", query: [:])
            guard !Task.isCancelled, identity == model.archiveIdentity, mutationID == token else { return }
            model.play(file)
        } catch {
            guard !Task.isCancelled, identity == model.archiveIdentity, mutationID == token else { return }
            self.error = error.localizedDescription
        }
    }
}

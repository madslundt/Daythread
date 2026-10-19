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
    @State private var userNote = ""
    @State private var alternatives: [InboxItem] = []
    @State private var resets: Set<String> = []
    @State private var operation: (fingerprint: String, id: String)?
    @State private var busy = false
    @State private var error: String?
    @State private var saved: String?
    @State private var requestID = UUID()
    @State private var mutationID = UUID()
    private var conflict: Bool { base?.version != latest?.version }
    private var hasUnsavedChanges: Bool {
        guard let base else { return false }
        return title != base.title || owner != (base.owner ?? "") || kind != base.kind || status != base.status ||
            precision != (base.deadline?.precision ?? "unknown") || day != (base.deadline?.day ?? "") ||
            instant != (base.deadline?.instant ?? "") || timezone != (base.deadline?.timezone ?? model.serverTimeZoneIdentifier ?? "UTC") ||
            duplicate != (base.duplicate_of ?? "") || userNote != (base.user_note ?? "") || !resets.isEmpty
    }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        Form {
            if let base {
                if prioritizesWriting { personalWriting }
                Section(L10n.text("Review")) {
                    Text(L10n.text("Unsaved edits remain here if saving fails. Leaving this screen discards them.")).font(.caption)
                    ErrorNote(message: error)
                    if let saved { Text(L10n.key(saved)).font(.caption).foregroundStyle(Theme.accent) }
                    if conflict, let latest {
                        Text(L10n.text("Saved version changed: \(latest.title) · \(latest.owner ?? L10n.text("Owner unknown")) · \(latest.deadline?.label ?? L10n.text("Deadline unknown")) · \(L10n.status(latest.status))")).font(.caption)
                        Button(L10n.text("Use latest version as save base (keep draft)")) { self.base = latest }
                    }
                    TextField(L10n.text("Title"), text: $title, axis: .vertical)
                    Picker(L10n.text("Kind"), selection: $kind) {
                        ForEach(InboxItem.kinds, id: \.self) { value in Text(L10n.status(value)).tag(value) }
                    }
                    if kind != "reflection" && kind != "note" {
                        TextField(L10n.text("Owner unknown"), text: $owner)
                        Picker(L10n.text("Deadline precision"), selection: $precision) {
                            Text(L10n.text("Unknown / unresolved")).tag("unknown"); Text(L10n.text("Date only")).tag("date"); Text(L10n.text("Exact instant")).tag("datetime")
                        }
                        if precision == "date" { TextField("YYYY-MM-DD", text: $day).textInputAutocapitalization(.never) }
                        if precision == "datetime" { TextField(L10n.text("ISO instant with UTC offset"), text: $instant).textInputAutocapitalization(.never) }
                        TextField(L10n.text("Deadline timezone"), text: $timezone).textInputAutocapitalization(.never)
                    }
                    Picker(L10n.text("Status"), selection: $status) {
                        Text(L10n.text("Pending")).tag("pending")
                        Text(kind == "question" ? L10n.text("Answered") : (kind == "reflection" || kind == "note") ? L10n.text("Reviewed") : L10n.text("Completed")).tag("completed")
                        Text(L10n.text("Dismissed")).tag("dismissed")
                    }
                    if status == "dismissed" {
                        Picker(L10n.text("Duplicate of"), selection: $duplicate) {
                            Text(L10n.text("Ordinary dismissal")).tag("")
                            ForEach(alternatives.filter { $0.id != id && $0.duplicate_of == nil }) { Text($0.title).tag($0.id) }
                        }
                    }
                    if status == "pending", let action = completionAction {
                        Button(action) { status = "completed"; Task { await save() } }
                            .disabled(busy || conflict || latest == nil || userNote.unicodeScalars.count > 8000)
                    }
                    Button(busy ? L10n.text("Saving…") : L10n.text("Save review")) { Task { await save() } }.disabled(busy || conflict || latest == nil || userNote.unicodeScalars.count > 8000)
                }
                if !prioritizesWriting { personalWriting }
                if let latest, latest.canSuggestFollowUps, !conflict {
                    Section(L10n.text("Calendar & reminders")) {
                        if hasUnsavedChanges {
                            Text(L10n.text("Save your review first. Then choose a suggested event or reminder to add to your apps.")).font(.caption).foregroundStyle(.secondary)
                        }
                        InboxFollowUpLinks(item: latest)
                            .id("\(latest.id).\(latest.version)")
                            .disabled(hasUnsavedChanges)
                    }
                }
                Section(L10n.text("Extracted values / reset overrides")) {
                    Text("\(L10n.status(base.proposal.kind)): \(base.proposal.title)\n\(base.proposal.owner ?? L10n.text("Owner unknown")) · \(base.proposal.deadline?.label ?? L10n.text("Deadline unknown"))").font(.caption)
                    ForEach(["title", "kind", "owner", "deadline"], id: \.self) { field in
                        Button(resets.contains(field) ? L10n.text("\(L10n.status(field)) resets on save") : L10n.text("Use extracted \(L10n.status(field))")) { reset(field, from: base.proposal) }
                    }
                }
                Section(L10n.text("Transcript evidence")) {
                    if base.health != "current" { Text(L10n.text("Evidence changed or was withdrawn. Prior excerpts are historical; review before relying on this item.")).foregroundStyle(Theme.accent) }
                    if kind == "question" { Text(L10n.text("No answer found in the scanned context; other recordings may contain one.")).font(.caption) }
                    ForEach(Array((latest?.evidence ?? []).enumerated()), id: \.offset) { _, span in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(span.quote).textSelection(.enabled)
                            Text(L10n.text("\(span.started_at) · \(span.current ? L10n.text("Current transcript") : L10n.text("Prior transcript")) · chunk timing\n\(span.source_id)")).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            if span.audio_available {
                                Button(L10n.text("Play source audio")) { Task { await play(span.source_id) } }.disabled(busy)
                            } else { Text(L10n.text("Original audio unavailable")).font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                }
                Section(L10n.text("Change history")) {
                    ForEach(latest?.history ?? []) { event in Text("\(event.created_at) · \(L10n.status(event.kind))").font(.caption) }
                }
            } else {
                ProgressView(L10n.text("Loading inbox item…")); ErrorNote(message: error)
                Button(L10n.text("Retry")) { Task { await load() } }
            }
        }.omiScreen().disabled(busy).navigationTitle(L10n.text("Review item")).navigationBarTitleDisplayMode(.inline)
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
            .onChange(of: userNote) { _, value in if !value.isEmpty { resets.remove("user_note") } }
    }
    private var prioritizesWriting: Bool { kind == "question" || kind == "reflection" || kind == "note" }
    private var completionAction: String? {
        switch kind {
        case "question": L10n.text("Mark answered")
        case "reflection", "note": L10n.text("Mark reviewed")
        case "commitment", "deadline": L10n.text("Complete")
        default: nil
        }
    }
    private var personalWriting: some View {
        Section {
            if prioritizesWriting { Text(title).font(.headline) }
            TextField(kind == "question" ? L10n.text("Your answer") : L10n.text("Your notes"), text: $userNote, axis: .vertical)
                .lineLimit(4...12).accessibilityIdentifier("inbox.userNote")
            Text("\(userNote.unicodeScalars.count)/8000").font(.caption).foregroundStyle(.secondary)
            Button(L10n.text("Clear your writing")) { userNote = ""; resets.insert("user_note") }
                .disabled(userNote.isEmpty && base?.user_note == nil)
        } header: { Text(kind == "question" ? L10n.text("Your answer") : L10n.text("Your notes")) } footer: {
            Text(L10n.text("Your writing is personal context, not recorded evidence. Saving it does not complete this item."))
        }
    }
    private func fill(_ item: InboxItem) {
        title = item.title; owner = item.owner ?? ""; kind = item.kind; status = item.status; duplicate = item.duplicate_of ?? ""
        userNote = item.user_note ?? ""
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
            self.error = L10n.error(error)
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
        do { client = try model.client() } catch { self.error = L10n.error(error); return }
        mutationID = token; busy = true; saved = nil; error = nil
        defer { if mutationID == token { busy = false } }
        var changes: [String: Any] = [:]
        if title != base.title { changes["title"] = title }
        if kind != base.kind { changes["kind"] = kind }
        if owner != (base.owner ?? "") { changes["owner"] = owner.isEmpty ? NSNull() : owner as Any }
        if userNote != (base.user_note ?? "") { changes["user_note"] = userNote.isEmpty ? NSNull() : userNote as Any }
        if precision != (base.deadline?.precision ?? "unknown") || day != (base.deadline?.day ?? "") || instant != (base.deadline?.instant ?? "") || timezone != (base.deadline?.timezone ?? model.serverTimeZoneIdentifier ?? "UTC") {
            changes["deadline"] = ["precision": precision, "day": precision == "date" ? day as Any : NSNull(), "instant": precision == "datetime" ? instant as Any : NSNull(), "timezone": timezone, "basis": L10n.source("user edit")]
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
            self.error = (error as? BackendFailure)?.status == 409 ? L10n.text("Saved version changed. Your draft is preserved. Refresh and compare before applying it.") : L10n.error(error)
        }
    }
    private func accept(_ result: InboxItem) {
        self.base = result; latest = result; fill(result); resets = []; operation = nil
        saved = L10n.text("Saved to your local backend.")
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
            self.error = L10n.error(error)
        }
    }
}

import SwiftUI
import OmiCore

struct InboxLink: View {
    @Environment(AppModel.self) private var model
    @State private var count: Int?
    @State private var countRequest = UUID()
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        NavigationLink { InboxView() } label: {
            Label(count.map { L10n.text("Inbox · \($0) pending across days") } ?? L10n.text("Inbox · across all days"), systemImage: "checklist")
        }.onAppear { Task { await refresh() } }
            .onChange(of: model.archiveIdentity) { _, _ in Task { await refresh() } }
    }
    private func refresh() async {
        let address = model.archiveIdentity, request = UUID()
        countRequest = request
        let updated = try? await model.client().decoded("v1/inbox", query: ["limit": "1"], as: InboxPage.self).pending
        guard !Task.isCancelled, address == model.archiveIdentity, countRequest == request else { return }
        count = updated
    }
}

struct InboxView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var status = "pending"
    @State private var kind = ""
    @State private var due = ""
    @State private var page: InboxPage?
    @State private var error: String?
    @State private var busy = false
    @State private var offset = 0
    @State private var start = Calendar.current.date(byAdding: .day, value: -6, to: Date())!
    @State private var end = Date()
    @State private var message: String?
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var queryKey: String { "\(model.archiveIdentity)|\(status)|\(kind)|\(due)|\(offset)" }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Section {
                EditorialTitle(eyebrow: L10n.text("Follow through"), title: L10n.text("Keep things moving.")).editorialRow()
            }
            Section {
                Picker(L10n.text("Show"), selection: $status) {
                    Text(L10n.text("Pending")).tag("pending"); Text(L10n.text("Completed / answered")).tag("completed"); Text(L10n.text("Dismissed")).tag("dismissed")
                }
                DisclosureGroup {
                    Picker(L10n.text("Kind"), selection: $kind) {
                        Text(L10n.text("All kinds")).tag(""); Text(L10n.text("Commitments")).tag("commitment"); Text(L10n.text("Deadlines")).tag("deadline"); Text(L10n.text("Questions")).tag("question")
                    }
                    Picker(L10n.text("Deadline"), selection: $due) {
                        Text(L10n.text("All deadlines")).tag(""); Text(L10n.text("Overdue")).tag("overdue"); Text(L10n.text("Upcoming")).tag("upcoming"); Text(L10n.text("Unknown / unresolved")).tag("unknown")
                    }
                } label: {
                    Label(filterSummary, systemImage: "line.3.horizontal.decrease")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                if busy { ProgressView() }
                ErrorNote(message: error)
                if let message { Text(L10n.key(message)).font(.caption) }
            }
            if let page {
                Section(L10n.text("\(L10n.status(status)) · all days")) {
                    ForEach(page.items) { item in
                        NavigationLink { InboxDetail(id: item.id) } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(L10n.status(item.kind) + (item.reviewed_at == nil ? L10n.text(" · Unreviewed") : L10n.text(" · Reviewed"))).font(.caption).foregroundStyle(.secondary)
                                Text(item.title)
                                Text("\(item.owner ?? L10n.text("Owner unknown")) · \(item.deadline?.label ?? L10n.text("Deadline unknown"))\(item.overdue == true ? L10n.text(" · Overdue") : "")").font(.caption).foregroundStyle(.secondary)
                                if item.health != "current" { Text(L10n.text("Evidence \(L10n.status(item.health))")).font(.caption).foregroundStyle(Theme.accent) }
                            }
                        }
                    }
                    if page.items.isEmpty { Text(page.coverage.jobs["done", default: 0] == 0 ? L10n.text("No scan has completed yet. Capture new speech or scan earlier recordings below.") : L10n.text("No items match. Extraction may be incomplete; this does not establish that no obligations exist.")).foregroundStyle(.secondary) }
                    if page.total > 50 {
                        HStack {
                            Button(L10n.text("Previous")) { offset = max(0, offset - 50) }.disabled(offset == 0)
                            Spacer(); Text("\(offset + 1)–\(min(offset + 50, page.total)) / \(page.total)").font(.caption); Spacer()
                            Button(L10n.text("Next")) { offset += 50 }.disabled(offset + 50 >= page.total)
                        }
                    }
                }
            }
            if page == nil && error == nil { ProgressView(L10n.text("Opening your inbox…")) }
            Section {
                if let page {
                    DisclosureGroup {
                        Text(L10n.text("\(page.coverage.jobs["done", default: 0]) scanned · \(page.coverage.jobs["queued", default: 0]) waiting · \(page.coverage.jobs["running", default: 0]) extracting · \(page.coverage.jobs["failed", default: 0]) failed · \(page.coverage.unscanned_sources) unscanned sources"))
                            .font(.caption).foregroundStyle(.secondary)
                        ForEach(page.recent_jobs.filter { $0.error != nil }) { job in
                            Text(L10n.text("\(job.recording_start.map { $0 + " · " } ?? "")\(L10n.status(job.state)): \(L10n.key(job.error ?? "")) (attempt \(job.attempts))")).font(.caption)
                        }
                    } label: {
                        Label(page.coverage.jobs["failed", default: 0] > 0 ? L10n.text("Scan coverage · \(page.coverage.jobs["failed", default: 0]) failed") : L10n.text("Scan status & coverage"), systemImage: "checkmark.shield")
                    }
                }
                DisclosureGroup(L10n.text("Scan earlier recordings / retry")) {
                    DatePicker(L10n.text("From"), selection: $start, displayedComponents: .date)
                    DatePicker(L10n.text("Through"), selection: $end, displayedComponents: .date)
                    Text(L10n.text("Up to 31 days in \(diary.calendar.timeZone.identifier). New transcripts scan automatically. The local worker must be running."))
                        .font(.caption).foregroundStyle(.secondary)
                    Button(L10n.text("Scan / retry failed work")) { Task { await scan() } }.disabled(busy)
                }.environment(\.calendar, diary.calendar).environment(\.timeZone, diary.calendar.timeZone)
            } header: { Text(L10n.text("Recording coverage")) } footer: {
                Text(L10n.text("Proposals come from your recorded conversations and need your review."))
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle(L10n.text("Inbox")).navigationBarTitleDisplayMode(.inline)
            .task(id: queryKey) { await load() }
            .task(id: scenePhase) {
                guard scenePhase == .active else { return }
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .seconds(10)) } catch { return }
                    await load()
                }
            }
            .refreshable { await load() }
            .onChange(of: status) { _, _ in offset = 0 }
            .onChange(of: kind) { _, _ in offset = 0 }
            .onChange(of: due) { _, _ in offset = 0 }
            .onChange(of: scenePhase) { _, phase in if phase == .active { Task { await load() } } }
    }
    private var filterSummary: String {
        let kindLabel: String
        switch kind {
        case "commitment": kindLabel = L10n.text("Commitments")
        case "deadline": kindLabel = L10n.text("Deadlines")
        case "question": kindLabel = L10n.text("Questions")
        default: kindLabel = L10n.text("All kinds")
        }
        let dueLabel: String
        switch due {
        case "unknown": dueLabel = L10n.text("Unresolved deadlines")
        case "overdue": dueLabel = L10n.text("Overdue deadlines")
        case "upcoming": dueLabel = L10n.text("Upcoming deadlines")
        default: dueLabel = L10n.text("Any deadline")
        }
        return "\(kindLabel) · \(dueLabel)"
    }
    private func load() async {
        let key = queryKey
        do {
            let result = try await model.client().decoded("v1/inbox", query: ["status": status, "limit": "50", "offset": "\(offset)"].merging(kind.isEmpty ? [:] : ["kind": kind]) { _, new in new }.merging(due.isEmpty ? [:] : ["due": due]) { _, new in new }, as: InboxPage.self)
            guard !Task.isCancelled, key == queryKey else { return }
            page = result; error = nil
        } catch { if !Task.isCancelled, key == queryKey { self.error = L10n.error(error) } }
    }
    private func scan() async {
        busy = true; defer { busy = false }
        do {
            let result = try await model.client().request("v1/inbox/extractions", method: "POST", body: ["start_day": diary.dayKey(start), "end_day": diary.dayKey(end), "retry_failed": true])
            message = L10n.text("\(result["total"].integer) windows queued or already scanned."); await load()
        } catch { self.error = L10n.error(error) }
    }
}

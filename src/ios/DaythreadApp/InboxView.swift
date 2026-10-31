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
    let day: String?
    let topicSources: Set<String>?
    let topicPassages: [InboxTopicPassage]
    let filters: DayFilterScope
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var status = "pending"
    @State private var kind = ""
    @State private var due = ""
    @State private var page: InboxPage?
    @State private var items: [InboxItem] = []
    @State private var error: String?
    @State private var busy = false
    @State private var offset = 0
    @State private var start = Calendar.current.date(byAdding: .day, value: -6, to: Date())!
    @State private var end = Date()
    @State private var message: String?
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var queryKey: String { "\(model.archiveIdentity)|\(model.archiveRevision)|\(day ?? "")|\(filters.identity)|\(status)|\(kind)|\(due)|\(offset)|\(topicSources?.sorted().joined() ?? "")|\(topicPassages)" }

    init(day: String? = nil, topicSources: Set<String>? = nil, topicPassages: [InboxTopicPassage] = [], filters: DayFilterScope = .init()) {
        self.day = day; self.topicSources = topicSources; self.topicPassages = topicPassages; self.filters = filters
    }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            if day != nil || filters.isFiltered {
                Section {
                    if let day { Text(day).font(.subheadline).foregroundStyle(.secondary) }
                    if filters.isFiltered { Text(L10n.text("Only recordings matching every selected filter are included.")).font(.caption).foregroundStyle(.secondary) }
                }
            }
            Section {
                Picker(L10n.text("Show"), selection: $status) {
                    Text(L10n.text("Pending")).tag("pending"); Text(L10n.text("Completed / answered / reviewed")).tag("completed"); Text(L10n.text("Dismissed")).tag("dismissed")
                }
                DisclosureGroup {
                    Picker(L10n.text("Kind"), selection: $kind) {
                        Text(L10n.text("All kinds")).tag("")
                        ForEach(InboxItem.kinds, id: \.self) { value in Text(L10n.status(value)).tag(value) }
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
                Section(day.map { L10n.status(status) + " · " + $0 } ?? L10n.text("\(L10n.status(status)) · all days")) {
                    ForEach(items) { item in
                        NavigationLink { InboxDetail(id: item.id) } label: {
                            InboxOverviewRow(item: item)
                        }
                    }
                    if items.isEmpty { Text(page.coverage.jobs["done", default: 0] == 0 ? L10n.text("No scan has completed yet. Capture new speech or scan earlier recordings below.") : L10n.text("No items match. Extraction may be incomplete; this does not establish that no obligations exist.")).foregroundStyle(.secondary) }
                    if page.total > 50 && topicSources == nil {
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
                if !filters.isFiltered { DisclosureGroup(L10n.text("Scan earlier recordings / retry")) {
                    DatePicker(L10n.text("From"), selection: $start, displayedComponents: .date)
                    DatePicker(L10n.text("Through"), selection: $end, displayedComponents: .date)
                    Text(L10n.text("Up to 31 days in \(diary.calendar.timeZone.identifier). New transcripts scan automatically. The local worker must be running."))
                        .font(.caption).foregroundStyle(.secondary)
                    Button(L10n.text("Scan / retry failed work")) { Task { await scan() } }.disabled(busy)
                }.environment(\.calendar, diary.calendar).environment(\.timeZone, diary.calendar.timeZone) }
            } header: { Text(L10n.text("Recording coverage")) } footer: {
                Text(L10n.text("Proposals come from your recorded conversations and need your review."))
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle(L10n.text("Inbox")).navigationBarTitleDisplayMode(.large)
            .task(id: queryKey) { page = nil; items = []; error = nil; await load() }
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
        let kindLabel = kind.isEmpty ? L10n.text("All kinds") : L10n.status(kind)
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
            var query = ["status": status, "limit": topicSources == nil ? "50" : "200", "offset": topicSources == nil ? "\(offset)" : "0"].merging(filters.query) { _, new in new }
            if let day { query["day"] = day }
            if !kind.isEmpty { query["kind"] = kind }
            if !due.isEmpty { query["due"] = due }
            let client = try model.client()
            let result = try await client.decoded("v1/inbox", query: query, as: InboxPage.self)
            var incoming = result.items
            if topicSources != nil {
                while incoming.count < result.total {
                    try Task.checkCancellation()
                    query["offset"] = "\(incoming.count)"
                    let next = try await client.decoded("v1/inbox", query: query, as: InboxPage.self)
                    if next.items.isEmpty { break }
                    incoming += next.items
                }
            }
            guard !Task.isCancelled, key == queryKey else { return }
            page = result
            items = topicSources.map { ids in incoming.filter { $0.matchesTopic(sources: ids, passages: topicPassages) } } ?? incoming
            error = nil
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

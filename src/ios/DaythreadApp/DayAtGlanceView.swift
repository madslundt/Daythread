import SwiftUI
import OmiCore

/// One overview preserves the different things a conversation can leave behind.
struct DayAtGlanceSections: View {
    let day: String
    var topicSources: Set<String>? = nil
    var topicPassages: [InboxTopicPassage] = []
    var topicVersions: [String: String] = [:]
    var filters = DayFilterScope()
    var topicTitle: String? = nil
    var contentLanguage: String? = nil
    var compact = false
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @Environment(\.locale) private var interfaceLocale
    @State private var items: [InboxItem] = []
    @State private var decisions: [DecisionRecord] = []
    @State private var coverage: InboxCoverage?
    @State private var inboxError: String?
    @State private var decisionError: String?
    @State private var inboxScanError: String?
    @State private var inboxScanMessage: String?
    @State private var decisionScanError: String?
    @State private var decisionScanMessage: String?
    @State private var loading = false
    @State private var scanning = false
    @State private var findingDecisions = false
    @State private var scanID = UUID()
    @State private var decisionScanID = UUID()
    @State private var refresh = UUID()
    @State private var loadedScope = ""
    @State private var visibleDecisions = 3
    @State private var category = ""
    @State private var labelState = DisplayLabelTranslationState()
    @State private var labelsRefresh = UUID()
    private var scope: String {
        model.archiveIdentity + day + filters.identity + (topicSources?.sorted().joined() ?? "day") + String(describing: topicPassages) + topicVersions.sorted { $0.key < $1.key }.map { $0.key + $0.value }.joined()
    }
    private var key: String { scope + model.archiveRevision + refresh.uuidString }
    private var scopedItems: [InboxItem] { loadedScope == scope ? items : [] }
    private var scopedDecisions: [DecisionRecord] { loadedScope == scope ? decisions : [] }
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var labels: [DisplayTranslations.Label] {
        let intents: [InboxIntent] = [.todo, .questions, .schedule, .reflection, .information]
        let rows = intents.filter { category.isEmpty || category == $0.rawValue }.flatMap { Array(orderedItems($0).prefix(compact ? 3 : 5)) }
        let choices = category.isEmpty || category == "decisions" ? Array(scopedDecisions.prefix(visibleDecisions)) : []
        return rows.map { .init(reference: .init(kind: "inbox", id: $0.id, version: $0.version), title: $0.title) }
            + choices.map { .init(reference: .init(kind: "decision", id: $0.id, version: $0.version), title: $0.title) }
    }
    private var labelsKey: String { scope + model.archiveRevision + labelsRefresh.uuidString + DisplayTranslations.identity(language: contentLanguage, labels: labels) }
    private var labelValues: [DisplayTranslations.Reference: String] { labelState.values(for: labelsKey) }

    var body: some View {
        let _ = interfaceLocale
        Section {
            if !compact { Text(diary.date(for: day).map { diary.label($0, template: "EEEE d MMMM") } ?? day).font(.subheadline).foregroundStyle(.secondary) }
            if let topicTitle { Text(L10n.text("Topic") + ": " + topicTitle).font(.subheadline.weight(.medium)) }
            if !compact { Text(L10n.text("Proposals come from your recorded conversations and need your review.")).font(.caption).foregroundStyle(.secondary) }
            if labelState.isLoading(key: labelsKey, language: contentLanguage, labels: labels) { ProgressView(L10n.text("Translating…")) }
            if let error = labelState.error(for: labelsKey) {
                ErrorNote(message: error)
                Button(L10n.text("Retry")) { labelsRefresh = UUID() }
            }
            if loading || loadedScope != scope { ProgressView(L10n.text("Loading action points…")) }
            ErrorNote(message: inboxError)
            if inboxError != nil { Button(L10n.text("Retry")) { refresh = UUID() } }
            Picker(L10n.text("Show"), selection: $category) {
                Text(L10n.text("All categories")).tag("")
                ForEach([InboxIntent.todo, .questions, .schedule], id: \.rawValue) { intent in Text(intent.title).tag(intent.rawValue) }
                Text(L10n.text("Decisions")).tag("decisions")
                ForEach([InboxIntent.reflection, .information], id: \.rawValue) { intent in Text(intent.title).tag(intent.rawValue) }
            }.accessibilityIdentifier("day.overviewCategory")
            NavigationLink { scopedInbox } label: { Label(L10n.text("Review all items"), systemImage: "tray") }
        } header: { Text(L10n.text("Your day at a glance")) }
        .task(id: key + String(describing: phase)) {
            guard phase == .active else { return }
            if loadedScope != scope {
                items = []; decisions = []; coverage = nil; visibleDecisions = compact ? 3 : 5; loadedScope = scope
            }
            await load()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                await load()
            }
        }
        .onAppear { refresh = UUID() }
        .onChange(of: scope) { _, _ in invalidateScans() }
        .task(id: labelsKey) { await labelState.load(key: labelsKey, language: contentLanguage, labels: labels, client: model.client) }
        if category.isEmpty || category == InboxIntent.todo.rawValue { inboxGroup(.todo) }
        if category.isEmpty || category == InboxIntent.questions.rawValue { inboxGroup(.questions) }
        if category.isEmpty || category == InboxIntent.schedule.rawValue { inboxGroup(.schedule) }
        if category.isEmpty || category == "decisions" { decisionGroup }
        if category.isEmpty || category == InboxIntent.reflection.rawValue { inboxGroup(.reflection) }
        if category.isEmpty || category == InboxIntent.information.rawValue { inboxGroup(.information) }
        if topicSources == nil {
            if compact {
                Section {
                    DisclosureGroup(L10n.text("Scan status & coverage")) {
                        scanControls
                        decisionScanControls
                    }
                }
            } else { scanSection }
        }
    }

    private var scopedInbox: some View { InboxView(day: day, topicSources: topicSources, topicPassages: topicPassages, filters: filters) }

    private func orderedItems(_ intent: InboxIntent) -> [InboxItem] {
        scopedItems.filter { $0.intent == intent }.enumerated().sorted {
            if ($0.element.overdue == true) != ($1.element.overdue == true) { return $0.element.overdue == true }
            let lhs = $0.element.health != "current", rhs = $1.element.health != "current"
            return lhs == rhs ? $0.offset < $1.offset : lhs
        }.map(\.element)
    }
    private func inboxGroup(_ intent: InboxIntent) -> some View {
        let relevant = orderedItems(intent)
        return Group {
            if !compact || !relevant.isEmpty {
                Section {
                    ForEach(relevant.prefix(compact ? 3 : 5)) { item in
                        NavigationLink { InboxDetail(id: item.id) } label: {
                            InboxOverviewRow(item: item, displayTitle: labelValues[.init(kind: "inbox", id: item.id, version: item.version)], compact: compact)
                        }
                    }
                    if !compact && relevant.isEmpty && !loading && inboxError == nil && loadedScope == scope {
                        Text(L10n.text("No items in this group yet. Scans may still be incomplete.")).font(.caption).foregroundStyle(.secondary)
                    }
                    if relevant.count > (compact ? 3 : 5) { NavigationLink(L10n.text("Review all items")) { scopedInbox } }
                } header: { Text(intent.title) }
            }
        }
    }

    private var decisionGroup: some View {
        Group {
            if !compact || !scopedDecisions.isEmpty || decisionError != nil {
                Section {
                    ErrorNote(message: decisionError)
                    if decisionError != nil { Button(L10n.text("Refresh decisions")) { refresh = UUID() } }
                    ForEach(scopedDecisions.prefix(visibleDecisions)) { record in
                        NavigationLink { DecisionDetail(id: record.id) } label: {
                            VStack(alignment: .leading, spacing: 7) {
                                DecisionRow(record: record, displayTitle: labelValues[.init(kind: "decision", id: record.id, version: record.version)], showsDate: !compact)
                                if !compact { Text(L10n.text("Review decision")).font(.caption.weight(.semibold)).foregroundStyle(Theme.accent) }
                            }
                        }
                    }
                    if scopedDecisions.count > visibleDecisions {
                        Button(L10n.text("Load more decisions")) { visibleDecisions += 10 }
                    }
                    if !compact && scopedDecisions.isEmpty && !loading && decisionError == nil && loadedScope == scope {
                        Text(topicSources == nil ? L10n.text("No decision records for this day.") : L10n.text("No current decision choices linked to an identified passage in this topic."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if !compact {
                        Text(L10n.text("Decisions are found automatically from new transcripts. Review original evidence before confirming a record. Confirmation records accuracy; it does not make a tentative choice final."))
                            .font(.caption).foregroundStyle(.secondary)
                        if topicSources == nil { decisionScanControls }
                    }
                } header: { Text(L10n.text("Decisions")) }
            }
        }
    }

    private var decisionScanControls: some View {
        Group {
            Button { Task { await scanDecisions() } } label: { Label(filters.isFiltered ? L10n.text("Scan whole day") : L10n.text("Find decisions"), systemImage: "arrow.triangle.branch") }.disabled(findingDecisions)
            if findingDecisions { ProgressView(L10n.text("Finding earlier decisions…")) }
            if let decisionScanMessage { Text(decisionScanMessage).font(.caption).foregroundStyle(.secondary) }
            ErrorNote(message: decisionScanError)
        }
    }

    private var scanSection: some View {
        Section { scanControls } header: { Text(L10n.text("Follow-up extraction")) }
    }

    private var scanControls: some View {
        Group {
            Button { Task { await scanDay() } } label: { Label(filters.isFiltered ? L10n.text("Scan whole day") : L10n.text("Find items from this day"), systemImage: "text.magnifyingglass") }.disabled(scanning)
            if scanning { ProgressView(L10n.text("Finding follow-ups")) }
            if let inboxScanMessage { Text(inboxScanMessage).font(.caption).foregroundStyle(.secondary) }
            ErrorNote(message: inboxScanError)
            if let coverage {
                if coverage.jobs["queued", default: 0] + coverage.jobs["running", default: 0] > 0 {
                    Text(L10n.text("Action points are being extracted from your recordings.")).font(.caption).foregroundStyle(.secondary)
                }
                if coverage.jobs["failed", default: 0] > 0 {
                    Text(L10n.text("Some follow-ups or notes could not be extracted.")).font(.caption).foregroundStyle(.secondary)
                    Button(filters.isFiltered ? L10n.text("Retry whole day") : L10n.text("Retry follow-up extraction")) { Task { await scanDay() } }.disabled(scanning)
                }
                DisclosureGroup(L10n.text("Scan status & coverage")) {
                    Text(L10n.text("\(coverage.jobs["done", default: 0]) scanned · \(coverage.jobs["queued", default: 0]) waiting · \(coverage.jobs["running", default: 0]) extracting · \(coverage.jobs["failed", default: 0]) failed · \(coverage.unscanned_sources) unscanned sources"))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            InboxLink()
        }
    }

    private func load() async {
        let requested = key
        loading = items.isEmpty && decisions.isEmpty
        defer { if key == requested { loading = false } }
        async let inbox: Void = loadInbox(requested)
        async let choices: Void = loadDecisions(requested)
        _ = await (inbox, choices)
    }

    private func loadInbox(_ requested: String) async {
        do {
            let client = try model.client()
            async let open = fetchInbox(client, status: "pending", requested: requested)
            async let done = fetchInbox(client, status: "completed", requested: requested)
            let (pending, reviewed) = try await (open, done)
            guard !Task.isCancelled, key == requested else { return }
            // Versioned snapshots can straddle a status edit. Keep its latest version
            // and retain reviewed information without reopening completed obligations.
            var latest: [String: InboxItem] = [:]
            for item in pending.items + reviewed.items {
                if latest[item.id].map({ $0.version <= item.version }) ?? true { latest[item.id] = item }
            }
            var seen: Set<String> = []
            let incoming = (pending.items + reviewed.items).compactMap { item -> InboxItem? in
                guard seen.insert(item.id).inserted, let current = latest[item.id], current.appearsInDayOverview else { return nil }
                return current
            }
            items = topicSources.map { ids in incoming.filter { $0.matchesTopic(sources: ids, passages: topicPassages) } } ?? incoming
            coverage = pending.coverage; inboxError = nil
        } catch { if !Task.isCancelled, key == requested { inboxError = L10n.error(error) } }
    }

    private func fetchInbox(_ client: BackendClient, status: String, requested: String) async throws -> (items: [InboxItem], coverage: InboxCoverage) {
        var query = ["day": day, "status": status, "limit": "200", "offset": "0"].merging(filters.query) { _, new in new }
        let page = try await client.decoded("v1/inbox", query: query, as: InboxPage.self)
        var incoming = page.items
        while incoming.count < page.total {
            guard !Task.isCancelled, key == requested else { throw CancellationError() }
            query["offset"] = "\(incoming.count)"
            let next = try await client.decoded("v1/inbox", query: query, as: InboxPage.self)
            if next.items.isEmpty { break }
            incoming += next.items
        }
        return (incoming, page.coverage)
    }

    private func loadDecisions(_ requested: String) async {
        do {
            let client = try model.client()
            var query = ["day": day, "include_dismissed": "false", "limit": "200", "offset": "0"].merging(filters.query) { _, new in new }
            let page: DecisionList = try await client.decisionRequest("v1/decisions", query: query)
            var incoming = page.items
            while incoming.count < page.total {
                guard !Task.isCancelled, key == requested else { return }
                query["offset"] = "\(incoming.count)"
                let next: DecisionList = try await client.decisionRequest("v1/decisions", query: query)
                if next.items.isEmpty { break }
                incoming += next.items
            }
            guard !Task.isCancelled, key == requested else { return }
            let active = incoming.filter {
                $0.content != nil && $0.successor_id == nil && $0.review_status != "dismissed" && $0.evidence_condition != "removed"
            }
            decisions = topicSources.map { ids in active.filter { $0.matchesTopic(sources: ids, passages: topicPassages, versions: topicVersions) } } ?? active
            decisionError = nil
        } catch { if !Task.isCancelled, key == requested { decisionError = L10n.error(error) } }
    }

    private func invalidateScans() {
        scanID = UUID(); decisionScanID = UUID(); scanning = false; findingDecisions = false
        inboxScanMessage = nil; inboxScanError = nil; decisionScanMessage = nil; decisionScanError = nil
    }
    private func scanDay() async {
        let requested = scope, token = UUID(), selectedDay = day
        scanID = token; scanning = true; inboxScanError = nil; inboxScanMessage = nil
        defer { if scanID == token { scanning = false } }
        do {
            let result = try await model.client().request("v1/inbox/extractions", method: "POST", body: ["start_day": selectedDay, "end_day": selectedDay, "retry_failed": true])
            guard !Task.isCancelled, scope == requested, scanID == token else { return }
            inboxScanMessage = L10n.text("\(result["total"].integer) windows queued or already scanned.")
            await load()
        } catch { if !Task.isCancelled, scope == requested, scanID == token { inboxScanError = L10n.error(error) } }
    }
    private func scanDecisions() async {
        let requested = scope, token = UUID(), selectedDay = day
        decisionScanID = token; findingDecisions = true; decisionScanError = nil; decisionScanMessage = nil
        defer { if decisionScanID == token { findingDecisions = false } }
        do {
            let client = try model.client()
            var request = DecisionExtractionRequest(day: selectedDay)
            while true {
                let result: DecisionExtraction = try await client.decisionRequest("v1/decision-extractions", method: "POST", body: request)
                guard !Task.isCancelled, scope == requested, decisionScanID == token else { return }
                guard result.status != "stale", result.status != "removed" else {
                    decisionScanMessage = nil
                    throw BackendFailure(message: L10n.text("Recorded evidence changed. Refresh this day before finding decisions again."))
                }
                decisionScanMessage = L10n.text("Decision scan: \(result.processed_sources) of \(result.total_sources) recordings")
                guard let next = result.next_run_id else { break }
                request = DecisionExtractionRequest(run_id: next)
            }
            await load()
        } catch { if !Task.isCancelled, scope == requested, decisionScanID == token { decisionScanError = L10n.error(error) } }
    }
}

extension DecisionRecord {
    func matchesTopic(sources: Set<String>, passages: [InboxTopicPassage], versions: [String: String]) -> Bool {
        guard evidence_condition == "current" || evidence_condition == "conflicted", let content else { return false }
        return content.choice.evidence.contains { anchor in
            sources.contains(anchor.source_id) && passages.contains {
                $0.containsCurrent(sourceID: anchor.source_id, sourceVersion: anchor.source_version, currentVersions: versions, start: anchor.start, end: anchor.end)
            }
        }
    }
}

private extension InboxIntent {
    var title: String {
        switch self {
        case .todo: L10n.text("To do")
        case .questions: L10n.text("Questions to answer")
        case .schedule: L10n.text("Events & reminders")
        case .reflection: L10n.text("Worth considering")
        case .information: L10n.text("Worth remembering")
        }
    }
}

struct InboxOverviewRow: View {
    let item: InboxItem
    var displayTitle: String? = nil
    var compact = false
    @Environment(\.locale) private var interfaceLocale
    private var nextAction: String {
        if item.status == "completed" { return item.kind == "question" ? L10n.text("Answered") : item.isReviewOnly ? L10n.text("Reviewed") : L10n.text("Completed") }
        if item.status == "dismissed" { return L10n.text("Reopen") }
        switch item.kind {
        case "question": return L10n.text("Answer question")
        case "event", "reminder": return L10n.text("Review event/reminder")
        case "reflection": return (item.user_note ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? L10n.text("Add your thoughts") : L10n.text("Mark reviewed")
        case "note": return item.reviewed_at == nil ? L10n.text("Read note") : L10n.text("Mark reviewed")
        default: return L10n.text("Complete")
        }
    }
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 6) {
            Text(displayTitle ?? item.title).foregroundStyle(.primary).lineLimit(compact ? 2 : nil)
            Text(L10n.status(item.kind) + " · " + (item.status == "completed" || item.reviewed_at != nil ? L10n.text("Reviewed") : L10n.text("Proposed"))).font(.caption).foregroundStyle(.secondary)
            if item.overdue == true { Label(L10n.text("Overdue"), systemImage: "exclamationmark.circle").font(.caption.weight(.semibold)).foregroundStyle(.orange) }
            if item.health != "current" { Text(L10n.text("Evidence \(L10n.status(item.health))")).font(.caption).foregroundStyle(.orange) }
            if let owner = item.owner, !owner.isEmpty { Text(owner).font(.caption).foregroundStyle(.secondary) }
            else if !compact && !item.isReviewOnly { Text(L10n.text("Owner unknown")).font(.caption).foregroundStyle(.secondary) }
            if let deadline = item.deadline {
                Text(L10n.text("Deadline") + ": " + deadline.label).font(.caption).foregroundStyle(.secondary)
            } else if !compact && item.intent == .todo {
                Text(L10n.text("Deadline unknown")).font(.caption).foregroundStyle(.secondary)
            } else if !compact && item.intent == .schedule {
                Text(L10n.text("Date unknown")).font(.caption).foregroundStyle(.secondary)
            }
            if let note = item.user_note, !note.isEmpty {
                Text((item.kind == "question" ? L10n.text("Your answer") : L10n.text("Your notes")) + ": " + note).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            if !compact { Text(nextAction).font(.caption.weight(.semibold)).foregroundStyle(Theme.accent) }
        }.padding(.vertical, 5)
    }
}

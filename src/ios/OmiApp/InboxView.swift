import SwiftUI
import OmiCore

struct InboxLink: View {
    @Environment(AppModel.self) private var model
    @State private var count: Int?
    @State private var countRequest = UUID()
    var body: some View {
        NavigationLink { InboxView() } label: {
            Label(count.map { "Inbox · \($0) pending across days" } ?? "Inbox · across all days", systemImage: "checklist")
        }.onAppear { Task { await refresh() } }
            .onChange(of: model.backendAddress) { _, _ in Task { await refresh() } }
    }
    private func refresh() async {
        let address = model.backendAddress, request = UUID()
        countRequest = request
        let updated = try? await model.client().decoded("v1/inbox", query: ["limit": "1"], as: InboxPage.self).pending
        guard !Task.isCancelled, address == model.backendAddress, countRequest == request else { return }
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
    private var queryKey: String { "\(model.backendAddress)|\(status)|\(kind)|\(due)|\(offset)" }

    var body: some View {
        List {
            Section {
                Text("Promises, deadlines and unanswered questions across all days. Model proposals need review.").font(.callout)
                Picker("Status", selection: $status) {
                    Text("Pending").tag("pending"); Text("Completed / answered").tag("completed"); Text("Dismissed").tag("dismissed")
                }
                Picker("Kind", selection: $kind) {
                    Text("All kinds").tag(""); Text("Commitments").tag("commitment"); Text("Deadlines").tag("deadline"); Text("Questions").tag("question")
                }
                Picker("Deadline", selection: $due) {
                    Text("All deadlines").tag(""); Text("Overdue").tag("overdue"); Text("Upcoming").tag("upcoming"); Text("Unknown / unresolved").tag("unknown")
                }
                if busy { ProgressView() }
                ErrorNote(message: error)
                if let message { Text(message).font(.caption) }
            }
            if let page {
                Section("Extraction coverage") {
                    Text("\(page.coverage.jobs["done", default: 0]) scanned · \(page.coverage.jobs["queued", default: 0]) waiting · \(page.coverage.jobs["running", default: 0]) extracting · \(page.coverage.jobs["failed", default: 0]) failed · \(page.coverage.unscanned_sources) unscanned sources").font(.caption)
                    ForEach(page.recent_jobs.filter { $0.error != nil }) { job in Text("\(job.recording_start.map { $0 + " · " } ?? "")\(job.state): \(job.error ?? "") (attempt \(job.attempts))").font(.caption) }
                }
                Section("\(status.capitalized) · all days") {
                    ForEach(page.items) { item in
                        NavigationLink { InboxDetail(id: item.id) } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(item.kind.capitalized + (item.reviewed_at == nil ? " · Unreviewed" : " · Reviewed")).font(.caption).foregroundStyle(.secondary)
                                Text(item.title)
                                Text("\(item.owner ?? "Owner unknown") · \(item.deadline?.label ?? "Deadline unknown")\(item.overdue == true ? " · Overdue" : "")").font(.caption).foregroundStyle(.secondary)
                                if item.health != "current" { Text("Evidence \(item.health.replacingOccurrences(of: "_", with: " "))").font(.caption).foregroundStyle(Theme.accent) }
                            }
                        }
                    }
                    if page.items.isEmpty { Text(page.coverage.jobs["done", default: 0] == 0 ? "No scan has completed yet. Capture new speech or scan earlier recordings below." : "No items match. Extraction may be incomplete; this does not establish that no obligations exist.").foregroundStyle(.secondary) }
                    if page.total > 50 {
                        HStack {
                            Button("Previous") { offset = max(0, offset - 50) }.disabled(offset == 0)
                            Spacer(); Text("\(offset + 1)–\(min(offset + 50, page.total)) / \(page.total)").font(.caption); Spacer()
                            Button("Next") { offset += 50 }.disabled(offset + 50 >= page.total)
                        }
                    }
                }
            }
            Section("Scan earlier recordings / retry") {
                DatePicker("From", selection: $start, displayedComponents: .date)
                DatePicker("Through", selection: $end, displayedComponents: .date)
                Text("Up to 31 days in \(diary.calendar.timeZone.identifier). New transcripts scan automatically. The local worker must be running.").font(.caption).foregroundStyle(.secondary)
                Button("Scan / retry failed work") { Task { await scan() } }.disabled(busy)
            }.environment(\.calendar, diary.calendar).environment(\.timeZone, diary.calendar.timeZone)
        }.navigationTitle("Inbox")
            .task(id: queryKey) { await load() }
            .task { while !Task.isCancelled { try? await Task.sleep(for: .seconds(10)); if !Task.isCancelled { await load() } } }
            .refreshable { await load() }
            .onChange(of: status) { _, _ in offset = 0 }
            .onChange(of: kind) { _, _ in offset = 0 }
            .onChange(of: due) { _, _ in offset = 0 }
            .onChange(of: scenePhase) { _, phase in if phase == .active { Task { await load() } } }
    }
    private func load() async {
        let key = queryKey
        do {
            let result = try await model.client().decoded("v1/inbox", query: ["status": status, "limit": "50", "offset": "\(offset)"].merging(kind.isEmpty ? [:] : ["kind": kind]) { _, new in new }.merging(due.isEmpty ? [:] : ["due": due]) { _, new in new }, as: InboxPage.self)
            guard !Task.isCancelled, key == queryKey else { return }
            page = result; error = nil
        } catch { if !Task.isCancelled, key == queryKey { self.error = error.localizedDescription } }
    }
    private func scan() async {
        busy = true; defer { busy = false }
        do {
            let result = try await model.client().request("v1/inbox/extractions", method: "POST", body: ["start_day": diary.dayKey(start), "end_day": diary.dayKey(end), "retry_failed": true])
            message = "\(result["total"].integer) windows queued or already scanned."; await load()
        } catch { self.error = error.localizedDescription }
    }
}

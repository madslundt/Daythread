import SwiftUI
import OmiCore

/// The brief selected-week recap is separate from the detailed two-week review below it.
struct WeeklyRecapOverview: View {
    let week: String
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @Environment(\.locale) private var interfaceLocale
    @State private var overview: JSONValue = .null
    @State private var expanded = false
    @State private var generating = false
    @State private var error: String?
    @State private var requestID = UUID()
    @State private var operation: Task<Void, Never>?
    @State private var selectedTopic: SavedTopic?

    private struct SavedTopic: Identifiable {
        let id: String
        let title: String
        let summary: String
        let day: String
    }

    private var path: String { "v1/weekly-reviews/\(week)/overview" }
    private var context: String { [model.archiveIdentity, model.archiveRevision, week].joined(separator: "|") }
    private var diary: DiaryCalendar {
        DiaryCalendar(timeZone: TimeZone(identifier: overview["timezone"].text)
            ?? TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current)
    }

    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            if !overview["summary"].text.isEmpty {
                SummaryTranslationView(output: overview, languageLabel: L10n.source("Recap language"), renderedContent: { translation, showing in
                    let text = showing ? translation?["text"].text ?? overview["summary"].text : overview["summary"].text
                    RecapOverviewDisclosure(text: text, expanded: $expanded, identifier: "weekly.overview") {
                        overviewText(text)
                    }
                })
                if let date = AppModel.date(overview["generated_at"].text) {
                    Text(L10n.text("Saved recap") + " · " + diary.label(date, template: "d MMM HHmm"))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text(L10n.text("\(overview["reading_minutes"].integer) min read"))
                    .font(.caption).foregroundStyle(.secondary)
                if overview["can_generate"] == .bool(true) {
                    Text(L10n.text("New recordings are available. Generate again to include them."))
                        .font(.caption).foregroundStyle(Theme.accent)
                }
            } else if overview["can_generate"] == .bool(true) {
                Text(L10n.text("Get a recap of this week in up to 5 minutes of reading. It is saved for later."))
                    .font(.subheadline).foregroundStyle(.secondary)
            } else if overview != .null {
                Text(L10n.text("Your weekly recap will be available when there is something to recap."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if overview["partial"] == .bool(true), let cutoff = AppModel.date(overview["cutoff"].text) {
                Text(L10n.text("Week in progress · evidence through \(diary.label(cutoff, template: "d MMM yyyy HHmm"))"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            ErrorNote(message: error)
            if error != nil && overview == .null {
                Button(L10n.text("Retry recap")) { Task { await load() } }
            }
            if generating {
                ProgressView(L10n.text("Generating weekly recap…"))
                    .accessibilityIdentifier("weekly.overview.generating")
            } else if overview["can_generate"] == .bool(true) {
                Button(overview["summary"].text.isEmpty ? L10n.text("Recap") : L10n.text("Generate again")) {
                    operation = Task { await generate() }
                }.accessibilityIdentifier("weekly.overview.generate")
            }
        }
        .task(id: context + String(describing: phase)) {
            guard phase == .active else { return }
            while !Task.isCancelled {
                if !generating { await load() }
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
            }
        }
        .onChange(of: overview["output_id"]) { _, _ in expanded = false; selectedTopic = nil }
        .onDisappear { operation?.cancel(); requestID = UUID(); generating = false }
        .sheet(item: $selectedTopic) { topic in
            NavigationStack {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        Text(topic.title).font(.headline)
                        if let date = diary.date(for: topic.day) {
                            Text(diary.label(date, template: "EEEE d MMMM yyyy"))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Text(markdown(topic.summary)).textSelection(.enabled)
                            .environment(\.openURL, OpenURLAction { _ in .discarded })
                        if let date = diary.date(for: topic.day) {
                            NavigationLink(L10n.text("Open day")) { DayView(date: date) }
                        }
                    }.padding()
                }.navigationTitle(L10n.text("Topics")).navigationBarTitleDisplayMode(.inline)
                    .toolbar { ToolbarItem(placement: .confirmationAction) {
                        Button(L10n.text("Done")) { selectedTopic = nil }
                    } }
            }
        }
    }

    private func markdown(_ value: String) -> AttributedString {
        (try? AttributedString(markdown: value, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(value)
    }

    private func overviewText(_ text: String) -> some View {
        Text(markdown(text)).textSelection(.enabled)
            .environment(\.openURL, OpenURLAction { url in
                guard url.scheme == "topic" else { return .discarded }
                let encoded = String(url.absoluteString.dropFirst("topic:".count))
                guard let id = encoded.removingPercentEncoding,
                      let topic = overview["topics"].array.first(where: { $0["id"].text == id }) else { return .discarded }
                selectedTopic = .init(id: id, title: topic["title"].text,
                    summary: topic["summary"].text, day: topic["day"].text)
                return .handled
            })
            .accessibilityIdentifier("weekly.overview.summary")
    }

    private func load() async {
        let token = UUID(), scope = context
        requestID = token
        do {
            let result = try await model.client().request(path)
            guard !Task.isCancelled, requestID == token, scope == context else { return }
            guard result["week_start"].text == week else {
                throw BackendFailure(message: L10n.text("Home-core returned a review for a different week."))
            }
            overview = result; error = nil
        } catch { if !Task.isCancelled, requestID == token, scope == context { self.error = L10n.error(error) } }
    }

    private func generate() async {
        guard !generating, overview["can_generate"] == .bool(true) else { return }
        let token = UUID(), scope = context
        requestID = token; generating = true; error = nil
        defer { if requestID == token { generating = false } }
        do {
            let result = try await model.client().request(path, method: "POST")
            guard !Task.isCancelled, requestID == token, scope == context else { return }
            guard result["week_start"].text == week else {
                throw BackendFailure(message: L10n.text("Home-core returned a review for a different week."))
            }
            overview = result
        } catch { if !Task.isCancelled, requestID == token, scope == context { self.error = L10n.error(error) } }
    }
}

struct WeeklyReviewContent: View {
    let version: JSONValue
    let sources: [JSONValue]
    let selectSource: (JSONValue) -> Void
    private let categories = [
        (key: "decision", title: L10n.text("Supported decisions"), icon: "arrow.triangle.branch"),
        (key: "commitment", title: L10n.text("Commitments needing attention"), icon: "checkmark.circle"),
        (key: "discussion", title: L10n.text("Recurring discussions"), icon: "bubble.left.and.bubble.right"),
        (key: "change", title: L10n.text("Changes since last week"), icon: "arrow.up.right")
    ]
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        ForEach(categories, id: \.key) { category in
            Section {
                let items = version["items"].array.filter { $0["category"].text == category.key }
                if items.isEmpty {
                    Text(L10n.text("No supported items found in the recorded evidence.")).font(.subheadline).foregroundStyle(.secondary)
                }
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    WeeklyReviewItem(item: item, sources: sources, selectSource: selectSource)
                }
            } header: { Label(category.title, systemImage: category.icon) }
        }
    }
}

private struct WeeklyReviewItem: View {
    let item: JSONValue
    let sources: [JSONValue]
    let selectSource: (JSONValue) -> Void
    @State private var showEvidence = false
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            Text(item["claim"].text).font(.headline).textSelection(.enabled)
            if item["category"].text == "commitment" {
                Label(item["status"].text == "explicitly_open" ? L10n.text("Explicitly still open") : L10n.text("Follow-through unconfirmed"), systemImage: "clock")
                    .font(.caption.weight(.medium)).foregroundStyle(Theme.accent)
            }
            Text(item["qualification"].text).font(.subheadline).foregroundStyle(.secondary).textSelection(.enabled)
            DisclosureGroup(L10n.text("Recorded evidence"), isExpanded: $showEvidence) {
                if showEvidence {
                    ForEach(Array(item["evidence"].array.enumerated()), id: \.offset) { _, ref in
                        VStack(alignment: .leading, spacing: 10) {
                            Text(ref["quote"].text).font(.subheadline).textSelection(.enabled)
                                .padding(.leading, 12)
                                .overlay(alignment: .leading) { Rectangle().fill(Theme.accent.opacity(0.3)).frame(width: 3) }
                            if let source = sources.first(where: { $0["id"].text == ref["source_id"].text }) {
                                Button { selectSource(source) } label: { Label(L10n.text("Inspect original recording"), systemImage: "waveform") }
                                    .buttonStyle(.borderless)
                            } else {
                                Text(L10n.text("Source is no longer available in the current archive.")).font(.caption).foregroundStyle(.secondary)
                            }
                        }.padding(.vertical, 8)
                    }
                }
            }.font(.subheadline)
        }.padding(.vertical, 8)
    }
}

struct WeeklyCoverageRow: View {
    let title: String
    let coverage: JSONValue
    private func rounded(_ key: String, divisor: Double = 1) -> Int {
        guard case .number(let value) = coverage[key] else { return 0 }
        return Int((value / divisor).rounded())
    }
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline.weight(.medium))
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 18) { metrics }
                VStack(alignment: .leading, spacing: 8) { metrics }
            }
            DisclosureGroup(L10n.text("Coverage details")) {
                Text(L10n.text("\(coverage["pending_chunks"].integer) pending · \(coverage["empty_chunks"].integer) empty or silent · \(coverage["expired_chunks"].integer) expired transcripts"))
                Text(L10n.text("\(coverage["capture_gaps"].integer) known capture gaps (\(rounded("known_gap_seconds")) seconds) · \(coverage["audio_unavailable"].integer) sources without audio"))
            }.font(.caption).foregroundStyle(.secondary)
        }.padding(.vertical, 5)
    }
    private var metrics: some View {
        Group {
            Label(L10n.text("\(coverage["speech_days"].integer) \(coverage["speech_days"].integer == 1 ? L10n.text("day") : L10n.text("days")) with speech"), systemImage: "calendar")
            Label(coverage["speech_sources"].integer == 1 ? L10n.text("1 source") : L10n.text("\(coverage["speech_sources"].integer) sources"), systemImage: "waveform")
            Label(L10n.text("\(rounded("captured_seconds", divisor: 60)) min"), systemImage: "clock")
        }.font(.caption).foregroundStyle(.secondary)
    }
}

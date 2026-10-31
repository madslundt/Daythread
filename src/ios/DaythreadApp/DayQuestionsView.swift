import SwiftUI
import OmiCore

/// The notification selects a click group; the request still loads the whole day.
struct DayQuestionsView: View {
    let jobID: String?
    let day: String?
    init(jobID: String) { self.jobID = jobID; self.day = nil }
    init(day: String) { self.jobID = nil; self.day = day }
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.locale) private var interfaceLocale
    @State private var page: DayQuestions?
    @State private var loadedScope = ""
    @State private var loading = true
    @State private var error: String?
    @State private var didScroll = false
    @State private var loadID = UUID()

    private var visiblePage: DayQuestions? { loadedScope == model.archiveIdentity ? page : nil }
    private var timeZone: TimeZone {
        TimeZone(identifier: visiblePage?.timezone ?? model.serverTimeZoneIdentifier ?? "") ?? .current
    }

    var body: some View {
        ScrollViewReader { proxy in
            TimelineView(.periodic(from: .now, by: 60)) { context in
                List {
                    Section {
                        if let day = visiblePage?.day, let date = DiaryCalendar(timeZone: timeZone).date(for: day) {
                            Text(DiaryCalendar(timeZone: timeZone).label(date, template: "EEEEdMMMM"))
                                .font(.subheadline).foregroundStyle(.secondary)
                        }
                        if loading { ProgressView(L10n.text("Loading…")) }
                        ErrorNote(message: error)
                        if error != nil { Button(L10n.text("Try again")) { Task { await load(retry: true) } } }
                    }
                    if let page = visiblePage {
                        if page.clickGroups.isEmpty && !loading && error == nil {
                            ContentUnavailableView(L10n.text("No questions yet"), systemImage: "questionmark.bubble")
                        }
                        ForEach(page.clickGroups) { group in
                            Section {
                                ForEach(group.rows) { row in
                                    questionRow(row, now: context.date)
                                        .id(row.id)
                                        .listRowBackground(page.isSelected(group)
                                            ? Color.accentColor.opacity(0.13) : Color(uiColor: .secondarySystemGroupedBackground))
                                }
                            } header: {
                                groupHeader(group, selected: page.isSelected(group), now: context.date)
                                    .id(group.headerID).accessibilityIdentifier(group.headerID)
                            }
                        }
                    }
                }
                .listStyle(.insetGrouped).omiScreen()
                .task(id: visiblePage?.firstHighlightedGroupID) {
                    guard !didScroll, let id = visiblePage?.firstHighlightedGroupID else { return }
                    await Task.yield()
                    guard !Task.isCancelled else { return }
                    proxy.scrollTo(id, anchor: .top)
                    didScroll = true
                }
            }
        }
        .navigationTitle(L10n.text("Questions"))
        .navigationBarTitleDisplayMode(.inline)
        .task(id: model.archiveIdentity + (jobID ?? day ?? "") + String(describing: scenePhase)) {
            if scenePhase == .active { await load() }
        }
        .refreshable { await load(retry: true) }
    }

    private func groupHeader(_ group: DayQuestionGroup, selected: Bool, now: Date) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(group.questionCount == 1 ? L10n.text("\(group.questionCount) question") : L10n.text("\(group.questionCount) questions"))
                Spacer()
                if let date = group.capturedDate {
                    Text(QuestionTimestamp.label(for: date, now: now, locale: interfaceLocale, timeZone: timeZone))
                }
            }
            if selected {
                Label(L10n.text("From this click"), systemImage: "circle.fill")
                    .font(.caption.weight(.semibold)).foregroundStyle(Color.accentColor)
            }
        }.textCase(nil)
    }

    private func questionRow(_ row: DayQuestionGroup.Row, now: Date) -> some View {
        let question = row.question
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Spacer()
                if let date = question.askedDate {
                    Text(QuestionTimestamp.label(for: date, now: now, locale: interfaceLocale, timeZone: timeZone))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Text(question.question).font(.headline).textSelection(.enabled)
            if let answer = question.answer, !answer.isEmpty {
                QuestionAnswerPreview(answer: answer)
            }
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("day-question-" + row.id)
    }

    private func load(retry: Bool = false) async {
        let scope = model.archiveIdentity
        let currentLoad = UUID()
        loadID = currentLoad
        if loadedScope != scope { page = nil; didScroll = false }
        loading = true; error = nil
        defer { if scope == model.archiveIdentity, loadID == currentLoad { loading = false } }
        do {
            let client = try model.client().snapshot()
            let path = jobID.map { "v1/jobs/" + $0 + "/questions" } ?? "v1/questions"
            let scopeQuery = day.map { ["day": $0] } ?? [:]
            var query = scopeQuery
            if retry { query["retry"] = "true" }
            repeat {
                let value = try await client.decoded(path, query: query, as: DayQuestions.self)
                query = scopeQuery
                guard scope == model.archiveIdentity, loadID == currentLoad, !Task.isCancelled else { return }
                page = value; loadedScope = scope
                error = value.error.map(L10n.key)
                loading = value.processing
                if !value.processing { break }
                try await Task.sleep(for: .seconds(2))
            } while !Task.isCancelled
        } catch {
            if scope == model.archiveIdentity, loadID == currentLoad, !Task.isCancelled {
                if let status = (error as? BackendFailure)?.status, [404, 409].contains(status) {
                    page = nil; loadedScope = ""
                }
                self.error = L10n.error(error)
            }
        }
    }
}

private struct QuestionAnswerPreview: View {
    let answer: String
    @State private var expanded = false

    var body: some View {
        Button {
            withAnimation { expanded.toggle() }
        } label: {
            Text(answer)
                .lineLimit(expanded ? nil : 3)
                .truncationMode(.tail)
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityValue(expanded ? L10n.text("Expanded") : L10n.text("Collapsed"))
        .accessibilityHint(expanded ? L10n.text("Show less") : L10n.text("Read more"))
        .contextMenu {
            Button(L10n.text("Copy"), systemImage: "doc.on.doc") {
                UIPasteboard.general.string = answer
            }
        }
    }
}

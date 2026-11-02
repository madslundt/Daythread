import SwiftUI
import OmiCore

enum ChatScope: Equatable {
    case all, day(String), range(String, String), meeting(String)

    init(_ value: JSONValue) {
        if !value["meeting_id"].text.isEmpty { self = .meeting(value["meeting_id"].text) }
        else if !value["day"].text.isEmpty { self = .day(value["day"].text) }
        else if !value["start_day"].text.isEmpty && !value["end_day"].text.isEmpty {
            self = .range(value["start_day"].text, value["end_day"].text)
        }
        else { self = .all }
    }

    var kind: String {
        switch self { case .all: "all"; case .day: "day"; case .range: "range"; case .meeting: "meeting" }
    }
    var fields: [String: String] {
        switch self {
        case .all: [:]
        case .day(let day): ["day": day]
        case .range(let start, let end): ["start_day": start, "end_day": end]
        case .meeting(let id): ["meeting_id": id]
        }
    }
    var isReady: Bool {
        switch self {
        case .meeting(let id): !id.isEmpty
        case .range(let start, let end): !start.isEmpty && !end.isEmpty && start <= end
        default: true
        }
    }
    var cacheSuffix: String {
        switch self {
        case .all: "all"
        case .day(let day): "day." + day
        case .range(let start, let end): "range." + start + "." + end
        case .meeting(let id): "meeting." + id
        }
    }
}

struct ChatView: View {
    @Environment(AppModel.self) private var model
    let initialScope: ChatScope?
    let initialQuestion: String
    let initialSessionID: String?
    @State private var sheet: ChatSheet?
    @State private var followsLatest = true
    @State private var openedInitialSession = false
    @State private var preparedQuestion = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    @FocusState private var focused: Bool
    private enum ChatSheet: String, Identifiable { case history, scope; var id: String { rawValue } }

    init(initialScope: ChatScope? = nil, initialQuestion: String = "", initialSessionID: String? = nil) {
        self.initialScope = initialScope
        self.initialQuestion = String(initialQuestion.prefix(2000))
        self.initialSessionID = initialSessionID
    }

    private var cacheKey: String { model.archiveIdentity + ".chat" + (initialScope.map { "." + $0.cacheSuffix } ?? "") }
    private var chat: ChatConversationModel {
        model.chatConversations.workspace(key: cacheKey, scope: initialScope ?? .all, question: initialQuestion)
    }
    private var scope: ChatScope { chat.scope }
    private var question: String { chat.question }
    private var sessions: [JSONValue] { chat.sessions }
    private var sessionID: String? { chat.sessionID }
    private var messages: [JSONValue] { chat.messages }
    private var loading: Bool { chat.loading }
    private var error: String? { chat.displayedError }
    private var cached: Bool { chat.cached }
    private var pendingQuestion: String { chat.pendingQuestion }
    private var streamingText: String { chat.streamingText }
    private var streamStage: String { chat.streamStage }
    private var trimmedQuestion: String { question.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var canSend: Bool { !loading && !chat.generating && scope.isReady && !trimmedQuestion.isEmpty && trimmedQuestion.unicodeScalars.count <= 2000 }
    private var replyProgress: String {
        switch streamStage {
        case "compacting": L10n.text("Compacting conversation…")
        case "expanding": L10n.text("Trying alternate search wording…")
        case "generating": L10n.text("Writing your answer…")
        default: L10n.text("Reading recorded evidence…")
        }
    }
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var meetings: [JSONValue] {
        model.serverTimeline["meetings"].array.filter { !$0["id"].text.isEmpty }
            .sorted { $0["started_at"].text > $1["started_at"].text }
    }
    private var scopeTitle: String {
        switch scope {
        case .all: L10n.text("All diaries · all days")
        case .day(let day): L10n.text("Diary · ") + day
        case .range(let start, let end): L10n.text("Diaries · ") + start + " – " + end
        case .meeting(let id): meetings.first(where: { $0["id"].text == id }).map(meetingTitle) ?? (id.isEmpty ? L10n.text("Choose a meeting") : L10n.text("Selected meeting"))
        }
    }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        ScrollViewReader { proxy in
            GeometryReader { viewport in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 24) {
                        if cached {
                            Text(L10n.text("Saved conversation on this iPhone; source availability and corrections may have changed"))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if chat.context["compaction_count"].integer > 0 {
                            Label(L10n.text("Earlier messages summarized. Your full chat is kept."), systemImage: "text.alignleft")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if messages.isEmpty && pendingQuestion.isEmpty && !loading && !chat.generating {
                            ChatWelcomeView { chat.question = $0; focused = true }
                        }
                        ForEach(Array(messages.enumerated()), id: \.element["id"].text) { _, message in
                            ChatMessageView(message: message)
                        }
                        if !pendingQuestion.isEmpty {
                            ChatQuestionBubble(text: pendingQuestion)
                        }
                        if chat.generating {
                            VStack(alignment: .leading, spacing: 12) {
                                HStack {
                                    Text("Daythread").font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
                                    Spacer()
                                    if !streamingText.isEmpty { ChatMessageTextActions(content: streamingText) }
                                }
                                if !streamingText.isEmpty {
                                    ChatMarkdownText(content: streamingText)
                                }
                                ProgressView(replyProgress)
                                    .font(.callout).tint(Theme.accent)
                                Text(L10n.text("You can leave this chat. Your reply will be saved."))
                                    .font(.caption).foregroundStyle(.secondary)
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        } else if loading {
                            ProgressView(L10n.text("Loading conversation…")).frame(maxWidth: .infinity)
                        }
                        Color.clear.frame(height: 1).id("chat.bottom")
                            .background(GeometryReader { geometry in
                                Color.clear.preference(key: ChatBottomPreference.self, value: geometry.frame(in: .named("chat.scroll")).maxY)
                            })
                    }.padding(20).frame(maxWidth: 760).frame(maxWidth: .infinity)
                }
                .coordinateSpace(name: "chat.scroll")
                .onPreferenceChange(ChatBottomPreference.self) { bottom in
                    if bottom > 0 && bottom <= viewport.size.height + 40 { followsLatest = true }
                }
                .simultaneousGesture(DragGesture().onChanged { _ in followsLatest = false })
                .scrollDismissesKeyboard(.interactively)
                .overlay(alignment: .bottomTrailing) {
                    if !followsLatest && (!messages.isEmpty || !pendingQuestion.isEmpty) {
                        Button { followsLatest = true; scrollToLatest(proxy) } label: {
                            Image(systemName: "arrow.down").font(.body.weight(.semibold)).frame(width: 44, height: 44)
                                .background(.regularMaterial, in: Circle()).shadow(color: .black.opacity(0.1), radius: 5, y: 2)
                        }.padding(16).accessibilityLabel(L10n.text("Latest message")).accessibilityIdentifier("chat.latest")
                    }
                }
                .onChange(of: messages) { _, _ in if followsLatest { scrollToLatest(proxy) } }
                .onChange(of: streamingText) { _, _ in if followsLatest { proxy.scrollTo("chat.bottom", anchor: .bottom) } }
                .onChange(of: pendingQuestion) { _, value in if !value.isEmpty { followsLatest = true; scrollToLatest(proxy) } }
                .onChange(of: focused) { _, value in if value { followsLatest = true; scrollToLatest(proxy) } }
                .onChange(of: viewport.size.height) { _, _ in if followsLatest { proxy.scrollTo("chat.bottom", anchor: .bottom) } }
            }
        }
        .background(Theme.canvas).navigationTitle(chat.title).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .top, spacing: 0) {
            Button { focused = false; sheet = .scope } label: {
                HStack(spacing: 10) {
                    Image(systemName: "line.3.horizontal.decrease")
                    Text(scopeTitle).font(.subheadline).foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.down").font(.caption.weight(.semibold))
                }.padding(.horizontal, 20).padding(.vertical, 10).frame(maxWidth: 760).frame(maxWidth: .infinity)
                    .background(Theme.canvas).contentShape(Rectangle())
            }.buttonStyle(.plain).disabled(loading).accessibilityLabel(L10n.text("Search within") + ": " + scopeTitle).accessibilityIdentifier("chat.scopeOptions")
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { composer }
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button { focused = false; sheet = .history } label: { Image(systemName: "clock.arrow.circlepath") }
                    .accessibilityLabel(L10n.text("Conversation history")).accessibilityIdentifier("chat.history")
                Button { newConversation(scope, clearDraft: true); focused = true } label: { Image(systemName: "square.and.pencil") }
                    .accessibilityLabel(L10n.text("New conversation")).accessibilityIdentifier("chat.new")
            }
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button(L10n.text("Done")) { focused = false }
            }
        }
        .sheet(item: $sheet) { destination in
            switch destination {
            case .history:
                ChatHistoryView(sessions: sessions, selectedID: sessionID, loading: loading, error: error,
                    refresh: { await chat.refresh() }, select: { id in
                        sheet = nil; followsLatest = true
                        Task { await chat.select(id) }
                    }, remove: { id in await chat.remove(id) })
            case .scope:
                NavigationStack {
                    List { scopeControls }.omiScreen().navigationTitle(L10n.text("Search within"))
                        .navigationBarTitleDisplayMode(.inline)
                        .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L10n.text("Done")) { sheet = nil } } }
                }.presentationDragIndicator(.visible)
            }
        }
        .task(id: cacheKey + model.archiveRevision + String(scenePhase == .active)) {
            guard scenePhase == .active else { return }
            do {
                try chat.connect(client: model.client())
                if !initialQuestion.isEmpty, !preparedQuestion {
                    preparedQuestion = true
                    chat.newConversation(initialScope ?? .all, clearDraft: true)
                    chat.question = initialQuestion
                }
                if let initialSessionID, !openedInitialSession {
                    openedInitialSession = true
                    await chat.select(initialSessionID)
                } else { await chat.refresh() }
            } catch { chat.connectionFailed(error) }
            if !initialQuestion.isEmpty { focused = true }
        }
        .onDisappear { chat.save() }
    }

    private func scrollToLatest(_ proxy: ScrollViewProxy) {
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) { proxy.scrollTo("chat.bottom", anchor: .bottom) }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let error {
                HStack(alignment: .top) {
                    ErrorNote(message: error)
                    Spacer(minLength: 8)
                    Button(L10n.text("Retry")) { Task { await chat.retry() } }.disabled(loading)
                }.font(.callout).accessibilityIdentifier("chat.error")
            }
            if trimmedQuestion.unicodeScalars.count > 1800 {
                Text(L10n.text("\(trimmedQuestion.unicodeScalars.count) / 2,000 characters"))
                    .font(.caption).foregroundStyle(trimmedQuestion.unicodeScalars.count > 2000 ? Color.red : .secondary)
            }
            HStack(alignment: .bottom, spacing: 10) {
                TextField(L10n.text("Ask about your recordings"), text: Binding(get: { question }, set: { chat.question = $0 }), axis: .vertical)
                    .lineLimit(1...5).focused($focused).padding(.horizontal, 14).padding(.vertical, 12)
                    .background(Theme.surface, in: RoundedRectangle(cornerRadius: 22))
                    .overlay { RoundedRectangle(cornerRadius: 22).stroke(Theme.accent.opacity(focused ? 0.6 : 0.18), lineWidth: 1) }
                    .accessibilityLabel(L10n.text("Question about your recordings")).accessibilityIdentifier("chat.question")
                    .disabled(chat.generating)
                Button {
                    guard canSend else { return }
                    focused = false; followsLatest = true; chat.send()
                    Task {
                        let notifications = CompletionNotifications.shared
                        await notifications.refreshAuthorization()
                        if notifications.permission == .notDetermined { await notifications.requestPermission() }
                    }
                } label: {
                    Group {
                        if chat.generating { ProgressView().tint(.secondary) }
                        else { Image(systemName: "arrow.up").font(.system(size: 18, weight: .semibold)) }
                    }
                    .frame(width: 48, height: 48)
                    .foregroundStyle(canSend ? Theme.onAccent : Color.secondary)
                    .background(canSend ? Theme.accent : Color.secondary.opacity(0.12), in: Circle())
                }.buttonStyle(.plain).disabled(!canSend)
                    .accessibilityLabel(L10n.text("Send question"))
                    .accessibilityIdentifier("chat.send")
            }
        }.padding(.horizontal, 16).padding(.vertical, 10).frame(maxWidth: 760).frame(maxWidth: .infinity).background(.bar)
    }

    private var scopeControls: some View {
        Section {
            Picker(L10n.text("Chat with"), selection: Binding(get: { scope.kind }, set: changeKind)) {
                Text(L10n.text("All diaries")).tag("all")
                Text(L10n.text("Date")).tag("day")
                Text(L10n.text("Date range")).tag("range")
                Text(L10n.text("Meeting")).tag("meeting")
            }.accessibilityIdentifier("chat.scope").disabled(loading)
            Menu {
                ForEach([false, true], id: \.self) { previous in
                    Section(previous ? L10n.text("Previous period") : L10n.text("Current period")) {
                        ForEach(DiaryPeriod.allCases, id: \.self) { period in
                            Button("\(previous ? L10n.text("Last") : L10n.text("This")) \(period.rawValue)") { selectPeriod(period, previous: previous) }
                        }
                    }
                }
            } label: { Label(L10n.text("Date shortcuts"), systemImage: "calendar.badge.clock") }
                .accessibilityIdentifier("chat.dateShortcuts").disabled(loading)
            switch scope {
            case .all:
                Text(L10n.text("All days are available to this conversation.")).font(.caption).foregroundStyle(.secondary)
            case .day(let key):
                DatePicker(L10n.text("Diary date"), selection: Binding(get: { diary.date(for: key) ?? model.now }, set: { newConversation(.day(diary.dayKey($0))) }), displayedComponents: .date)
                    .environment(\.calendar, diary.calendar).environment(\.timeZone, diary.calendar.timeZone)
                    .accessibilityIdentifier("chat.day").disabled(loading)
                Text(L10n.text("Dates in \(diary.calendar.timeZone.identifier)")).font(.caption).foregroundStyle(.secondary)
            case .range(let start, let end):
                rangeControls(start: start, end: end)
            case .meeting:
                Menu {
                    ForEach(Array(meetings.enumerated()), id: \.offset) { _, meeting in
                        Button(meetingTitle(meeting)) { newConversation(.meeting(meeting["id"].text)) }
                    }
                } label: { Label(scopeTitle, systemImage: "person.2") }
                    .accessibilityLabel(L10n.text("Choose meeting")).accessibilityValue(scopeTitle)
                    .accessibilityIdentifier("chat.meeting").disabled(loading || meetings.isEmpty)
                if meetings.isEmpty { Text(L10n.text("No recorded meetings are available yet.")).font(.caption).foregroundStyle(.secondary) }
            }
        }
        footer: { Text(L10n.text("Changing scope starts a new conversation. Saved conversations keep their original scope.")) }
    }

    private func rangeControls(start: String, end: String) -> some View {
        let startDate = diary.date(for: start) ?? model.now
        let endDate = diary.date(for: end) ?? startDate
        return Group {
            DatePicker(L10n.text("Start date"), selection: Binding(get: { startDate }, set: { newConversation(.range(diary.dayKey($0), end)) }), in: ...endDate, displayedComponents: .date)
                .accessibilityIdentifier("chat.startDay")
            DatePicker(L10n.text("End date"), selection: Binding(get: { endDate }, set: { newConversation(.range(start, diary.dayKey($0))) }), in: startDate..., displayedComponents: .date)
                .accessibilityIdentifier("chat.endDay")
            Text(L10n.text("\(start) – \(end) · both dates included")).font(.caption).foregroundStyle(.secondary)
            Text(L10n.text("Dates in \(diary.calendar.timeZone.identifier)")).font(.caption).foregroundStyle(.secondary)
        }.environment(\.calendar, diary.calendar).environment(\.timeZone, diary.calendar.timeZone).disabled(loading)
    }

    private func selectPeriod(_ period: DiaryPeriod, previous: Bool) {
        let dates = diary.dateRange(for: period, containing: model.now, previous: previous)
        newConversation(.range(diary.dayKey(dates.lowerBound), diary.dayKey(dates.upperBound)))
    }

    private func meetingTitle(_ meeting: JSONValue) -> String {
        guard let date = AppModel.date(meeting["started_at"].text) else { return L10n.text("Meeting · ") + meeting["id"].text }
        return L10n.text("Meeting · ") + diary.label(date, template: "d MMM yyyy HHmm")
    }

    private func changeKind(_ kind: String) {
        guard kind != scope.kind else { return }
        switch kind {
        case "day": newConversation(.day(diary.dayKey(model.now)))
        case "range":
            let today = diary.dayKey(model.now)
            newConversation(.range(today, today))
        case "meeting": newConversation(.meeting(meetings.first?["id"].text ?? ""))
        default: newConversation(.all)
        }
    }

    private func newConversation(_ selection: ChatScope, clearDraft: Bool = false) {
        followsLatest = true
        chat.newConversation(selection, clearDraft: clearDraft)
    }
}

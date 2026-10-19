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
    @State private var scope: ChatScope
    @State private var question = ""
    @State private var sessions: [JSONValue] = []
    @State private var sessionID: String?
    @State private var messages: [JSONValue] = []
    @State private var loading = false
    @State private var error: String?
    @State private var cached = false
    @State private var sheet: ChatSheet?
    @State private var loadedCacheKey: String?
    @State private var followsLatest = true
    @State private var retryAction: ChatRetry = .load
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private enum ChatSheet: String, Identifiable { case history, scope; var id: String { rawValue } }
    private enum ChatRetry { case load, select(String), send }
    @State private var requestID = UUID()
    @State private var sendTask: Task<Void, Never>?
    @State private var streamingText = ""
    @State private var streamStage = "searching"
    @State private var pendingQuestion = ""
    @FocusState private var focused: Bool

    init(initialScope: ChatScope? = nil, initialQuestion: String = "") {
        self.initialScope = initialScope
        self.initialQuestion = String(initialQuestion.prefix(2000))
        _scope = State(initialValue: initialScope ?? .all)
        _question = State(initialValue: String(initialQuestion.prefix(2000)))
    }

    private var cacheKey: String { model.archiveIdentity + ".chat" + (initialScope.map { "." + $0.cacheSuffix } ?? "") }
    private var trimmedQuestion: String { question.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var canSend: Bool { !loading && sendTask == nil && scope.isReady && !trimmedQuestion.isEmpty && trimmedQuestion.unicodeScalars.count <= 2000 }
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
                        if messages.isEmpty && pendingQuestion.isEmpty && !loading {
                            ChatWelcomeView { question = $0; focused = true }
                        }
                        ForEach(Array(messages.enumerated()), id: \.element["id"].text) { _, message in
                            ChatMessageView(message: message)
                        }
                        if !pendingQuestion.isEmpty {
                            ChatQuestionBubble(text: pendingQuestion)
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Daythread").font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
                                if !streamingText.isEmpty {
                                    Text(streamingText).textSelection(.enabled)
                                    Text(L10n.text("Draft answer · sources are checked before saving.")).font(.caption).foregroundStyle(.secondary)
                                }
                                ProgressView(streamStage == "expanding" ? L10n.text("Trying alternate search wording…") : streamStage == "generating" ? L10n.text("Writing your answer…") : L10n.text("Reading recorded evidence…"))
                                    .font(.callout).tint(Theme.accent)
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
        .background(Theme.canvas).navigationTitle(L10n.text("Chat")).navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.canvas, for: .navigationBar)
        .safeAreaInset(edge: .top, spacing: 0) {
            Button { focused = false; sheet = .scope } label: {
                HStack(spacing: 10) {
                    Image(systemName: "line.3.horizontal.decrease")
                    VStack(alignment: .leading, spacing: 2) {
                        Text(L10n.text("Search within")).font(.caption).foregroundStyle(.secondary)
                        Text(scopeTitle).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.down").font(.caption.weight(.semibold))
                }.padding(.horizontal, 20).padding(.vertical, 10).frame(maxWidth: 760).frame(maxWidth: .infinity)
                    .background(Theme.canvas).contentShape(Rectangle())
            }.buttonStyle(.plain).disabled(loading).accessibilityIdentifier("chat.scopeOptions")
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { composer }
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button { focused = false; sheet = .history } label: { Image(systemName: "clock.arrow.circlepath") }
                    .accessibilityLabel(L10n.text("Conversation history")).accessibilityIdentifier("chat.history").disabled(loading)
                Button { newConversation(scope, clearDraft: true); focused = true } label: { Image(systemName: "square.and.pencil") }
                    .accessibilityLabel(L10n.text("New conversation")).accessibilityIdentifier("chat.new").disabled(loading)
            }
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button(L10n.text("Done")) { focused = false }
            }
        }
        .sheet(item: $sheet) { destination in
            switch destination {
            case .history:
                ChatHistoryView(sessions: sessions, selectedID: sessionID, loading: loading, error: error, refresh: { await load(restore: false) }) { id in
                    sheet = nil
                    Task { await select(id) }
                }
            case .scope:
                NavigationStack {
                    List { scopeControls }.omiScreen().navigationTitle(L10n.text("Search within"))
                        .navigationBarTitleDisplayMode(.inline)
                        .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L10n.text("Done")) { sheet = nil } } }
                }.presentationDragIndicator(.visible)
            }
        }
        .task(id: cacheKey + model.archiveRevision) {
            cancelRequest()
            await load(restore: loadedCacheKey != cacheKey)
        }
        .task(id: question) {
            do { try await Task.sleep(for: .milliseconds(350)); save() } catch { }
        }
        .onDisappear { save(); cancelRequest() }
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
                    Button(L10n.text("Retry")) { retry() }.disabled(loading || sendTask != nil)
                }.font(.callout).accessibilityIdentifier("chat.error")
            }
            if trimmedQuestion.unicodeScalars.count > 1800 {
                Text(L10n.text("\(trimmedQuestion.unicodeScalars.count) / 2,000 characters"))
                    .font(.caption).foregroundStyle(trimmedQuestion.unicodeScalars.count > 2000 ? Color.red : .secondary)
            }
            HStack(alignment: .bottom, spacing: 10) {
                TextField(L10n.text("Ask about your recordings"), text: $question, axis: .vertical)
                    .lineLimit(1...5).focused($focused).padding(.horizontal, 14).padding(.vertical, 12)
                    .background(Theme.surface, in: RoundedRectangle(cornerRadius: 22))
                    .overlay { RoundedRectangle(cornerRadius: 22).stroke(Theme.accent.opacity(focused ? 0.6 : 0.18), lineWidth: 1) }
                    .accessibilityLabel(L10n.text("Question about your recordings")).accessibilityIdentifier("chat.question")
                    .disabled(sendTask != nil)
                Button {
                    if sendTask != nil { cancelRequest(); focused = true }
                    else { startSend() }
                } label: {
                    Image(systemName: sendTask != nil ? "stop.fill" : "arrow.up").font(.system(size: 18, weight: .semibold))
                        .frame(width: 48, height: 48)
                        .foregroundStyle(canSend || sendTask != nil ? Theme.onAccent : Color.secondary)
                        .background(canSend || sendTask != nil ? Theme.accent : Color.secondary.opacity(0.12), in: Circle())
                }.buttonStyle(.plain).disabled(sendTask == nil && !canSend)
                    .accessibilityLabel(sendTask != nil ? L10n.text("Stop") : L10n.text("Send question"))
                    .accessibilityIdentifier(sendTask != nil ? "chat.stop" : "chat.send")
            }
        }.padding(.horizontal, 16).padding(.vertical, 10).frame(maxWidth: 760).frame(maxWidth: .infinity).background(.bar)
    }

    private func startSend() {
        guard canSend else { return }
        sendTask = Task { await send() }
    }

    private func cancelRequest() {
        requestID = UUID()
        sendTask?.cancel(); sendTask = nil; loading = false; pendingQuestion = ""; streamingText = ""
    }

    private func retry() {
        switch retryAction {
        case .send: startSend()
        case .load: Task { await load(restore: false) }
        case .select(let id): Task { await select(id) }
        }
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
        guard !loading else { return }
        scope = selection; sessionID = nil; messages = []; error = nil; cached = false; followsLatest = true
        if clearDraft { question = "" }
        save()
    }

    private func load(restore: Bool) async {
        guard restore || !loading else { return }
        let key = cacheKey, request = UUID()
        requestID = request; loading = true; error = nil; retryAction = .load
        if restore {
            loadedCacheKey = key
            scope = initialScope ?? .all; sessionID = nil; messages = []; sessions = []; question = initialQuestion; cached = false
            if initialQuestion.isEmpty, let value = LocalCache.load(key) {
                sessions = value["sessions"].array
                let savedScope = ChatScope(value)
                if initialScope == nil || initialScope == savedScope {
                    messages = value["messages"].array; scope = savedScope; question = value["draft"].text
                    sessionID = value["session_id"].text.isEmpty ? nil : value["session_id"].text; cached = true
                }
            }
            if !initialQuestion.isEmpty { focused = true }
        }
        defer { if requestID == request { loading = false } }
        do {
            let result = try await model.client().request("v1/chat/sessions")
            guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
            sessions = result["items"].array
            if let savedID = sessionID {
                do {
                    let restored = try await model.client().request("v1/chat/sessions/\(savedID)")
                    guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
                    messages = restored["messages"].array; cached = false
                } catch let failure as BackendFailure where failure.status == 404 {
                    guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
                    messages = []; sessionID = nil; cached = false
                }
            }
            save()
        } catch {
            guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
            self.error = L10n.error(error)
        }
    }

    private func select(_ id: String) async {
        guard !loading else { return }
        let key = cacheKey, request = UUID()
        requestID = request; loading = true; error = nil; retryAction = .select(id)
        defer { if requestID == request { loading = false } }
        do {
            let result = try await model.client().request("v1/chat/sessions/\(id)")
            guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
            followsLatest = true
            scope = ChatScope(result); sessionID = id; messages = result["messages"].array; question = ""; cached = false; save()
        } catch {
            guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
            self.error = L10n.error(error)
        }
    }

    private func send() async {
        guard !Task.isCancelled else { return }
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !loading, scope.isReady else { sendTask = nil; return }
        guard !text.isEmpty, text.unicodeScalars.count <= 2000 else { sendTask = nil; error = L10n.text("Keep questions under 2,000 characters."); return }
        let key = cacheKey, request = UUID(), client: BackendClient
        do { client = try model.client() } catch { sendTask = nil; retryAction = .send; self.error = L10n.error(error); return }
        requestID = request; focused = false; loading = true; error = nil; retryAction = .send
        pendingQuestion = text; streamingText = ""; streamStage = "searching"
        defer { if requestID == request { sendTask = nil; loading = false; pendingQuestion = ""; streamingText = "" } }
        var body: [String: Any] = ["question": text]
        if let sessionID { body["session_id"] = sessionID }
        else { for (field, value) in scope.fields { body[field] = value } }
        do {
            let result = try await client.chat(body: body) { event in
                guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
                if event["type"].text == "delta" { streamingText += event["text"].text }
                else if event["type"].text == "status" { streamStage = event["stage"].text }
            }
            guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
            messages.append(.object(["id": result["user_message_id"], "role": .string("user"), "content": .string(text)]))
            messages.append(.object(["id": result["message_id"], "role": .string("assistant"), "content": result["answer"], "sources": result["sources"], "evidence": result["evidence"], "retrieval_details": result["retrieval_details"]]))
            if !result["session_id"].text.isEmpty { sessionID = result["session_id"].text }
            let old = sessions.first { $0["id"].text == sessionID }
            let entry: JSONValue = .object([
                "id": result["session_id"],
                "title": old?["title"] ?? .string(String(text.prefix(120))),
                "updated_at": result["evidence"]["generated_at"],
                "day": result["day"], "meeting_id": result["meeting_id"],
                "start_day": result["start_day"], "end_day": result["end_day"]
            ])
            sessions.removeAll { $0["id"].text == sessionID }
            sessions.insert(entry, at: 0)
            question = ""; cached = false; save()
        } catch {
            guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
            self.error = L10n.error(error)
        }
    }

    private func save() {
        guard loadedCacheKey == cacheKey else { return }
        var value: [String: JSONValue] = ["draft": .string(question), "sessions": .array(sessions), "messages": .array(messages), "session_id": sessionID.map(JSONValue.string) ?? .null]
        for (field, text) in scope.fields { value[field] = .string(text) }
        LocalCache.save(.object(value), key: cacheKey)
    }
}

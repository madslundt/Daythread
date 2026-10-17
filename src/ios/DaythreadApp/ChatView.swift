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
    @State private var scopeExpanded = false
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
    private var canSend: Bool { !loading && scope.isReady && !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }
    private var meetings: [JSONValue] {
        model.serverTimeline["meetings"].array.filter { !$0["id"].text.isEmpty }
            .sorted { $0["started_at"].text > $1["started_at"].text }
    }
    private var scopeTitle: String {
        switch scope {
        case .all: "All diaries · all days"
        case .day(let day): "Diary · " + day
        case .range(let start, let end): "Diaries · " + start + " – " + end
        case .meeting(let id): meetings.first(where: { $0["id"].text == id }).map(meetingTitle) ?? (id.isEmpty ? "Choose a meeting" : "Selected meeting")
        }
    }

    var body: some View {
        List {
            Section {
                EditorialTitle(eyebrow: "Personal recall", title: "Ask your days.")
                    .editorialRow()
            }
            Section {
                Menu {
                    Button("New conversation") { newConversation(scope) }
                    ForEach(Array(sessions.enumerated()), id: \.offset) { _, session in
                        Button(session["title"].text.isEmpty ? "Conversation" : session["title"].text) {
                            Task { await select(session["id"].text) }
                        }
                    }
                } label: { Label("Conversation history", systemImage: "clock.arrow.circlepath") }.disabled(loading)
                if cached { Text("Saved conversation on this iPhone; source availability and corrections may have changed").font(.caption).foregroundStyle(.secondary) }
                ErrorNote(message: error)
            }
            scopeControls
            if messages.isEmpty {
                Section {
                    VStack(alignment: .leading, spacing: 12) {
                        Image(systemName: "bubble.left.and.text.bubble.right").font(.title).foregroundStyle(Theme.accent).accessibilityHidden(true)
                        Text("Start with a question.").font(.headline)
                        Text("Explore a conversation, decision, or commitment. Answers link back to your recorded evidence.").font(.subheadline).foregroundStyle(.secondary)
                    }.padding(.vertical, 8)
                    ForEach(["What did we decide?", "What did I say I would follow up on?"], id: \.self) { suggestion in
                        Button { question = suggestion; focused = true } label: {
                            HStack(alignment: .firstTextBaseline, spacing: 12) {
                                Text(suggestion).multilineTextAlignment(.leading)
                                Spacer(minLength: 0)
                                Image(systemName: "arrow.up.left").accessibilityHidden(true)
                            }.frame(minHeight: 36)
                        }.disabled(loading)
                    }
                }
            }
            ForEach(Array(messages.enumerated()), id: \.offset) { _, message in
                Section(message["role"].text == "user" ? "You" : "Daythread") {
                    if message["stale"] == .bool(true) {
                        Text("Historical answer: transcript evidence changed. Ask again for a current answer.").font(.caption).foregroundStyle(.secondary)
                    }
                    Text(.init(message["content"].text)).textSelection(.enabled)
                    if message["role"].text == "assistant" { EvidenceFeedbackView(outputID: message["id"].text, evidence: message["evidence"], outputText: message["content"].text) }
                    if !message["retrieval_details"]["notice"].text.isEmpty {
                        Text(message["retrieval_details"]["notice"].text).font(.caption).foregroundStyle(.secondary)
                    }
                    if !message["sources"].array.isEmpty { SourceList(sources: message["sources"].array) }
                }
            }
            if !pendingQuestion.isEmpty {
                Section("You") { Text(pendingQuestion) }
                Section("Daythread") {
                    if !streamingText.isEmpty {
                        Text(streamingText).textSelection(.enabled)
                        Text("Draft answer · sources are checked before saving.").font(.caption).foregroundStyle(.secondary)
                    }
                    ProgressView(streamStage == "expanding" ? "Trying alternate search wording…" : streamStage == "generating" ? "Writing your answer…" : "Reading recorded evidence…")
                }
            } else if loading { Section { ProgressView("Reading recorded evidence…") } }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle("Chat").navigationBarTitleDisplayMode(.inline)
            .scrollDismissesKeyboard(.interactively)
            .safeAreaInset(edge: .bottom) {
                HStack(alignment: .bottom, spacing: 12) {
                    TextField("Ask about your recordings", text: $question, axis: .vertical)
                        .lineLimit(1...5).focused($focused).padding(.horizontal, 14).padding(.vertical, 12)
                        .background(.background, in: RoundedRectangle(cornerRadius: 18))
                        .overlay { RoundedRectangle(cornerRadius: 18).stroke(Theme.accent.opacity(focused ? 0.6 : 0.18), lineWidth: 1) }
                        .accessibilityLabel("Question about your recordings").accessibilityIdentifier("chat.question")
                        .disabled(sendTask != nil)
                    if sendTask != nil {
                        Button("Stop") { sendTask?.cancel() }
                            .accessibilityIdentifier("chat.stop")
                    } else {
                    Button { sendTask = Task { await send() } } label: {
                        Image(systemName: "arrow.up").font(.system(size: 20, weight: .semibold))
                            .frame(width: 48, height: 48)
                            .foregroundStyle(canSend ? Theme.onAccent : Color.secondary)
                            .background(canSend ? Theme.accent : Color.secondary.opacity(0.12), in: Circle())
                    }.buttonStyle(.plain).disabled(!canSend)
                        .accessibilityLabel("Send question").accessibilityIdentifier("chat.send")
                    }
                }.padding(.horizontal, 20).padding(.vertical, 12).frame(maxWidth: 860)
                    .frame(maxWidth: .infinity).background(.bar)
            }
            .task(id: cacheKey + model.archiveRevision) { sendTask?.cancel(); sendTask = nil; await load(restore: true) }
            .refreshable { sendTask?.cancel(); sendTask = nil; await load(restore: false) }
            .onDisappear { sendTask?.cancel(); sendTask = nil; requestID = UUID(); loading = false; streamingText = ""; pendingQuestion = "" }
    }

    private var scopeControls: some View {
        Section {
            DisclosureGroup(isExpanded: $scopeExpanded) {
            Picker("Chat with", selection: Binding(get: { scope.kind }, set: changeKind)) {
                Text("All diaries").tag("all")
                Text("Date").tag("day")
                Text("Date range").tag("range")
                Text("Meeting").tag("meeting")
            }.accessibilityIdentifier("chat.scope").disabled(loading)
            Menu {
                ForEach([false, true], id: \.self) { previous in
                    Section(previous ? "Previous period" : "Current period") {
                        ForEach(DiaryPeriod.allCases, id: \.self) { period in
                            Button("\(previous ? "Last" : "This") \(period.rawValue)") { selectPeriod(period, previous: previous) }
                        }
                    }
                }
            } label: { Label("Date shortcuts", systemImage: "calendar.badge.clock") }
                .accessibilityIdentifier("chat.dateShortcuts").disabled(loading)
            switch scope {
            case .all:
                Text("All days are available to this conversation.").font(.caption).foregroundStyle(.secondary)
            case .day(let key):
                DatePicker("Diary date", selection: Binding(get: { diary.date(for: key) ?? model.now }, set: { newConversation(.day(diary.dayKey($0))) }), displayedComponents: .date)
                    .environment(\.calendar, diary.calendar).environment(\.timeZone, diary.calendar.timeZone)
                    .accessibilityIdentifier("chat.day").disabled(loading)
                Text("Dates in \(diary.calendar.timeZone.identifier)").font(.caption).foregroundStyle(.secondary)
            case .range(let start, let end):
                rangeControls(start: start, end: end)
            case .meeting:
                Menu {
                    ForEach(Array(meetings.enumerated()), id: \.offset) { _, meeting in
                        Button(meetingTitle(meeting)) { newConversation(.meeting(meeting["id"].text)) }
                    }
                } label: { Label(scopeTitle, systemImage: "person.2") }
                    .accessibilityLabel("Choose meeting").accessibilityValue(scopeTitle)
                    .accessibilityIdentifier("chat.meeting").disabled(loading || meetings.isEmpty)
                if meetings.isEmpty { Text("No recorded meetings are available yet.").font(.caption).foregroundStyle(.secondary) }
            }
            } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Search within").font(.caption).foregroundStyle(.secondary)
                    Text(scopeTitle).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                }.padding(.vertical, 4)
            }
        }
        footer: { Text("Changing scope starts a new conversation. Saved conversations keep their original scope.") }
    }

    private func rangeControls(start: String, end: String) -> some View {
        let startDate = diary.date(for: start) ?? model.now
        let endDate = diary.date(for: end) ?? startDate
        return Group {
            DatePicker("Start date", selection: Binding(get: { startDate }, set: { newConversation(.range(diary.dayKey($0), end)) }), in: ...endDate, displayedComponents: .date)
                .accessibilityIdentifier("chat.startDay")
            DatePicker("End date", selection: Binding(get: { endDate }, set: { newConversation(.range(start, diary.dayKey($0))) }), in: startDate..., displayedComponents: .date)
                .accessibilityIdentifier("chat.endDay")
            Text("\(start) – \(end) · both dates included").font(.caption).foregroundStyle(.secondary)
            Text("Dates in \(diary.calendar.timeZone.identifier)").font(.caption).foregroundStyle(.secondary)
        }.environment(\.calendar, diary.calendar).environment(\.timeZone, diary.calendar.timeZone).disabled(loading)
    }

    private func selectPeriod(_ period: DiaryPeriod, previous: Bool) {
        let dates = diary.dateRange(for: period, containing: model.now, previous: previous)
        newConversation(.range(diary.dayKey(dates.lowerBound), diary.dayKey(dates.upperBound)))
    }

    private func meetingTitle(_ meeting: JSONValue) -> String {
        guard let date = AppModel.date(meeting["started_at"].text) else { return "Meeting · " + meeting["id"].text }
        return "Meeting · " + diary.label(date, template: "d MMM yyyy HHmm")
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

    private func newConversation(_ selection: ChatScope) {
        scope = selection; sessionID = nil; messages = []; question = ""; error = nil; cached = false; save()
    }

    private func load(restore: Bool) async {
        guard restore || !loading else { return }
        let key = cacheKey, request = UUID()
        requestID = request; loading = true; error = nil
        if restore {
            scope = initialScope ?? .all; sessionID = nil; messages = []; sessions = []; question = initialQuestion; cached = false
            if initialQuestion.isEmpty, let value = LocalCache.load(key) {
                sessions = value["sessions"].array
                let savedScope = ChatScope(value)
                if initialScope == nil || initialScope == savedScope {
                    messages = value["messages"].array; scope = savedScope
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
                if sessions.contains(where: { $0["id"].text == savedID }) {
                    let restored = try await model.client().request("v1/chat/sessions/\(savedID)")
                    guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
                    messages = restored["messages"].array; cached = false
                } else { messages = []; sessionID = nil; cached = false }
            }
            save()
        } catch {
            guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
            self.error = error.localizedDescription
        }
    }

    private func select(_ id: String) async {
        let key = cacheKey, request = UUID()
        requestID = request; loading = true; error = nil
        defer { if requestID == request { loading = false } }
        do {
            let result = try await model.client().request("v1/chat/sessions/\(id)")
            guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
            scope = ChatScope(result); sessionID = id; messages = result["messages"].array; question = ""; cached = false; save()
        } catch {
            guard !Task.isCancelled, requestID == request, cacheKey == key else { return }
            self.error = error.localizedDescription
        }
    }

    private func send() async {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !loading, scope.isReady else { sendTask = nil; return }
        guard !text.isEmpty, text.count <= 2000 else { sendTask = nil; error = "Keep questions under 2,000 characters."; return }
        let key = cacheKey, request = UUID(), client: BackendClient
        do { client = try model.client() } catch { sendTask = nil; self.error = error.localizedDescription; return }
        requestID = request; focused = false; loading = true; error = nil
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
            self.error = error.localizedDescription
        }
    }

    private func save() {
        var value: [String: JSONValue] = ["sessions": .array(sessions), "messages": .array(messages), "session_id": sessionID.map(JSONValue.string) ?? .null]
        for (field, text) in scope.fields { value[field] = .string(text) }
        LocalCache.save(.object(value), key: cacheKey)
    }
}

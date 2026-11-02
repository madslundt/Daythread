import SwiftUI
import OmiCore

/// App-owned workspaces outlive navigation. The backend owns accepted reply jobs.
@MainActor
final class ChatConversations {
    private var workspaces: [String: ChatConversationModel] = [:]

    func workspace(key: String, scope: ChatScope, question: String) -> ChatConversationModel {
        if let workspace = workspaces[key] { return workspace }
        let workspace = ChatConversationModel(key: key, scope: scope, question: question)
        workspaces[key] = workspace
        return workspace
    }
}

struct ChatReply: Codable, Equatable, Identifiable {
    let id: String
    let parameters: JSONValue
    var sessionID: String?
    var accepted = false
    var state = "queued"
    var draft = ""
    var stage = "searching"
    var error: String?
    var question: String { parameters["question"].text }
    var generating: Bool { ["queued", "running", "completed"].contains(state) }
}

@MainActor @Observable
final class ChatConversationModel {
    typealias Request = @MainActor (String, String, [String: Any]?) async throws -> JSONValue
    var scope: ChatScope
    var question: String { didSet { if !restoring { save() } } }
    private(set) var sessions: [JSONValue] = []
    private(set) var sessionID: String?
    private(set) var messages: [JSONValue] = []
    private(set) var context: JSONValue = .null
    private(set) var loading = false
    private(set) var error: String?
    private(set) var cached = false
    private(set) var replies: [String: ChatReply] = [:]
    @ObservationIgnored private var activeReplyID: String?
    @ObservationIgnored private let key: String
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private var request: Request?
    @ObservationIgnored private var operations: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var submissions: [String: UIBackgroundTaskIdentifier] = [:]
    @ObservationIgnored private var selection = UUID()
    @ObservationIgnored private var deletedIDs: Set<String> = []
    @ObservationIgnored private var restoreMostRecent = false
    @ObservationIgnored private var restoring = true
    @ObservationIgnored private let pollInterval: Duration

    init(key: String, scope: ChatScope, question: String = "", defaults: UserDefaults = .standard,
         pollInterval: Duration = .seconds(2)) {
        self.key = key; self.defaults = defaults; self.scope = scope; self.question = question
        self.pollInterval = pollInterval
        // Content caches can be invalidated by a recording edit. Selection and accepted
        // request IDs must survive that invalidation so the server can restore the chat.
        let legacy = LocalCache.load(key)
        let pointer = defaults.data(forKey: "chat.selection." + key)
            .flatMap { try? JSONDecoder().decode(JSONValue.self, from: $0) } ?? legacy
        restoreMostRecent = pointer == nil && question.isEmpty
        if let pointer, question.isEmpty {
            sessionID = pointer["session_id"].text.isEmpty ? nil : pointer["session_id"].text
            self.scope = ChatScope(pointer)
            self.question = pointer["draft"].text
            activeReplyID = pointer["active_reply_id"].text.isEmpty ? nil : pointer["active_reply_id"].text
        }
        if let data = defaults.data(forKey: "chat.replies." + key),
           let saved = try? JSONDecoder().decode([String: ChatReply].self, from: data) { replies = saved }
        if let legacy, legacy["session_id"].text == sessionID {
            sessions = legacy["sessions"].array; messages = legacy["messages"].array
            context = legacy["context"]
            cached = !messages.isEmpty
        }
        restoring = false
    }

    var currentReply: ChatReply? {
        if let sessionID { return replies.values.first { $0.sessionID == sessionID } }
        return activeReplyID.flatMap { replies[$0] }
    }
    var generating: Bool { currentReply?.generating == true }
    var streamingText: String { currentReply?.draft ?? "" }
    var streamStage: String { currentReply?.stage ?? "searching" }
    var displayedError: String? { error ?? currentReply?.error }
    var pendingQuestion: String {
        guard let reply = currentReply else { return "" }
        if messages.last?["role"].text == "user", messages.last?["content"].text == reply.question { return "" }
        return reply.question
    }
    var title: String {
        guard let session = sessions.first(where: { $0["id"].text == sessionID }) else { return L10n.text("Chat") }
        return ChatHistoryPresentation.title(session["title"].text, firstQuestion: messages.first?["content"].text ?? "")
    }

    func connect(client: BackendClient) throws {
        let snapshot = try client.snapshot()
        connect { path, method, body in try await snapshot.request(path, method: method, body: body) }
    }

    func connect(request: @escaping Request) {
        self.request = request
        for reply in replies.values where reply.generating { start(reply.id) }
    }

    func newConversation(_ scope: ChatScope, clearDraft: Bool = false) {
        restoreMostRecent = false
        selection = UUID(); loading = false
        self.scope = scope; sessionID = nil; activeReplyID = nil; messages = []; context = .null; error = nil; cached = false
        if clearDraft { question = "" }
        save()
    }

    func refresh() async {
        guard let request else { return }
        let expected = selection
        var selected = sessionID
        loading = true; error = nil
        defer { if selection == expected { loading = false } }
        do {
            let result = try await request("v1/chat/sessions", "GET", nil)
            guard selection == expected, !Task.isCancelled else { return }
            sessions = result["items"].array.filter { !deletedIDs.contains($0["id"].text) }
            if selected == nil, restoreMostRecent {
                selected = sessions.first(where: { ChatScope($0) == scope })?["id"].text
                sessionID = selected; restoreMostRecent = false
            }
            if let selected {
                let detail = try await request("v1/chat/sessions/" + selected, "GET", nil)
                guard selection == expected, !Task.isCancelled else { return }
                apply(detail)
            }
            save()
        } catch let failure as BackendFailure where failure.status == 404 {
            guard selection == expected, !Task.isCancelled else { return }
            if let selected { forget(selected) }
        } catch {
            if selection == expected, !Task.isCancelled { self.error = L10n.error(error) }
        }
    }

    func select(_ id: String) async {
        guard !deletedIDs.contains(id) else { return }
        restoreMostRecent = false
        selection = UUID(); sessionID = id; activeReplyID = nil; messages = []; context = .null; cached = false; error = nil
        save()
        await refresh()
    }

    /// Persist the request UUID before any networking, including before a tab can disappear.
    func send() {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard request != nil, !generating, scope.isReady, !text.isEmpty, text.unicodeScalars.count <= 2000 else { return }
        var parameters: [String: JSONValue] = ["question": .string(text)]
        if let sessionID { parameters["session_id"] = .string(sessionID) }
        else { for (field, value) in scope.fields { parameters[field] = .string(value) } }
        let reply = ChatReply(id: UUID().uuidString.lowercased(), parameters: .object(parameters), sessionID: sessionID)
        if let previous = currentReply { replies.removeValue(forKey: previous.id) }
        replies[reply.id] = reply; activeReplyID = reply.id; error = nil
        question = ""; save()
        start(reply.id)
    }

    func retry() async {
        error = nil
        if let reply = currentReply, reply.generating { start(reply.id) }
        else if let reply = currentReply, reply.state == "failed" {
            replies.removeValue(forKey: reply.id); activeReplyID = nil
            question = reply.question; save()
        } else { await refresh() }
    }

    func connectionFailed(_ failure: Error) { error = L10n.error(failure) }

    func remove(_ id: String) async {
        guard let request else { return }
        do {
            _ = try await request("v1/chat/sessions/" + id, "DELETE", nil)
            forget(id)
        } catch let failure as BackendFailure where failure.status == 404 { forget(id) }
        catch { self.error = L10n.error(error) }
    }

    private func forget(_ id: String) {
        deletedIDs.insert(id)
        if let uuid = UUID(uuidString: id) { CompletionNotifications.shared.removeChat(uuid) }
        for reply in replies.values where reply.sessionID == id {
            operations.removeValue(forKey: reply.id)?.cancel()
            replies.removeValue(forKey: reply.id)
        }
        sessions.removeAll { $0["id"].text == id }
        if sessionID == id { newConversation(scope, clearDraft: true) }
        save()
    }

    private func apply(_ detail: JSONValue) {
        let id = detail["id"].text
        guard !deletedIDs.contains(id) else { return }
        if sessionID == id {
            messages = detail["messages"].array; context = detail["context"]
            scope = ChatScope(detail); cached = false
        }
        upsert(detail)
        let job = detail["pending_job"]
        if !job["id"].text.isEmpty, replies[job["id"].text] == nil {
            let reply = ChatReply(id: job["id"].text, parameters: job["parameters"], sessionID: id,
                                  accepted: true, state: job["state"].text,
                                  draft: job["draft"].text, stage: job["stage"].text,
                                  error: job["error"].text.isEmpty ? nil : L10n.key(job["error"].text))
            replies[reply.id] = reply
            if reply.generating { start(reply.id) }
        }
    }

    private func upsert(_ detail: JSONValue) {
        guard case .object(var entry) = detail, !detail["id"].text.isEmpty else { return }
        entry.removeValue(forKey: "messages"); entry.removeValue(forKey: "pending_job")
        sessions.removeAll { $0["id"].text == detail["id"].text }
        sessions.append(.object(entry))
        sessions.sort { $0["updated_at"].text > $1["updated_at"].text }
    }

    private func start(_ id: String) {
        guard operations[id] == nil, request != nil else { return }
        operations[id] = Task { [self] in await follow(id) }
    }

    private func endSubmission(_ id: String) {
        if let token = submissions.removeValue(forKey: id), token != .invalid {
            UIApplication.shared.endBackgroundTask(token)
        }
    }

    private func follow(_ id: String) async {
        defer { operations.removeValue(forKey: id) }
        guard let request else { return }
        // Only submitting needs phone execution time. Accepted generation runs on the server.
        if replies[id]?.accepted == false {
            submissions[id] = UIApplication.shared.beginBackgroundTask(withName: "Save chat request") { [weak self] in
                Task { @MainActor in self?.endSubmission(id) }
            }
        }
        defer { endSubmission(id) }
        while let reply = replies[id], reply.generating, !Task.isCancelled {
            do {
                let job: JSONValue
                if !reply.accepted {
                    let params = try JSONSerialization.jsonObject(with: JSONEncoder().encode(reply.parameters))
                    job = try await request("v1/jobs", "POST", ["id": id, "kind": "chat", "parameters": params])
                    endSubmission(id)
                } else {
                    try await BackendClient.waitForForeground()
                    job = try await request("v1/jobs/" + id, "GET", nil)
                }
                guard var current = replies[id], !Task.isCancelled else { return }
                let assigned = job["parameters"]["session_id"].text
                guard !assigned.isEmpty, !deletedIDs.contains(assigned) else { return }
                current.sessionID = assigned; current.accepted = true; current.state = job["state"].text
                current.draft = job["draft"].text; current.stage = job["stage"].text
                current.error = job["error"].text.isEmpty ? nil : L10n.key(job["error"].text)
                replies[id] = current
                if activeReplyID == id, sessionID == nil { sessionID = assigned }
                save()
                let detail = try await request("v1/chat/sessions/" + assigned, "GET", nil)
                guard replies[id] != nil, !deletedIDs.contains(assigned), !Task.isCancelled else { return }
                apply(detail)
                if current.state == "completed" {
                    replies.removeValue(forKey: id)
                    if activeReplyID == id { activeReplyID = nil }
                    save(); return
                }
                save()
                if current.state == "failed" { return }
                guard current.generating else { throw BackendFailure(message: L10n.source("Home-core returned an invalid generation status.")) }
                try await Task.sleep(for: pollInterval)
            } catch is CancellationError { return }
            catch let failure as BackendFailure where failure.status == 404 && reply.accepted {
                if let session = reply.sessionID { forget(session) }
                return
            } catch {
                guard var current = replies[id], !Task.isCancelled else { return }
                current.error = L10n.error(error)
                if let failure = error as? BackendFailure, let status = failure.status, (400..<500).contains(status) {
                    current.state = "failed"
                }
                replies[id] = current; save(); endSubmission(id)
                if !current.generating { return }
                do { try await Task.sleep(for: .seconds(10)); try await BackendClient.waitForForeground() }
                catch { return }
            }
        }
    }

    func save() {
        var value: [String: JSONValue] = ["draft": .string(question), "session_id": sessionID.map(JSONValue.string) ?? .null,
                                        "active_reply_id": activeReplyID.map(JSONValue.string) ?? .null]
        for (field, text) in scope.fields { value[field] = .string(text) }
        if let data = try? JSONEncoder().encode(JSONValue.object(value)) {
            defaults.set(data, forKey: "chat.selection." + key)
        }
        if let data = try? JSONEncoder().encode(replies) { defaults.set(data, forKey: "chat.replies." + key) }
        value["messages"] = .array(messages); value["sessions"] = .array(sessions)
        value["context"] = context
        LocalCache.save(.object(value), key: key)
    }
}

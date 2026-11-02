import XCTest
import Foundation
@testable import Daythread

@MainActor
final class ChatConversationTests: XCTestCase {
    func testSelectedChatAndDraftSurviveContentCacheInvalidation() async throws {
        let storage = storage()
        defer { storage.clean() }
        let server = FakeChatServer()
        server.complete()
        let model = makeModel(storage)
        connect(model, server)
        await model.select(server.sessionID)
        model.question = "Follow up on that decision"
        XCTAssertEqual(model.messages.count, 2)
        XCTAssertNotNil(LocalCache.load(storage.key))

        LocalCache.reconcile(address: storage.address, revision: UUID().uuidString)
        XCTAssertNil(LocalCache.load(storage.key), "Changing transcript evidence must invalidate cached content")
        let restored = makeModel(storage)
        XCTAssertEqual(restored.sessionID, server.sessionID, "Chat selection must outlive the transcript cache")
        XCTAssertEqual(restored.question, "Follow up on that decision")
        connect(restored, server)
        await restored.refresh()
        XCTAssertEqual(restored.messages.count, 2)
        XCTAssertEqual(restored.messages.last?["content"].text, "Saved answer")
    }

    func testSendPersistsItsRequestIDBeforeNetworkingStarts() async throws {
        let storage = storage()
        defer { storage.clean() }
        let server = FakeChatServer()
        server.postState = "completed"
        let model = makeModel(storage)
        model.connect { path, method, body in
            if method == "POST" {
                let data = try XCTUnwrap(storage.defaults.data(forKey: "chat.replies." + storage.key))
                let saved = try JSONDecoder().decode([String: ChatReply].self, from: data)
                let id = try XCTUnwrap(body?["id"] as? String)
                XCTAssertEqual(saved[id]?.question, "What did we decide?")
                XCTAssertNotNil(UUID(uuidString: id))
            }
            return try await server.request(path, method, body)
        }
        model.question = "What did we decide?"
        model.send()
        let replyID = try XCTUnwrap(model.currentReply?.id)
        let pointerData = try XCTUnwrap(storage.defaults.data(forKey: "chat.selection." + storage.key))
        let pointer = try JSONDecoder().decode(JSONValue.self, from: pointerData)
        XCTAssertEqual(pointer["active_reply_id"].text, replyID)
        XCTAssertTrue(server.postIDs.isEmpty, "send() saves synchronously before its network task can run")
        await eventually { model.replies.isEmpty }
        XCTAssertEqual(server.postIDs, [replyID])
    }

    func testNavigatingToNewChatDoesNotRetargetALateAcceptedReply() async throws {
        let storage = storage()
        defer { storage.clean() }
        let gate = Gate()
        let server = FakeChatServer()
        server.postGate = gate
        server.postState = "completed"
        let model = makeModel(storage)
        connect(model, server)
        model.question = "First question"
        model.send()
        await eventually { !server.postIDs.isEmpty }

        model.newConversation(.day("2026-11-02"), clearDraft: true)
        model.question = "Second question"
        gate.open()
        await eventually { model.replies.isEmpty }

        XCTAssertNil(model.sessionID)
        XCTAssertTrue(model.messages.isEmpty)
        XCTAssertEqual(model.question, "Second question")
        XCTAssertEqual(model.scope, .day("2026-11-02"))
        XCTAssertEqual(model.sessions.first?["id"].text, server.sessionID, "The first answer still belongs in history")
        await model.select(server.sessionID)
        XCTAssertEqual(model.messages.last?["content"].text, "Saved answer")
    }

    func testReloadingAnAcceptedPendingReplyPollsTheSameJobWithoutResubmission() async throws {
        let storage = storage()
        defer { storage.clean() }
        let server = FakeChatServer()
        server.cancelFirstPoll = true
        let original = makeModel(storage)
        connect(original, server)
        original.question = "Keep working while I leave"
        original.send()
        let id = try XCTUnwrap(original.currentReply?.id)
        await eventually { server.pollIDs == [id] }
        XCTAssertTrue(original.currentReply?.accepted == true)
        XCTAssertTrue(try savedReplies(storage)[id]?.accepted == true, "The accepted job must be durable before reconstruction")

        let restored = makeModel(storage)
        XCTAssertEqual(restored.currentReply?.id, id)
        XCTAssertEqual(restored.sessionID, server.sessionID)
        server.complete()
        connect(restored, server)
        await eventually { restored.replies.isEmpty }
        XCTAssertEqual(server.postIDs, [id], "An accepted job must be resumed instead of generating another reply")
        XCTAssertEqual(server.pollIDs, [id, id])
        XCTAssertEqual(restored.messages.last?["content"].text, "Saved answer")
    }

    func testReloadingAnUnacceptedRequestReusesThePersistedIdempotencyKey() async throws {
        let storage = storage()
        defer { storage.clean() }
        let server = FakeChatServer()
        let original = makeModel(storage)
        original.connect { path, method, body in
            if method == "POST" {
                server.postIDs.append(try XCTUnwrap(body?["id"] as? String))
                throw CancellationError()
            }
            return try await server.request(path, method, body)
        }
        original.question = "Save this before my connection drops"
        original.send()
        let id = try XCTUnwrap(original.currentReply?.id)
        await eventually { server.postIDs == [id] }
        XCTAssertEqual(try savedReplies(storage)[id]?.question, "Save this before my connection drops")

        let restored = makeModel(storage)
        XCTAssertEqual(restored.currentReply?.id, id)
        XCTAssertFalse(try XCTUnwrap(restored.currentReply).accepted)
        server.postState = "completed"
        connect(restored, server)
        await eventually { restored.replies.isEmpty }
        XCTAssertEqual(server.postIDs, [id, id])
        XCTAssertEqual(restored.messages.count, 2)
    }

    func testDeletingAnInFlightChatDoesNotRestoreItsLateAnswer() async throws {
        let storage = storage()
        defer { storage.clean() }
        let gate = Gate()
        let server = FakeChatServer()
        server.pollGate = gate
        let model = makeModel(storage)
        connect(model, server)
        model.question = "A reply I will delete"
        model.send()
        await eventually { !server.pollIDs.isEmpty }
        XCTAssertEqual(model.sessionID, server.sessionID)
        await model.remove(server.sessionID)
        XCTAssertNil(model.sessionID)
        XCTAssertTrue(model.replies.isEmpty)

        server.complete()
        gate.open()
        await eventually { server.returnedLatePoll }
        await Task.yield()
        XCTAssertNil(model.sessionID)
        XCTAssertTrue(model.messages.isEmpty)
        XCTAssertTrue(model.sessions.isEmpty)
        XCTAssertTrue(model.replies.isEmpty)
        let restored = makeModel(storage)
        XCTAssertNil(restored.sessionID)
        XCTAssertTrue(restored.replies.isEmpty)
    }

    func testSelectionsAndDraftsAreIsolatedByProfileAndArchiveKey() async throws {
        let storage = storage()
        defer { storage.clean() }
        let server = FakeChatServer()
        let selected = makeModel(storage)
        connect(selected, server)
        await selected.select(server.sessionID)
        selected.question = "Private profile draft"

        for key in [storage.address + ".other-profile.chat", "other-archive." + storage.key] {
            let other = ChatConversationModel(key: key, scope: .all, defaults: storage.defaults)
            XCTAssertNil(other.sessionID)
            XCTAssertEqual(other.question, "")
            XCTAssertTrue(other.messages.isEmpty)
            XCTAssertTrue(other.replies.isEmpty)
            UserDefaults.standard.removeObject(forKey: "cache." + key)
        }
        let restored = makeModel(storage)
        XCTAssertEqual(restored.sessionID, server.sessionID)
        XCTAssertEqual(restored.question, "Private profile draft")
    }

    func testMissingPointerRestoresMostRecentCompatibleChatButIntentionalNewChatStaysBlank() async throws {
        let storage = storage()
        defer { storage.clean() }
        let server = FakeChatServer()
        server.complete()
        server.listedSessions = [
            .object(["id": .string(UUID().uuidString), "title": .string("Other diary"),
                     "day": .string("2026-11-01"), "updated_at": .string("2026-11-02T12:03:00Z")]),
            .object(["id": .string(server.sessionID), "title": .string("Latest chat"),
                     "updated_at": .string("2026-11-02T12:02:00Z")]),
            .object(["id": .string(UUID().uuidString), "title": .string("Older chat"),
                     "updated_at": .string("2026-11-02T12:01:00Z")]),
        ]
        let model = makeModel(storage)
        XCTAssertNil(model.sessionID)
        connect(model, server)
        await model.refresh()
        XCTAssertEqual(model.sessionID, server.sessionID, "An empty local install should restore the latest chat in the matching scope")
        XCTAssertEqual(model.messages.last?["content"].text, "Saved answer")

        model.newConversation(.all, clearDraft: true)
        let restored = makeModel(storage)
        connect(restored, server)
        await restored.refresh()
        XCTAssertNil(restored.sessionID, "Choosing New conversation must survive reopening without jumping back to an older chat")
        XCTAssertTrue(restored.messages.isEmpty)
        XCTAssertEqual(restored.question, "")
        XCTAssertFalse(restored.sessions.isEmpty, "History is still available from the intentional blank chat")
    }

    func testFailedDeletionKeepsTheChatAndReportsAnError() async throws {
        let storage = storage()
        defer { storage.clean() }
        let server = FakeChatServer()
        server.complete()
        let model = makeModel(storage)
        connect(model, server)
        await model.select(server.sessionID)
        server.deleteFailure = BackendFailure(message: "The archive is unavailable", status: 503)
        await model.remove(server.sessionID)
        XCTAssertEqual(model.sessionID, server.sessionID)
        XCTAssertEqual(model.messages.count, 2)
        XCTAssertTrue(model.sessions.contains { $0["id"].text == server.sessionID })
        XCTAssertNotNil(model.displayedError)
        let restored = makeModel(storage)
        XCTAssertEqual(restored.sessionID, server.sessionID, "A failed delete must not erase the durable selection")
        XCTAssertEqual(restored.messages.count, 2)
    }

    private func savedReplies(_ storage: Storage) throws -> [String: ChatReply] {
        let data = try XCTUnwrap(storage.defaults.data(forKey: "chat.replies." + storage.key))
        return try JSONDecoder().decode([String: ChatReply].self, from: data)
    }

    func testCompactionMetadataDoesNotReplaceMessagesAndStaysWithItsConversation() async throws {
        let storage = storage()
        defer { storage.clean() }
        let server = FakeChatServer()
        server.complete()
        server.context = .object(["compaction_count": .number(2), "context_limit": .number(8192)])
        let model = makeModel(storage)
        connect(model, server)
        await model.select(server.sessionID)
        XCTAssertEqual(model.context["compaction_count"].integer, 2)
        XCTAssertEqual(model.messages.count, 2)
        XCTAssertEqual(model.messages.last?["content"].text, "Saved answer")
        let restored = makeModel(storage)
        XCTAssertEqual(restored.context["compaction_count"].integer, 2)
        restored.newConversation(.all, clearDraft: true)
        XCTAssertEqual(restored.context, .null)
        XCTAssertTrue(restored.messages.isEmpty)
    }

    private func makeModel(_ storage: Storage) -> ChatConversationModel {
        ChatConversationModel(key: storage.key, scope: .all, defaults: storage.defaults, pollInterval: .milliseconds(10))
    }

    private func connect(_ model: ChatConversationModel, _ server: FakeChatServer) {
        model.connect { path, method, body in try await server.request(path, method, body) }
    }

    private func eventually(_ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        while !condition(), ContinuousClock.now < deadline { await Task.yield() }
        XCTAssertTrue(condition(), "Expected chat state did not arrive", file: file, line: line)
    }

    private struct Storage {
        let suite: String
        let defaults: UserDefaults
        let address: String
        var key: String { address + ".profile.chat" }
        func clean() {
            defaults.removePersistentDomain(forName: suite)
            UserDefaults.standard.removeObject(forKey: "cache." + key)
            UserDefaults.standard.removeObject(forKey: "archiveRevision." + address)
        }
    }

    private func storage() -> Storage {
        let id = UUID().uuidString
        let suite = "ChatConversationTests." + id
        return Storage(suite: suite, defaults: UserDefaults(suiteName: suite)!, address: "chat-test." + id)
    }
}

@MainActor
private final class Gate {
    private var continuations: [CheckedContinuation<Void, Never>] = []
    private var opened = false
    func wait() async {
        guard !opened else { return }
        await withCheckedContinuation { continuations.append($0) }
    }
    func open() {
        opened = true
        let waiting = continuations
        continuations.removeAll()
        for continuation in waiting { continuation.resume() }
    }
}

@MainActor
private final class FakeChatServer {
    let sessionID = UUID().uuidString.lowercased()
    var postIDs: [String] = []
    var pollIDs: [String] = []
    var postState = "running"
    var postGate: Gate?
    var pollGate: Gate?
    var cancelFirstPoll = false
    var returnedLatePoll = false
    var listedSessions: [JSONValue]?
    var deleteFailure: BackendFailure?
    var context: JSONValue = .null
    private var state = "running"
    private var parameters = JSONValue.object(["question": .string("Saved question")])
    private var deleted = false

    func complete() { state = "completed" }

    func request(_ path: String, _ method: String, _ body: [String: Any]?) async throws -> JSONValue {
        if path == "v1/jobs", method == "POST" {
            let id = try XCTUnwrap(body?["id"] as? String)
            postIDs.append(id)
            if let params = body?["parameters"] as? [String: Any] {
                parameters = try JSONDecoder().decode(JSONValue.self, from: JSONSerialization.data(withJSONObject: params))
            }
            await postGate?.wait()
            state = postState
            return job(id)
        }
        if path.hasPrefix("v1/jobs/"), method == "GET" {
            let id = String(path.dropFirst("v1/jobs/".count))
            pollIDs.append(id)
            if cancelFirstPoll { cancelFirstPoll = false; throw CancellationError() }
            await pollGate?.wait()
            returnedLatePoll = true
            return job(id)
        }
        if path == "v1/chat/sessions", method == "GET" {
            return .object(["items": .array(deleted ? [] : (listedSessions ?? [detail]))])
        }
        if path == "v1/chat/sessions/" + sessionID {
            if method == "DELETE" {
                if let deleteFailure { throw deleteFailure }
                deleted = true; return .object([:])
            }
            if method == "GET" { return detail }
        }
        XCTFail("Unexpected fake chat request: \(method) \(path)")
        throw BackendFailure(message: "Unexpected test request", status: 404)
    }

    private func job(_ id: String) -> JSONValue {
        var fields: [String: JSONValue] = [:]
        if case .object(let value) = parameters { fields = value }
        fields["session_id"] = .string(sessionID)
        return .object(["id": .string(id), "kind": .string("chat"), "parameters": .object(fields), "state": .string(state)])
    }

    private var detail: JSONValue {
        let user: JSONValue = .object(["id": .string("user-message"), "role": .string("user"), "content": parameters["question"]])
        let assistant: JSONValue = .object(["id": .string("assistant-message"), "role": .string("assistant"), "content": .string("Saved answer")])
        return .object(["id": .string(sessionID), "title": parameters["question"],
                        "created_at": .string("2026-11-02T12:00:00Z"), "updated_at": .string("2026-11-02T12:01:00Z"),
                        "messages": .array(state == "completed" ? [user, assistant] : [user]), "context": context])
    }
}

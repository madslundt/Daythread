import Foundation
import Observation

struct MemoryLabel: Codable, Identifiable {
    let id: String
    var kind: String
    var name: String
    var description: String
    var aliases: [String]
    var draft: LabelDraft { LabelDraft(kind: kind, name: name, description: description, aliases: aliases) }
}
struct LabelDraft: Codable {
    var kind = "person"
    var name = ""
    var description = ""
    var aliases: [String] = []
}
struct LabelDirectory: Decodable { let revision: String; let items: [MemoryLabel] }
struct LabelSource: Codable, Identifiable {
    let id: String
    let source_version: String
    var revision: String { source_version }
    let started_at: String
    let ended_at: String
    let device_id: String
    let state: String
    let text: String?
    let audio_available: Bool
    let source_kind: String
    let audio_availability: String?
    let transcription_outcome: String?
    let error_code: String?
    let source_id: String?
    var ref: LabelSourceRef { LabelSourceRef(id: id, revision: revision) }
    var json: JSONValue { (try? JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(self))) ?? .null }
}
struct LabelSourceRef: Codable { let id: String; let revision: String }
struct LabelAssociation: Decodable, Identifiable {
    let id: String
    let entity_id: String
    let source_id: String
    let relation: String
    let needs_review: Bool
}
struct SourceLabelsResponse: Decodable { let revision: String; let source: LabelSource; let links: [LabelAssociation] }
struct LabelTimelineEntry: Decodable, Identifiable {
    let id: String
    let entry_type: String
    let relation: String?
    let kind: String?
    let title: String?
    let body: String?
    let decision_id: String?
    let decision: DecisionRecord?
    let entity_ids: [String]?
    let sources: [LabelSource]
    let needs_review: Bool
    let started_at: String
}
struct LabelTimelineResponse: Decodable {
    let revision: String
    let entity: MemoryLabel
    let items: [LabelTimelineEntry]
    let total: Int
}
struct LabelCommand: Encodable {
    var action: String
    var id: String
    var expected_revision: String
    var mutation_id: String?
    var create: Bool?
    var value: LabelDraft?
    var entity_id: String?
    var source: LabelSourceRef?
    var relation: String?
    var kind: String?
    var title: String?
    var body: String?
    var sources: [LabelSourceRef]?
    var entity_ids: [String]?
    var target_id: String?
    var new_id: String?
    var link_ids: [String]?
    var note_ids: [String]?
    var decision_link_ids: [String]?
    var decision_id: String?
    var decision_version: Int?
}

extension BackendClient {
    func labels<T: Decodable>(_ path: String = "v1/labels", query: [String: String] = [:]) async throws -> T {
        let result = try await request(path, query: query)
        return try JSONDecoder().decode(T.self, from: JSONEncoder().encode(result))
    }
}

@MainActor @Observable final class LabelWriter {
    var busy = false
    var error: String?
    private var signature: Data?
    private var mutationID = UUID().uuidString.lowercased()
    func save(_ command: LabelCommand, client: BackendClient) async -> Bool {
        guard !busy else { return false }
        busy = true; error = nil
        defer { busy = false }
        do {
            var command = command
            let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
            let bytes = try encoder.encode(command) + Data(client.base.absoluteString.utf8)
            if signature != bytes { signature = bytes; mutationID = UUID().uuidString.lowercased() }
            command.mutation_id = mutationID
            let object = try JSONSerialization.jsonObject(with: encoder.encode(command)) as! [String: Any]
            _ = try await client.request("v1/labels/mutations", method: "POST", body: object)
            signature = nil
            return true
        } catch { self.error = L10n.error(error); return false }
    }
}

func associationMeaning(_ value: String) -> String {
    switch value {
    case "mentioned": L10n.text("Mentioned")
    case "participant": L10n.text("Participant · confirmed by you")
    case "about_project": L10n.text("About project")
    default: L10n.text("Related · no participation claim")
    }
}
func annotationMeaning(_ value: String) -> String {
    switch value {
    case "decision": L10n.text("Decision note · personal annotation, separate from register")
    case "open_question": L10n.text("Open question · written by you")
    default: L10n.text("Conversation · grouped by you")
    }
}

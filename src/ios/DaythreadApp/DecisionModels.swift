import Foundation

struct DecisionAnchor: Codable, Equatable {
    var source_id: String
    var source_version: String
    var quote: String
    var start: Int
    var end: Int
}
struct DecisionClaim: Codable, Equatable {
    var text: String
    var evidence: [DecisionAnchor]
}
struct DecisionContradiction: Codable, Equatable {
    var field = "choice"
    var text = ""
    var evidence: [DecisionAnchor] = []
    var claim: DecisionClaim {
        get { DecisionClaim(text: text, evidence: evidence) }
        set { text = newValue.text; evidence = newValue.evidence }
    }
}
struct DecisionRelationship: Codable, Equatable {
    var kind: String
    var label: String
    var target_id: String?
}
struct DecisionContent: Codable, Equatable {
    var choice: DecisionClaim
    var context: DecisionClaim?
    var rationale: DecisionClaim?
    var alternatives: [DecisionClaim] = []
    var contradictions: [DecisionContradiction] = []
    var owner_notes = ""
    var relationships: [DecisionRelationship] = []
}
extension DecisionContent {
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        choice = try values.decode(DecisionClaim.self, forKey: .choice)
        context = try values.decodeIfPresent(DecisionClaim.self, forKey: .context)
        rationale = try values.decodeIfPresent(DecisionClaim.self, forKey: .rationale)
        alternatives = try values.decodeIfPresent([DecisionClaim].self, forKey: .alternatives) ?? []
        contradictions = try values.decodeIfPresent([DecisionContradiction].self, forKey: .contradictions) ?? []
        owner_notes = try values.decodeIfPresent(String.self, forKey: .owner_notes) ?? ""
        relationships = try values.decodeIfPresent([DecisionRelationship].self, forKey: .relationships) ?? []
    }
}
struct DecisionRecord: Codable, Identifiable {
    let id: String
    let thread_id: String
    let version: Int
    let state: String
    let display_state: String
    let review_status: String
    let evidence_condition: String
    let content: DecisionContent?
    let started_at: String
    let ended_at: String
    let created_at: String
    let updated_at: String
    let predecessor_id: String?
    let successor_id: String?
    let history: [DecisionHistory]?
    var title: String { content?.choice.text ?? "Removed decision" }
}
struct DecisionHistory: Codable {
    let version: Int
    let event: String
    let content: DecisionContent?
    let created_at: String
}
struct DecisionList: Decodable { let items: [DecisionRecord]; let total: Int }
struct DecisionExtraction: Decodable {
    let id: String
    let status: String
    let processed_sources: Int
    let processed_source_offset: Int?
    let total_sources: Int
    let pending_chunks: Int
    let gaps: Int
    let suppressed_sources: Int?
    let items: [DecisionRecord]
    let next_run_id: String?
}
struct DecisionCreate: Encodable {
    var command_id = UUID().uuidString
    let state: String
    let content: DecisionContent
}
struct DecisionEvent: Encodable {
    var command_id = UUID().uuidString
    let expected_version: Int
    let type: String
    var content: DecisionContent?
    var state: String?
    var predecessor_id: String?
    var predecessor_version: Int?
}
struct DecisionExtractionRequest: Encodable {
    var request_id = UUID().uuidString
    var day: String?
    var source_ids: [String]?
    var run_id: String?
}
struct DecisionSource: Decodable, Identifiable {
    let id: String
    let source_version: String
    let text: String
    let started_at: String
    let audio_available: Bool

    var initialQuotation: String { text.unicodeScalars.count <= 20_000 ? text : "" }

    // Swift's String.count counts grapheme clusters; backend offsets count Unicode code points.
    func anchor(quote: String, occurrence: Int = 0) -> DecisionAnchor? {
        let haystack = Array(text.unicodeScalars), needle = Array(quote.unicodeScalars)
        guard !needle.isEmpty, needle.count <= 20_000, needle.count <= haystack.count, occurrence >= 0 else { return nil }
        var match = 0
        for start in 0...(haystack.count - needle.count) {
            if Array(haystack[start..<(start + needle.count)]) == needle {
                if match == occurrence {
                    return DecisionAnchor(source_id: id, source_version: source_version, quote: quote, start: start, end: start + needle.count)
                }
                match += 1
            }
        }
        return nil
    }
}

extension BackendClient {
    // Decision content never enters URLCache, LocalCache or the durable capture outbox.
    func decisionRequest<Response: Decodable>(_ path: String, method: String = "GET", query: [String: String] = [:], body: (any Encodable)? = nil) async throws -> Response {
        let data = try await data(path: path, method: method, query: query, body: body.map { try JSONEncoder().encode($0) }, profileHeader: profileID, timeout: path == "v1/decision-extractions" ? 3600 : 180, ephemeral: true)
        return try JSONDecoder().decode(Response.self, from: data)
    }
    func deleteDecision(_ record: DecisionRecord) async throws {
        _ = try await data(path: "v1/decisions/\(record.id)", method: "DELETE", query: ["expected_version": "\(record.version)"], profileHeader: profileID, ephemeral: true)
    }
}

extension BackendFailure {
    var requiresDecisionReload: Bool { status == 404 || status == 409 }
}

import Foundation

struct PrepSearch: Codable, Equatable {
    var kind = "person"
    var subject = ""
    var aliases: [String] = []
    var qualifier = ""
    var start_day: String?
    var end_day: String?
    var offset = 0
}
struct PrepSelection: Codable { let id: String; let version: String }
struct PrepValidation: Encodable { let search: PrepSearch; let snapshot: String }
struct PrepValidity: Decodable { let valid: Bool }
struct PrepGenerate: Encodable {
    let search: PrepSearch
    let snapshot: String
    let selected: [PrepSelection]
    let latest_source_id: String
    let user_context: String
}
struct PrepCoverage: Codable {
    let total_matches: Int
    let pending_chunks: Int
    let capture_gaps: Int
    let returned: Int
    let omitted_matches: Int
    let limitations: [String]
}
struct PrepSource: Codable, Identifiable {
    let id: String
    let version: String
    let device_id: String
    let started_at: String
    let ended_at: String
    let state: String
    let text: String
    let audio_available: Bool
    let source_kind: String
    let source_id: String
    let source_version: String
    let transcript_version: String?
    var json: JSONValue {
        .object(["id": .string(id), "started_at": .string(started_at), "ended_at": .string(ended_at),
                 "text": .string(text), "state": .string(state), "device_id": .string(device_id),
                 "audio_available": .bool(audio_available), "source_kind": .string(source_kind),
                 "source_version": .string(source_version), "transcript_version": transcript_version.map(JSONValue.string) ?? .null])
    }
}
struct PrepSearchResult: Decodable {
    let as_of: String
    let search: PrepSearch
    let snapshot: String
    let timezone: String
    let sources: [PrepSource]
    let coverage: PrepCoverage
}
struct PrepEvidence: Decodable {
    let source_id: String
    let version: String
    let excerpt: String
    let started_at: String
    let ended_at: String
    let segment_start: Double?
    let segment_end: Double?
}
struct PrepItem: Decodable {
    let text: String
    let citations: [PrepEvidence]
    let completion_status: String?
}
struct PrepReviewedExcerpt: Decodable, Identifiable {
    let id: String
    let text: String
    let started_at: String
    let omitted_characters: Int
}
struct PrepResult: Decodable {
    let as_of: String
    let reviewed_excerpts: [PrepReviewedExcerpt]
    let generated_at: String
    let model: String
    let prompt_version: String
    let latest_source_id: String
    let user_context: String
    let sources: [PrepSource]
    let coverage: PrepCoverage
    let latest_discussion: [PrepItem]
    let promises_to_check: [PrepItem]
    let recorded_questions: [PrepItem]
    let suggested_questions: [PrepItem]
}

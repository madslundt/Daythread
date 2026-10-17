import Foundation

/// Only generation endpoints become durable jobs; reads and corrections retain
/// their original behavior. The job UUID is created once before endpoint retries.
public struct GenerationRoute: Equatable {
    public let kind: String
    public let parameters: [String: String]
    public init?(path: String, method: String) {
        guard method == "POST" else { return nil }
        let parts = path.split(separator: "/").map(String.init)
        switch parts {
        case ["v1", "rewind"]:
            kind = "rewind"; parameters = [:]
        case let p where p.count == 3 && p[0] == "v1" && p[1] == "recaps":
            kind = "recap"; parameters = ["day": p[2]]
        case let p where p.count == 4 && p[0] == "v1" && p[1] == "transcripts" && p[3] == "translation":
            kind = "translation"; parameters = ["source_id": p[2]]
        case let p where p.count == 4 && p[0] == "v1" && p[1] == "meetings" && p[3] == "recap":
            kind = "meeting_recap"; parameters = ["meeting_id": p[2]]
        default: return nil
        }
    }
}

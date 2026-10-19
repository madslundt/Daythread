import Foundation

/// The same intersection scopes the day recap, topic browser, highlights and recordings.
public struct DayFilterScope: Equatable, Sendable {
    public let tagID: String
    public let zoneID: String
    public init(tagID: String = "", zoneID: String = "") { self.tagID = tagID; self.zoneID = zoneID }
    public var isFiltered: Bool { !tagID.isEmpty || !zoneID.isEmpty }
    public var query: [String: String] {
        var result: [String: String] = [:]
        if !tagID.isEmpty { result["tag_id"] = tagID }
        if !zoneID.isEmpty { result["zone_id"] = zoneID }
        return result
    }
    public var identity: String { try! JSONEncoder().encode([tagID, zoneID]).base64EncodedString() }

    /// Filtered content must never accept an unscoped or differently scoped response.
    public func matchesResponseScope(_ scope: [String: String]?) -> Bool {
        guard let scope else { return !isFiltered }
        return scope["tag_id", default: ""] == tagID && scope["zone_id", default: ""] == zoneID
    }

    public func matchesSnapshot(_ scope: [String: String]?, fingerprint: String, expectedFingerprint: String) -> Bool {
        matchesResponseScope(scope) && (!isFiltered || (!expectedFingerprint.isEmpty && fingerprint == expectedFingerprint))
    }
}

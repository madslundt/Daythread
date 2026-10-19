import Foundation

/// Versioned references request display labels without submitting client-authored prose.
public enum DisplayTranslations {
    public struct Reference: Codable, Hashable, Sendable {
        public let kind: String
        public let id: String
        public let version: Int?
        public let source_version: String?
        public let transcript_version: String?
        public let start_offset: Int?
        public let end_offset: Int?
        public let chapter_id: String?
        public init(kind: String, id: String, version: Int? = nil, source_version: String? = nil,
                    transcript_version: String? = nil, start_offset: Int? = nil, end_offset: Int? = nil,
                    chapter_id: String? = nil) {
            self.kind = kind; self.id = id; self.version = version; self.source_version = source_version
            self.transcript_version = transcript_version; self.start_offset = start_offset; self.end_offset = end_offset
            self.chapter_id = chapter_id
        }
    }
    public struct Label: Encodable, Equatable, Sendable {
        public let reference: Reference
        public let title: String
        public init(reference: Reference, title: String) { self.reference = reference; self.title = title }
    }
    public struct Request: Encodable {
        public let language: String
        public let items: [Reference]
        public init(language: String, items: [Reference]) { self.language = language; self.items = items }
    }
    public struct Entry: Decodable, Sendable {
        public let reference: Reference
        public let original_title: String
        public let title: String
        public let content_key: String
        private enum CodingKeys: String, CodingKey { case original_title, title, content_key }
        public init(from decoder: Decoder) throws {
            reference = try Reference(from: decoder)
            let values = try decoder.container(keyedBy: CodingKeys.self)
            original_title = try values.decode(String.self, forKey: .original_title)
            title = try values.decode(String.self, forKey: .title)
            content_key = try values.decode(String.self, forKey: .content_key)
        }
        public init(reference: Reference, original_title: String, title: String, content_key: String) {
            self.reference = reference; self.original_title = original_title; self.title = title; self.content_key = content_key
        }
    }
    public struct Response: Decodable, Sendable {
        public let language: String
        public let items: [Entry]
        public init(language: String, items: [Entry]) { self.language = language; self.items = items }
    }

    public static func project(_ response: Response, language: String, labels: [Label]) -> [Reference: String]? {
        guard !language.isEmpty, response.language == language, response.items.count == labels.count,
              Set(labels.map(\.reference)).count == labels.count else { return nil }
        var result: [Reference: String] = [:]
        for (entry, label) in zip(response.items, labels) {
            guard entry.reference == label.reference, entry.original_title == label.title,
                  !entry.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !entry.content_key.isEmpty else { return nil }
            result[label.reference] = entry.title
        }
        return result
    }

    public static func identity(language: String?, labels: [Label]) -> String {
        struct Identity: Encodable { let language: String?; let labels: [Label] }
        let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys
        return try! encoder.encode(Identity(language: language, labels: labels)).base64EncodedString()
    }
}

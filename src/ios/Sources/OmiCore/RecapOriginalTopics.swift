import Foundation

/// Original topic navigation stays independent of the selective generated recap.
public enum RecapOriginalTopics {
    public struct Section: Equatable, Sendable {
        public let title: String
        public let chapterID: String
        public let start: Int
        public let end: Int
        public init(title: String, chapterID: String = "", start: Int, end: Int) {
            self.title = title; self.chapterID = chapterID; self.start = start; self.end = end
        }
    }
    public struct Source: Sendable {
        public let id: String
        public let sourceVersion: String
        public let transcriptVersion: String
        public let text: String
        public let sections: [Section]
        public init(id: String, sourceVersion: String, transcriptVersion: String, text: String, sections: [Section]) {
            self.id = id; self.sourceVersion = sourceVersion; self.transcriptVersion = transcriptVersion
            self.text = text; self.sections = sections
        }
    }
    public struct Part: Equatable, Identifiable, Sendable {
        public var id: String { sourceID + ":\(start):\(end)" }
        public let sourceID: String
        public let sourceVersion: String
        public let transcriptVersion: String
        public let title: String
        public let chapterID: String
        public let start: Int
        public let end: Int
        public let quote: String
    }
    public struct Topic: Identifiable, Equatable, Sendable {
        public let id: String
        public let title: String
        public var parts: [Part]
    }

    public static func topics(sources: [Source], excluding covered: [InboxTopicPassage] = [], query: String = "") -> [Topic] {
        var topics: [Topic] = []
        for source in sources where !source.sourceVersion.isEmpty && !source.transcriptVersion.isEmpty {
            let scalars = Array(source.text.unicodeScalars)
            for section in source.sections {
                guard !section.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                      let quote = passage(scalars, start: section.start, end: section.end),
                      !covered.contains(where: { $0.contains(sourceID: source.id, start: section.start, end: section.end) }) else { continue }
                let part = Part(sourceID: source.id, sourceVersion: source.sourceVersion, transcriptVersion: source.transcriptVersion,
                                title: section.title, chapterID: section.chapterID, start: section.start, end: section.end, quote: quote)
                let id = section.chapterID.isEmpty ? part.id : "chapter:" + section.chapterID
                if let index = topics.firstIndex(where: { $0.id == id }) {
                    if !topics[index].parts.contains(where: { $0.id == part.id }) { topics[index].parts.append(part) }
                } else { topics.append(Topic(id: id, title: section.title, parts: [part])) }
            }
        }
        let search = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !search.isEmpty else { return topics }
        return topics.filter { ($0.title + " " + $0.parts.map(\.quote).joined(separator: " "))
            .range(of: search, options: [.caseInsensitive, .diacriticInsensitive]) != nil }
    }

    public static func matchesCurrent(_ part: Part, source: Source) -> Bool {
        source.id == part.sourceID && source.sourceVersion == part.sourceVersion &&
        source.transcriptVersion == part.transcriptVersion &&
        passage(Array(source.text.unicodeScalars), start: part.start, end: part.end) == part.quote &&
        source.sections.contains {
            $0.title == part.title && $0.chapterID == part.chapterID && $0.start == part.start && $0.end == part.end
        }
    }

    private static func passage(_ scalars: [Unicode.Scalar], start: Int, end: Int) -> String? {
        guard start >= 0, end > start, end <= scalars.count else { return nil }
        return String(String.UnicodeScalarView(scalars[start..<end]))
    }
}

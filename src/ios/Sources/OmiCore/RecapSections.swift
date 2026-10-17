import Foundation

/// Presentation text used to identify sections within one immutable recap version.
public enum RecapSections {
    public struct Topic: Equatable {
        public let title: String?
        public let text: String
        public var copyText: String {
            [title, text].compactMap { $0 }.joined(separator: "\n\n")
                .replacingOccurrences(of: "**", with: "")
        }
    }

    public static func topics(_ markdown: String) -> [Topic] {
        var result: [Topic] = []
        var title: String?
        var lines: [String] = []
        func append() {
            let body = lines.joined(separator: "\n").replacingOccurrences(
                of: #"(?i)\[[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\]"#,
                with: "", options: .regularExpression
            ).replacingOccurrences(of: #" +([.,;!?])"#, with: "$1", options: .regularExpression)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !body.isEmpty { result.append(Topic(title: title, text: body)) }
            lines = []
        }
        for raw in markdown.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            let heading = captures(#"^#{1,4}\s+(.+)$"#, line)?[0]
                ?? captures(#"^\*\*([^*]+)\*\*:?$"#, line)?[0]
            if let heading { append(); title = heading.replacingOccurrences(of: "**", with: "") }
            else { lines.append(raw) }
        }
        append()
        return result
    }

    public static func texts(_ markdown: String) -> [String] {
        RecapPresentation.parts(markdown).map(\.text)
    }

    private static func captures(_ pattern: String, _ text: String) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else { return nil }
        return (1..<match.numberOfRanges).map { index in
            Range(match.range(at: index), in: text).map { String(text[$0]) } ?? ""
        }
    }
}

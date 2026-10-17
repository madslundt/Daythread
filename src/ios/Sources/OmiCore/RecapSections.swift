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
        var result: [String] = []
        var lastWasItem = false
        let actions = #"(?i)^(?:to follow[ -]?up|follow[ -]?ups?|action (?:items?|points?)|next steps?|to[ -]?dos?|tasks?)$"#
        for raw in markdown.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty else { continue }
            let plain = line.replacingOccurrences(of: "**", with: "")
            let heading = captures(#"^#{1,4}\s+(.+)$"#, line)
            let label = captures(#"^([^:]+):\s*(.*)$"#, plain)
            if heading != nil || label.map({ matches(actions, $0[0].trimmingCharacters(in: .whitespaces)) }) == true || matches(actions, plain.replacingOccurrences(of: #":$"#, with: "", options: .regularExpression)) {
                let title = heading?[0] ?? label?[0] ?? plain.replacingOccurrences(of: #":$"#, with: "", options: .regularExpression)
                result.append(title)
                if matches(actions, title.replacingOccurrences(of: "**", with: "").replacingOccurrences(of: #":$"#, with: "", options: .regularExpression).trimmingCharacters(in: .whitespaces)), let body = label?[1], !body.isEmpty {
                    let cleaned = body.replacingOccurrences(of: #"(?i)\s+Completion is not recorded\.?$"#, with: "", options: .regularExpression)
                    let separator = #"(?i)(?:;\s*|,\s*(?:and\s+)?)(?=(?:share|call|bring|confirm|send|book|check|prepare|schedule|review|ask|buy|pick up|follow up)\b)"#
                    let pattern = try! NSRegularExpression(pattern: separator)
                    let ranges = pattern.matches(in: cleaned, range: NSRange(cleaned.startIndex..., in: cleaned))
                    var start = cleaned.startIndex
                    for match in ranges {
                        guard let range = Range(match.range, in: cleaned) else { continue }
                        if start < range.lowerBound { result.append(String(cleaned[start..<range.lowerBound])) }
                        start = range.upperBound
                    }
                    if start < cleaned.endIndex { result.append(String(cleaned[start...])) }
                }
                lastWasItem = false
            } else if let item = captures(#"^(?:[-*+]\s+|\d+[.)]\s+)(?:\[[ xX]\]\s+)?(.+)$"#, line) {
                result.append(item[0]); lastWasItem = true
            } else if raw.first?.isWhitespace == true, lastWasItem, !result.isEmpty {
                result[result.count - 1] += " " + line
            } else {
                result.append(line); lastWasItem = false
            }
        }
        return result
    }

    private static func matches(_ pattern: String, _ text: String) -> Bool { captures(pattern, text) != nil }
    private static func captures(_ pattern: String, _ text: String) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else { return nil }
        return (1..<match.numberOfRanges).map { index in
            Range(match.range(at: index), in: text).map { String(text[$0]) } ?? ""
        }
    }
}

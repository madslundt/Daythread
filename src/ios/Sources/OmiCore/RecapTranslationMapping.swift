import Foundation

/// Translation supplies display text; canonical topics continue to own identity and evidence.
public enum RecapTranslationMapping {
    public struct Structure: Codable, Equatable {
        public let version: Int
        public let summary_version: String
        public let blocks: [Block]
        public init(version: Int = 1, summary_version: String, blocks: [Block]) {
            self.version = version; self.summary_version = summary_version; self.blocks = blocks
        }
    }
    public struct Block: Codable, Equatable {
        public let index: Int
        public let kind: String
        public let start_offset: Int
        public let end_offset: Int
        public let original_text: String
        public let text: String
        public init(index: Int, kind: String, start_offset: Int, end_offset: Int, original_text: String, text: String) {
            self.index = index; self.kind = kind; self.start_offset = start_offset; self.end_offset = end_offset
            self.original_text = original_text; self.text = text
        }
    }
    public struct Topic: Equatable, Identifiable {
        public let id: String
        public let original: RecapPresentation.Group
        public let display: RecapPresentation.Group
    }
    public struct Mapping: Equatable {
        public let topics: [Topic]
        public let followUps: [RecapPresentation.Group]
    }

    /// Recap links retain canonical titles even when the displayed topic is translated.
    public static func topicID(for title: String, in mapping: Mapping) -> String? {
        mapping.topics.first {
            $0.original.title?.replacingOccurrences(of: "**", with: "") == title
        }?.id
    }

    public static func original(_ markdown: String, day: String) -> Mapping {
        .init(topics: RecapPresentation.topics(markdown, day: day).enumerated().map {
            .init(id: "recap:\($0.offset)", original: $0.element, display: $0.element)
        }, followUps: RecapPresentation.groups(markdown, day: day).filter(\.hasActions))
    }

    public static func map(original markdown: String, translated: String, structure: Structure,
                           summaryVersion: String, day: String) -> Mapping? {
        guard structure.version == 1, !summaryVersion.isEmpty, structure.summary_version == summaryVersion,
              validBlocks(markdown, translated: translated, blocks: structure.blocks) else { return nil }
        let originals = RecapPresentation.groups(markdown, day: day)
        let displays = RecapPresentation.groups(groupingProjection(markdown, blocks: structure.blocks), day: day)
        guard originals.count == displays.count else { return nil }
        var topics: [Topic] = [], followUps: [RecapPresentation.Group] = []
        for (original, display) in zip(originals, displays) {
            if original.hasActions { followUps.append(display); continue }
            guard original.parts.count == display.parts.count,
                  zip(original.parts, display.parts).allSatisfy({ $0.0.kind == $0.1.kind }) else { return nil }
            var start = 0, title = original.title, translatedTitle = display.title
            func append(_ end: Int) {
                guard end > start else { return }
                topics.append(.init(id: "recap:\(topics.count)",
                    original: .init(title: title, parts: Array(original.parts[start..<end])),
                    display: .init(title: translatedTitle, parts: Array(display.parts[start..<end]))))
            }
            for index in original.parts.indices {
                if let label = capture(#"^\*\*([^*]+)\*\*"#, original.parts[index].text)?.first {
                    append(index)
                    guard let translatedLabel = capture(#"^\*\*([^*]+)\*\*"#, display.parts[index].text)?.first else { return nil }
                    start = index; title = label; translatedTitle = translatedLabel
                }
            }
            append(original.parts.count)
        }
        guard topics.map(\.original) == RecapPresentation.topics(markdown, day: day) else { return nil }
        return .init(topics: topics, followUps: followUps)
    }

    private struct Line { let start: Int; let end: Int; let text: String }
    private static func groupingProjection(_ original: String, blocks: [Block]) -> String {
        let scalars = Array(original.unicodeScalars)
        var result = "", offset = 0
        for block in blocks {
            var text = block.text
            let source = RecapPresentation.parts(block.original_text)
            let probe = RecapPresentation.parts(block.original_text + "\n- _")
            if source.first?.kind == .heading, probe.last?.action == true,
               RecapPresentation.groups(block.text + "\n- _", day: "").first?.title == nil {
                // Plain legacy action labels are classified from the original language.
                // This internal heading owns grouping; only the translated body is shown.
                text = "## Follow-ups" + (source.count > 1 ? "\n" + text : "")
            }
            result += String(String.UnicodeScalarView(scalars[offset..<block.start_offset])) + text
            offset = block.end_offset
        }
        return result + String(String.UnicodeScalarView(scalars[offset...]))
    }

    private static func lines(_ value: String) -> [Line] {
        let scalars = Array(value.unicodeScalars)
        var result: [Line] = [], start = 0
        for index in 0...scalars.count where index == scalars.count || scalars[index].value == 10 {
            var end = index
            if end > start && scalars[end - 1].value == 13 { end -= 1 }
            let text = String(String.UnicodeScalarView(scalars[start..<end]))
            if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { result.append(.init(start: start, end: end, text: text)) }
            start = index + 1
        }
        return result
    }

    private static func validBlocks(_ original: String, translated: String, blocks: [Block]) -> Bool {
        let source = lines(original), scalars = Array(original.unicodeScalars)
        guard !source.isEmpty, source.count == blocks.count else { return false }
        var rebuilt = "", offset = 0
        for (index, pair) in zip(source, blocks).enumerated() {
            let (line, block) = pair
            guard block.index == index, block.start_offset == line.start, block.end_offset == line.end,
                  block.original_text == line.text, !block.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  !block.text.contains("\n"), !block.text.contains("\r"),
                  let before = skeleton(line.text), let after = skeleton(block.text),
                  block.kind == before.kind, before.kind == after.kind, before.prefix == after.prefix,
                  before.suffix == after.suffix, before.label == after.label,
                  protected(line.text) == protected(block.text) else { return false }
            rebuilt += String(String.UnicodeScalarView(scalars[offset..<line.start])) + block.text
            offset = line.end
        }
        rebuilt += String(String.UnicodeScalarView(scalars[offset...]))
        return rebuilt == translated
    }

    private struct Skeleton { let kind: String; let prefix: String; let suffix: String; let label: String? }
    private static func skeleton(_ line: String) -> Skeleton? {
        var kind = "text", prefix = "", suffix = "", body = line
        if let heading = capture(#"^(\s*#{1,6}\s+)(.*)$"#, line) {
            kind = "heading"; prefix = heading[0]; body = heading[1]
        } else if let heading = capture(#"^(\s*\*\*)(.+?)(\*\*:?[ \t]*)$"#, line) {
            kind = "heading"; prefix = heading[0]; body = heading[1]; suffix = heading[2]
        } else if let item = capture(#"^(\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)$"#, line) {
            kind = "item"; prefix = item[0]; body = item[1]
        }
        let label = capture(#"^\*\*[^*]+\*\*(\s*(?:[—–:-]\s*)?)(.*)$"#, body)?.first
        return .init(kind: kind, prefix: prefix, suffix: suffix, label: label)
    }

    private static func protected(_ text: String) -> [String] {
        let uuid = #"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"#
        let pattern = #"!?\[[^\]\n]*\]\([^\)\n]+\)|\[\s*(?:source:)?"# + uuid + #"(?:\s*,\s*"# + uuid + #")*\s*\]|https?://[^\s<>]+|\b"# + uuid + #"\b"#
        guard let regex = try? NSRegularExpression(pattern: pattern, options: .caseInsensitive) else { return [] }
        return regex.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap {
            guard let range = Range($0.range, in: text) else { return nil }
            let value = String(text[range])
            if let link = capture(#"^(!?)\[[^\]\n]*\](\([^\)\n]+\))$"#, value) { return link.joined() }
            return value
        }
    }
    private static func capture(_ pattern: String, _ text: String) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else { return nil }
        return (1..<match.numberOfRanges).map { Range(match.range(at: $0), in: text).map { String(text[$0]) } ?? "" }
    }
}

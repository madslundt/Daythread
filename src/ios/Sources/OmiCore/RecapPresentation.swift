import Foundation

/// Presentation and evidence matching mirror the web recap without rewriting saved keys.
public enum RecapPresentation {
    public struct Part: Equatable {
        public enum Kind { case heading, text, item }
        public let kind: Kind
        public var text: String
        public let action: Bool
        public init(kind: Kind, text: String, action: Bool = false) {
            self.kind = kind; self.text = text; self.action = action
        }
    }
    public struct Group: Equatable {
        public let title: String?
        public var parts: [Part]
        public var text: String {
            var seen: Set<String> = []
            return parts.filter { $0.kind != .heading }.map { readingText($0.text) }.filter {
                let key = $0.replacingOccurrences(of: "**", with: "")
                    .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression).lowercased()
                return !key.isEmpty && seen.insert(key).inserted
            }.joined(separator: "\n\n")
        }
        public var hasActions: Bool { parts.contains { $0.action } }
    }
    public struct Source {
        public let id: String
        public let text: String
        public init(id: String, text: String) { self.id = id; self.text = text }
    }
    public struct Section: Equatable {
        public let title: String
        public let start: Int
        public let end: Int
        public init(title: String, start: Int, end: Int) { self.title = title; self.start = start; self.end = end }
    }
    public enum Basis { case citation, subject, words, unlinked }
    public struct Matches {
        public let ids: [String]
        public let basis: Basis
    }

    private static let uuid = #"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"#
    private static let actionHeading = #"(?i)^(?:to follow[ -]?up|follow[ -]?ups?|action (?:items?|points?)|next steps?|to[ -]?dos?|tasks?)$"#
    private static let common = Set("the and for with from that this these those about into before after next last then than will would could should have has had was were are our your their they them you its not but also some more what when where which how who been being day today tomorrow yesterday week month year time plans plan planned planning review remember recorded recording recordings discussion conversation follow share call bring confirm send book check prepare schedule ask need needs agreed suggested monday tuesday wednesday thursday friday saturday sunday".split(separator: " ").map(String.init))
    private static let danish = Set("det den der dette denne disse som til med om eller og en et kan vil har var være bliver blev bruger brugeren taler nævnte nævnes beskriver beskrevet samt her også deres vores min mit mine man jeg mig dig han hun dem sin sit sine sig skal skulle kunne have havde ikke fra ved for på af at ind ud dag dagen andet første anden meget noget nogle alle hver hvad hvordan hvor hvorfor hvis når fordi optagelse optagelsen samtalen emne emnet diskussion discussion details mentioned said".split(separator: " ").map(String.init))

    public static func readingText(_ text: String) -> String {
        let citation = "\\[\\s*" + uuid + "(?:\\s*,\\s*" + uuid + ")*\\s*\\]"
        return text.replacingOccurrences(of: citation, with: "", options: [.regularExpression, .caseInsensitive])
            .replacingOccurrences(of: #" +([.,;!?])"#, with: "$1", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static func parts(_ markdown: String) -> [Part] {
        var result: [Part] = [], actions = false
        for raw in markdown.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty else { continue }
            let plain = line.replacingOccurrences(of: "**", with: "")
            let heading = captures(#"^#{1,4}\s+(.+)$"#, line)?.first
            let label = captures(#"^([^:]+):\s*(.*)$"#, plain)
            let bare = plain.replacingOccurrences(of: #":$"#, with: "", options: .regularExpression)
            if heading != nil || label.map({ matches(actionHeading, $0[0].trimmingCharacters(in: .whitespaces)) }) == true || matches(actionHeading, bare) {
                let title = heading ?? label?.first ?? bare
                actions = matches(actionHeading, title.replacingOccurrences(of: "**", with: "").replacingOccurrences(of: #":$"#, with: "", options: .regularExpression).trimmingCharacters(in: .whitespaces))
                result.append(Part(kind: .heading, text: title))
                if actions, let body = label?[1], !body.isEmpty {
                    let clean = body.replacingOccurrences(of: #"(?i)\s+Completion is not recorded\.?$"#, with: "", options: .regularExpression)
                    let separator = #"(?i)(?:;\s*|,\s*(?:and\s+)?)(?=(?:share|call|bring|confirm|send|book|check|prepare|schedule|review|ask|buy|pick up|follow up)\b)"#
                    for item in split(clean, pattern: separator) where !item.isEmpty { result.append(Part(kind: .item, text: item, action: true)) }
                }
            } else if let bullet = captures(#"^(?:[-*+]\s+|\d+[.)]\s+)(?:(\[[ xX]\])\s+)?(.+)$"#, line) {
                result.append(Part(kind: .item, text: bullet[1], action: actions || !bullet[0].isEmpty))
            } else if raw.first?.isWhitespace == true, result.last?.kind == .item {
                result[result.count - 1].text += " " + line
            } else { result.append(Part(kind: .text, text: line)) }
        }
        return result
    }

    public static func groups(_ markdown: String, day: String) -> [Group] {
        var result: [Group] = []
        for part in parts(markdown) {
            if part.kind == .heading || (part.kind == .text && matches(#"^\*\*[^*]+\*\*:?$"#, part.text)) {
                let title = part.text.replacingOccurrences(of: "**", with: "").trimmingCharacters(in: .whitespaces)
                if result.isEmpty && matches(#"(?i)^(?:diary recap|day recap|daily recap|recap)\s*[:·—-]?\s*"# + NSRegularExpression.escapedPattern(for: day) + "$", title) { continue }
                result.append(Group(title: part.text, parts: []))
            } else {
                if result.isEmpty { result.append(Group(title: nil, parts: [])) }
                result[result.count - 1].parts.append(part)
            }
        }
        return result.filter { !$0.parts.isEmpty }
    }

    /// Preserve citations while separating labelled subjects inside broad recap headings.
    /// Action lists remain readable in the day overview; Inbox owns their review/status.
    public static func topics(_ markdown: String, day: String, query: String = "") -> [Group] {
        let result = groups(markdown, day: day).filter { !$0.hasActions }.flatMap { group -> [Group] in
            let labelled = group.parts.compactMap { part -> (String, Part)? in
                guard let title = captures(#"^\*\*([^*]+)\*\*\s*(?:[—–:]\s*)?"#, part.text)?.first else { return nil }
                return (title, part)
            }
            guard !labelled.isEmpty else { return [group] }
            var topics: [Group] = []
            for part in group.parts {
                if let title = labelled.first(where: { $0.1 == part })?.0 {
                    topics.append(Group(title: title, parts: [part]))
                } else if !topics.isEmpty { topics[topics.count - 1].parts.append(part) }
                else { topics.append(Group(title: group.title, parts: [part])) }
            }
            return topics
        }
        let search = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !search.isEmpty else { return result }
        return result.filter {
            (($0.title ?? "") + " " + $0.text).range(of: search, options: [.caseInsensitive, .diacriticInsensitive]) != nil
        }
    }

    public static func sources(for text: String, in sources: [Source]) -> Matches {
        let ids = Set(allMatches("(?i)\\b" + uuid + "\\b", text).map { $0.lowercased() })
        if !ids.isEmpty { return Matches(ids: sources.filter { ids.contains($0.id.lowercased()) }.map(\.id), basis: .citation) }
        let label = captures(#"^\*\*([^*]+)\*\*"#, text)?.first ?? captures(#"^([^:—–]+)[—–:]\s+"#, text)?.first ?? ""
        let subject = words(label.replacingOccurrences(of: #"(?:[·|]\s*)?\b\d{1,2}:\d{2}\b.*$"#, with: "", options: .regularExpression))
        let candidates = sources.map { (source: $0, words: words($0.text)) }
        if subject.contains(where: { !common.contains($0) && $0.count >= 3 }) {
            let related = candidates.filter { phrase($0.words, contains: subject) }.map { $0.source.id }
            if !related.isEmpty { return Matches(ids: related, basis: .subject) }
        }
        let pointWords = words(text)
        let keywords = Set(pointWords.filter { $0.count >= 3 && !common.contains($0) && !matches(#"^\d+$"#, $0) })
        let related = candidates.filter {
            (!keywords.isEmpty && pointWords.count >= 2 && phrase($0.words, contains: pointWords)) || Set($0.words).intersection(keywords).count >= 2
        }.map { $0.source.id }
        return Matches(ids: related, basis: related.isEmpty ? .unlinked : .words)
    }

    public static func sections(for point: String, topic: String = "", text: String, sections: [Section]) -> [Section] {
        let characters = Array(text.unicodeScalars)
        let candidates = sections.filter { $0.start >= 0 && $0.end > $0.start && $0.end <= characters.count }.map {
            (section: $0, text: String(String.UnicodeScalarView(characters[$0.start..<$0.end])))
        }
        let lines = readingText(point).components(separatedBy: .newlines).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        let subject = topic.isEmpty ? (lines.count > 1 ? lines[0] : "") : topic
        let subjectWords = words(subject), keywords = Set(passageWords(subject))
        if !subjectWords.isEmpty && !keywords.isEmpty {
            let named = candidates.filter { phrase(words($0.section.title), contains: subjectWords) }
            if !named.isEmpty { return named.map(\.section) }
            let mentioned = candidates.filter { phrase(words($0.text), contains: subjectWords) }
            if !mentioned.isEmpty { return mentioned.map(\.section) }
            let scored = candidates.map { (section: $0.section, score: Set(passageWords($0.section.title)).intersection(keywords).count) }
            if let best = scored.map(\.score).max(), best > 0 { return scored.filter { $0.score == best }.map(\.section) }
        }
        let terms = Set(passageWords(readingText(point)))
        let scored = candidates.map { (section: $0.section, score: Set(passageWords($0.section.title + " " + $0.text)).intersection(terms).count) }
        guard let best = scored.map(\.score).max(), best >= 2 else { return [] }
        let selected = scored.filter { $0.score == best }
        return selected.count == 1 ? selected.map(\.section) : []
    }

    private static func words(_ value: String) -> [String] {
        allMatches(#"[\p{L}\p{N}]+"#, value.decomposedStringWithCompatibilityMapping
            .replacingOccurrences(of: #"\p{M}"#, with: "", options: .regularExpression).lowercased())
    }
    private static func passageWords(_ value: String) -> [String] {
        words(value).filter { $0.count >= 3 && !common.contains($0) && !danish.contains($0) && !matches(#"^\d+$"#, $0) }
    }
    private static func phrase(_ haystack: [String], contains needle: [String]) -> Bool {
        (" " + haystack.joined(separator: " ") + " ").contains(" " + needle.joined(separator: " ") + " ")
    }
    private static func matches(_ pattern: String, _ text: String) -> Bool { captures(pattern, text) != nil }
    private static func captures(_ pattern: String, _ text: String) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern), let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else { return nil }
        return (1..<match.numberOfRanges).map { Range(match.range(at: $0), in: text).map { String(text[$0]) } ?? "" }
    }
    private static func allMatches(_ pattern: String, _ text: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        return regex.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap { Range($0.range, in: text).map { String(text[$0]) } }
    }
    private static func split(_ text: String, pattern: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [text] }
        var result: [String] = [], start = text.startIndex
        for match in regex.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let range = Range(match.range, in: text) else { continue }
            result.append(String(text[start..<range.lowerBound])); start = range.upperBound
        }
        result.append(String(text[start...])); return result
    }
}

/// Optional recap checks are local, explicitly set by the user and isolated by archive and version.
public struct RecapCheckStore {
    private let defaults: UserDefaults
    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }
    public func checks(archive: String, day: String, version: String) -> Set<String> {
        Set(defaults.stringArray(forKey: key(archive, day, version)) ?? [])
    }
    public func save(_ checks: Set<String>, archive: String, day: String, version: String) {
        defaults.set(checks.sorted(), forKey: key(archive, day, version))
    }
    private func key(_ archive: String, _ day: String, _ version: String) -> String {
        let identity = try! JSONEncoder().encode([archive, day, version])
        return "omi.recap-checks.v1:" + identity.base64EncodedString()
    }
}

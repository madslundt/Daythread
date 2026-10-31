import Foundation

extension CompletionNotification {
    public struct Presentation: Equatable {
        public let title: String
        public let body: String
    }

    /// Only the count line is interface copy. Question and answer text remain literal.
    public func presentation(translate: (String) -> String = AppLocalization.key) -> Presentation {
        if let count = questionCountLine {
            let noun = count.value == 1 ? "question" : "questions"
            return Presentation(title: count.value > 0 ? title : translate(title),
                                body: translate("\(count.value) \(noun)") + body[count.newline...])
        }
        let answerTitle = kind == "wearable_action" && target.action == "answer"
        return Presentation(title: answerTitle ? title : translate(title), body: answerTitle ? body : translate(body))
    }

    private var questionCountLine: (value: Int, newline: String.Index)? {
        guard kind == "wearable_action", target.action == nil || target.action == "answer",
              let newline = body.firstIndex(of: "\n") else { return nil }
        let parts = body[..<newline].split(separator: " ", omittingEmptySubsequences: false)
        let nouns: Set<String> = ["question", "questions", "spørgsmål", "frage", "fragen",
                                     "pregunta", "preguntas", "spørsmål", "fråga", "frågor"]
        guard parts.count == 2, !parts[0].isEmpty,
              parts[0].allSatisfy({ $0 >= "0" && $0 <= "9" }), nouns.contains(parts[1].lowercased()),
              let count = Int(parts[0]), (0...1000).contains(count),
              target.questionCount == nil || target.questionCount == count else { return nil }
        return (count, newline)
    }
}

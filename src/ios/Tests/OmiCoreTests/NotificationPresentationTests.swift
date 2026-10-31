import XCTest
@testable import OmiCore

final class NotificationPresentationTests: XCTestCase {
    private let danish = ["{value0} question": "{value0} spørgsmål", "{value0} questions": "{value0} spørgsmål",
                          "Summarize recent audio": "Opsummér den seneste lyd"]

    func testConfiguredDanishOverridesEnglishDeviceForCountOnly() throws {
        let language = AppLocalization.resolveLanguage(preference: "da", preferredLanguages: ["en-US"])
        XCTAssertEqual(language, "da")
        let answer = "  Æble + 2 = 3.\nKeep {value0}, punctuation & spaces.  "
        let event = try notification(body: "3 questions\n" + answer)
        let copy = event.presentation { AppLocalization.resolve($0, translations: danish) }
        XCTAssertEqual(copy.title, event.title, "A question matching an interface key must remain literal")
        XCTAssertEqual(copy.body, "3 spørgsmål\n" + answer)
        XCTAssertEqual(Array(copy.body.dropFirst("3 spørgsmål\n".count).utf8), Array(answer.utf8))
    }

    func testInFlightLocalizedCountSwitchesBackToEnglishWithoutChangingAnswer() throws {
        for prefix in ["1 spørgsmål", "1 Frage", "1 pregunta", "1 question", "1 spørsmål", "1 fråga"] {
            let event = try notification(body: prefix + "\nAnswer\n  stays verbatim.  ", count: 1)
            let copy = event.presentation { $0 }
            XCTAssertEqual(copy.body, "1 question\nAnswer\n  stays verbatim.  ")
            XCTAssertEqual(copy.title, event.title)
        }
        let plural = try notification(body: "3 Fragen\nAnswer", count: 3).presentation { $0 }
        XCTAssertEqual(plural.body, "3 questions\nAnswer")
    }

    func testLegacyCanonicalEventStillLocalizesWithoutNewMetadata() throws {
        let event = try notification(body: "3 questions\nLiteral answer", metadata: false)
        XCTAssertEqual(event.presentation { AppLocalization.resolve($0, translations: danish) }.body, "3 spørgsmål\nLiteral answer")
    }

    func testOtherActivitiesAndMalformedCountsDoNotRewriteModelText() throws {
        let summary = try notification(body: "3 questions\nLiteral summary", action: "summarize")
        XCTAssertEqual(summary.presentation { AppLocalization.resolve($0, translations: danish) }.body, summary.body)
        for body in ["There are 3 questions\nAnswer", "1001 questions\nAnswer", "3 questions quoted in an answer", "3 questions\nAnswer"] {
            let event = try notification(body: body, count: 2)
            XCTAssertEqual(event.presentation { "translated " + $0 }.body, body)
        }
    }

    private func notification(body: String, count: Int = 3, action: String = "answer", metadata: Bool = true) throws -> CompletionNotification {
        var target: [String: Any] = ["job_id": UUID().uuidString]
        if metadata { target["action"] = action; target["question_count"] = count }
        let object: [String: Any] = ["id": UUID().uuidString, "sequence": 1, "kind": "wearable_action",
            "title": "Summarize recent audio", "body": body, "target": target, "created_at": "2026-10-31T20:00:00Z"]
        return try JSONDecoder().decode(CompletionNotification.self, from: JSONSerialization.data(withJSONObject: object))
    }
}

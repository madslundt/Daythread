import XCTest
@testable import OmiCore

final class InboxModelsTests: XCTestCase {
    func testUnknownOwnerAndDeadlinePrecisionSurviveDecoding() throws {
        let json = #"{"id":"item","kind":"commitment","title":"Send résumé","owner":null,"deadline":{"precision":"date","day":"2026-10-20","instant":null,"timezone":"Europe/Copenhagen","phrase":"tomorrow","basis":"capture date"},"status":"pending","health":"needs_review","version":3,"reviewed_at":null,"duplicate_of":null,"proposal":{"kind":"commitment","title":"Send résumé","owner":null,"deadline":null},"evidence":[{"source_id":"source","revision":"r2","start":3,"end":9,"quote":"résumé","started_at":"2026-10-19T12:00:00Z","audio_available":false,"current":false}],"history":[]}"#
        let item = try JSONDecoder().decode(InboxItem.self, from: Data(json.utf8))
        XCTAssertNil(item.owner)
        XCTAssertNil(item.deadline?.instant)
        XCTAssertEqual(item.deadline?.label, "2026-10-20")
        XCTAssertEqual(item.evidence?.first?.quote, "résumé")
        XCTAssertEqual(item.evidence?.first?.audio_available, false)
        XCTAssertEqual(item.evidence?.first?.current, false)
        XCTAssertEqual(try JSONDecoder().decode(InboxItem.self, from: JSONEncoder().encode(item)), item)
    }
    func testUnresolvedDeadlineIsNotPresentedAsADate() throws {
        let json = #"{"precision":"unknown","day":null,"instant":null,"timezone":"Europe/Copenhagen","phrase":"next Friday","basis":null}"#
        let deadline = try JSONDecoder().decode(InboxDeadline.self, from: Data(json.utf8))
        XCTAssertEqual(deadline.label, "Unresolved: next Friday")
        XCTAssertNil(deadline.day)
    }

    func testTopicActionPointsRequireCurrentPassageContainmentWithinSharedRecording() throws {
        let json = #"{"id":"item","kind":"commitment","title":"Book a service","owner":null,"deadline":null,"status":"pending","health":"current","version":3,"proposal":{"kind":"commitment","title":"Book a service","owner":null,"deadline":null},"source_ids":["shared","second"],"evidence_spans":[{"source_id":"shared","start":60,"end":90,"current":true},{"source_id":"second","start":5,"end":20,"current":false}]}"#
        let item = try JSONDecoder().decode(InboxItem.self, from: Data(json.utf8))
        XCTAssertTrue(item.canSuggestFollowUps)
        XCTAssertTrue(item.matchesSources(["second"]))
        XCTAssertTrue(item.matchesTopic(sources: ["shared"], passages: [.init(sourceID: "shared", start: 50, end: 100)]))
        XCTAssertFalse(item.matchesTopic(sources: ["shared"], passages: [.init(sourceID: "shared", start: 0, end: 50)]))
        XCTAssertFalse(item.matchesTopic(sources: ["shared"], passages: [.init(sourceID: "shared", start: 90, end: 100)]))
        XCTAssertFalse(item.matchesTopic(sources: ["second"], passages: [.init(sourceID: "second", start: 0, end: 30)]))
        XCTAssertFalse(item.matchesTopic(sources: ["shared"], passages: [.init(sourceID: "shared", start: 70, end: 100)]))
        XCTAssertFalse(item.matchesTopic(sources: ["shared"], passages: []))
    }

    func testInboxFollowUpsDecodeExistingNativeSuggestionShape() throws {
        let json = #"{"item_id":"item","item_version":4,"timezone":"Europe/Copenhagen","suggestions":[{"kind":"reminder","title":"Call Sam","notes":"Check the time","reason":"Recorded commitment","all_day":false,"location":""}]}"#
        let response = try JSONDecoder().decode(InboxFollowUps.self, from: Data(json.utf8))
        XCTAssertEqual(response.item_id, "item")
        XCTAssertEqual(response.item_version, 4)
        XCTAssertEqual(response.suggestions.first?.kind, .reminder)
        XCTAssertNil(response.suggestions.first?.actionDate(timeZone: .gmt))
    }

    func testPersonalWritingDoesNotChangeLifecycleOrBecomeExtractedEvidence() throws {
        let intents: [String: InboxIntent] = ["commitment": .todo, "deadline": .todo, "question": .questions, "reminder": .schedule, "event": .schedule, "reflection": .reflection, "note": .information]
        for (kind, intent) in intents {
            let json = """
            {"id":"item","kind":"\(kind)","title":"Recorded point","status":"pending","health":"current","version":4,"user_note":"My own thoughts","user_note_provenance":"user","proposal":{"kind":"\(kind)","title":"Recorded point"}}
            """
            let item = try JSONDecoder().decode(InboxItem.self, from: Data(json.utf8))
            XCTAssertEqual(item.intent, intent)
            XCTAssertEqual(item.status, "pending")
            XCTAssertEqual(item.user_note, "My own thoughts")
            XCTAssertEqual(item.user_note_provenance, "user")
            XCTAssertNil(item.evidence)
            XCTAssertEqual(item.proposal.title, "Recorded point")
            XCTAssertEqual(item.canSuggestFollowUps, kind != "reflection" && kind != "note")
            XCTAssertEqual(try JSONDecoder().decode(InboxItem.self, from: JSONEncoder().encode(item)), item)
        }
    }

    func testTopicDecisionChoiceRequiresCurrentVersionAndContainedChoiceAnchor() {
        let passage = InboxTopicPassage(sourceID: "shared", start: 50, end: 100)
        XCTAssertTrue(passage.containsCurrent(sourceID: "shared", sourceVersion: "v2", currentVersions: ["shared": "v2"], start: 60, end: 90))
        XCTAssertFalse(passage.containsCurrent(sourceID: "shared", sourceVersion: "v1", currentVersions: ["shared": "v2"], start: 60, end: 90))
        XCTAssertFalse(passage.containsCurrent(sourceID: "shared", sourceVersion: "v2", currentVersions: [:], start: 60, end: 90))
        XCTAssertFalse(passage.containsCurrent(sourceID: "shared", sourceVersion: "v2", currentVersions: ["shared": "v2"], start: 30, end: 90))
        XCTAssertFalse(passage.containsCurrent(sourceID: "other", sourceVersion: "v2", currentVersions: ["other": "v2"], start: 60, end: 90))
    }

    func testReviewedInformationRemainsVisibleWithoutReopeningCompletedObligations() throws {
        let cases: [(String, String, String, Bool)] = [
            ("note", "completed", "current", true), ("reflection", "completed", "current", true),
            ("commitment", "completed", "current", false), ("deadline", "completed", "current", false),
            ("question", "completed", "current", false), ("event", "completed", "current", false),
            ("reminder", "completed", "current", false),
            ("note", "dismissed", "current", false), ("reflection", "dismissed", "current", false),
            ("question", "pending", "current", true), ("note", "pending", "current", true),
            ("note", "pending", "withdrawn", false), ("commitment", "pending", "withdrawn", false),
            ("note", "completed", "withdrawn", false), ("note", "completed", "needs_review", true),
            ("commitment", "pending", "needs_review", true)
        ]
        for (kind, status, health, visible) in cases {
            let json = """
            {"id":"item","kind":"\(kind)","title":"Recorded point","status":"\(status)","health":"\(health)","version":4,"proposal":{"kind":"\(kind)","title":"Recorded point"}}
            """
            let item = try JSONDecoder().decode(InboxItem.self, from: Data(json.utf8))
            XCTAssertEqual(item.appearsInDayOverview, visible, "\(kind): \(status), \(health)")
        }
    }
}

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
}

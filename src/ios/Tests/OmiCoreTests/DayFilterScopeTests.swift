import XCTest
@testable import OmiCore

final class DayFilterScopeTests: XCTestCase {
    func testFiltersUseTheSameIntersectionWithoutAddingTranscriptSearch() {
        let scope = DayFilterScope(tagID: "work", zoneID: "office")
        XCTAssertTrue(scope.isFiltered)
        XCTAssertEqual(scope.query, ["tag_id": "work", "zone_id": "office"])
        XCTAssertEqual(DayFilterScope(tagID: "work").query, ["tag_id": "work"])
        XCTAssertEqual(DayFilterScope(zoneID: "office").query, ["zone_id": "office"])
        XCTAssertFalse(DayFilterScope().isFiltered)
        XCTAssertTrue(DayFilterScope().query.isEmpty)
    }

    func testFilteredRecapRejectsWholeDayOrDifferentScopeResponses() {
        let scope = DayFilterScope(tagID: "work", zoneID: "office")
        XCTAssertTrue(scope.matchesResponseScope(["tag_id": "work", "zone_id": "office"]))
        XCTAssertFalse(scope.matchesResponseScope(nil))
        XCTAssertFalse(scope.matchesResponseScope(["tag_id": "work"]))
        XCTAssertFalse(scope.matchesResponseScope(["tag_id": "personal", "zone_id": "office"]))
        XCTAssertFalse(scope.matchesResponseScope(["tag_id": "work", "zone_id": "home"]))
        XCTAssertTrue(DayFilterScope().matchesResponseScope(nil))
        XCTAssertFalse(DayFilterScope().matchesResponseScope(["tag_id": "work"]))
        XCTAssertTrue(DayFilterScope(tagID: "work").matchesResponseScope(["tag_id": "work", "zone_id": ""]))
    }

    func testScopeIdentityDoesNotCollideAcrossFilterKindsOrSeparators() {
        let identities = [DayFilterScope(), .init(tagID: "a"), .init(zoneID: "a"),
                          .init(tagID: "a.b", zoneID: "c"), .init(tagID: "a", zoneID: "b.c")].map(\.identity)
        XCTAssertEqual(Set(identities).count, identities.count)
    }

    func testFilteredTopicSnapshotRequiresTheSameNonemptyMembershipFingerprint() {
        let filters = DayFilterScope(tagID: "work"), scope = ["tag_id": "work"]
        XCTAssertTrue(filters.matchesSnapshot(scope, fingerprint: "selected-originals-v1", expectedFingerprint: "selected-originals-v1"))
        XCTAssertFalse(filters.matchesSnapshot(scope, fingerprint: "selected-originals-v2", expectedFingerprint: "selected-originals-v1"))
        XCTAssertFalse(filters.matchesSnapshot(nil, fingerprint: "selected-originals-v1", expectedFingerprint: "selected-originals-v1"))
        XCTAssertFalse(filters.matchesSnapshot(scope, fingerprint: "", expectedFingerprint: ""))
        XCTAssertTrue(DayFilterScope().matchesSnapshot(nil, fingerprint: "", expectedFingerprint: ""))
    }
}

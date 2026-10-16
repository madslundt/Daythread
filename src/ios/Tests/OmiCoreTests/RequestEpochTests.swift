import XCTest
@testable import OmiCore

final class RequestEpochTests: XCTestCase {
    func testCancelledReadCannotDiscardReplacementOrClearItsLoadingState() {
        var epoch = RequestEpoch()
        let old = epoch.replace()
        let replacement = epoch.replace()
        XCTAssertFalse(epoch.accepts(old))
        XCTAssertTrue(epoch.accepts(replacement))
    }
    func testDeletionRefreshRejectsPriorPageResult() {
        var epoch = RequestEpoch()
        let page = epoch.replace()
        epoch.replace()
        XCTAssertFalse(epoch.accepts(page))
    }
}

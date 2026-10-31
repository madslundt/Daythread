import XCTest
@testable import OmiCore

final class DiscoveryRecoveryTests: XCTestCase {
    func testConnectedButUnreadySetupRetriesThreeTimesThenStops() {
        var recovery = OmiDiscoveryRecovery()
        recovery.start(at: 100)
        XCTAssertEqual(recovery.attempts, 1)
        XCTAssertEqual(recovery.advance(at: 102.9, ready: false), .wait)
        XCTAssertEqual(recovery.advance(at: 103, ready: false), .retry)
        XCTAssertEqual(recovery.attempts, 2)
        XCTAssertEqual(recovery.advance(at: 106, ready: false), .retry)
        XCTAssertEqual(recovery.attempts, 3)
        XCTAssertEqual(recovery.advance(at: 109, ready: false), .exhausted)
        XCTAssertNil(recovery.retryAt)
        XCTAssertEqual(recovery.advance(at: 1000, ready: false), .wait)
    }
    func testReadinessAndDisconnectCancelDeadlinesWithoutAnotherRetry() {
        var recovery = OmiDiscoveryRecovery()
        recovery.start(at: 10)
        XCTAssertEqual(recovery.advance(at: 13, ready: true), .wait)
        XCTAssertNil(recovery.retryAt)
        recovery.start(at: 20)
        recovery.cancel()
        XCTAssertEqual(recovery.advance(at: 23, ready: false), .wait)
        XCTAssertNil(recovery.retryAt)
        recovery.start(at: 30)
        XCTAssertEqual(recovery.attempts, 1)
        XCTAssertEqual(recovery.retryAt, 33)
    }
}

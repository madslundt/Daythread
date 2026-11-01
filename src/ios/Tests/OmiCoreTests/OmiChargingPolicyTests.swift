import XCTest
@testable import OmiCore

final class OmiChargingPolicyTests: XCTestCase {
    func testDisabledByDefaultAndRejectsMalformedStatus() {
        var policy = OmiChargingPolicy()
        policy.update(charging: true, autoPause: false, autoResume: false)
        XCTAssertFalse(policy.paused)
        XCTAssertEqual(OmiChargingPolicy.decode(Data([0])), false)
        XCTAssertEqual(OmiChargingPolicy.decode(Data([1])), true)
        for bytes in [Data(), Data([2]), Data([255]), Data([1, 0])] {
            XCTAssertNil(OmiChargingPolicy.decode(bytes))
        }
    }
    func testDockUndockAndReconnectPreserveHoldUntilConfirmedRemoval() {
        var policy = OmiChargingPolicy()
        policy.update(charging: true, autoPause: true, autoResume: true)
        XCTAssertTrue(policy.paused)
        policy.update(charging: nil, autoPause: true, autoResume: true)
        XCTAssertTrue(policy.paused)
        policy = OmiChargingPolicy(paused: policy.paused) // Restored after relaunch.
        policy.update(charging: true, autoPause: true, autoResume: true)
        XCTAssertTrue(policy.paused)
        policy.update(charging: false, autoPause: true, autoResume: true)
        XCTAssertFalse(policy.paused)
    }
    func testResumeOptOutStaysPausedUntilExplicitResume() {
        var policy = OmiChargingPolicy()
        policy.update(charging: true, autoPause: true, autoResume: false)
        policy.update(charging: false, autoPause: true, autoResume: false)
        XCTAssertTrue(policy.paused)
        policy.resumeManually(charging: false, autoPause: true)
        XCTAssertFalse(policy.paused)
        policy.resumeManually(charging: true, autoPause: true)
        XCTAssertTrue(policy.paused)
        policy.update(charging: true, autoPause: false, autoResume: false)
        XCTAssertFalse(policy.paused)
    }
    func testEnablingResumeAfterRemovalReleasesTheHeldRecording() {
        var policy = OmiChargingPolicy()
        policy.update(charging: true, autoPause: true, autoResume: false)
        policy.update(charging: false, autoPause: true, autoResume: false)
        XCTAssertTrue(policy.paused)
        policy.update(charging: false, autoPause: true, autoResume: true)
        XCTAssertFalse(policy.paused)
    }
}

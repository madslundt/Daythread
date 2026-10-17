import XCTest
@testable import OmiCore

final class RecordingScheduleTests: XCTestCase {
    private var calendar: Calendar { var value = Calendar(identifier: .gregorian); value.timeZone = TimeZone(identifier: "Europe/Copenhagen")!; return value }
    private func date(_ value: String) -> Date { ISO8601DateFormatter().date(from: value)! }

    func testWeekdaysAndExclusiveStop() {
        var policy = RecordingSchedule(); policy.scheduled = true
        XCTAssertFalse(policy.allows(date("2026-10-16T06:59:59Z"), calendar: calendar))
        XCTAssertTrue(policy.allows(date("2026-10-16T07:00:00Z"), calendar: calendar))
        XCTAssertFalse(policy.allows(date("2026-10-16T15:00:00Z"), calendar: calendar))
        XCTAssertFalse(policy.allows(date("2026-10-17T08:00:00Z"), calendar: calendar))
        XCTAssertEqual(policy.recordingDeadline(date("2026-10-16T08:00:00Z"), calendar: calendar), date("2026-10-16T15:00:00Z"))
    }

    func testOvernightUsesTheStartingWeekdayAndWrapsSunday() {
        var policy = RecordingSchedule(); policy.scheduled = true; policy.weekdays = [7]
        policy.startMinute = 22 * 60; policy.endMinute = 6 * 60
        XCTAssertTrue(policy.allows(date("2026-10-18T21:00:00Z"), calendar: calendar))
        XCTAssertTrue(policy.allows(date("2026-10-19T03:00:00Z"), calendar: calendar))
        XCTAssertFalse(policy.allows(date("2026-10-19T04:00:00Z"), calendar: calendar))
        XCTAssertEqual(policy.recordingDeadline(date("2026-10-18T21:00:00Z"), calendar: calendar), date("2026-10-19T04:00:00Z"))
    }

    func testManualOverrideAndDisableWinEvenWithEmptyOrInvalidSchedule() throws {
        var policy = RecordingSchedule(); policy.scheduled = true; policy.weekdays = []
        XCTAssertFalse(policy.allows(Date()))
        policy.manualOverride = true
        XCTAssertTrue(policy.allows(Date()))
        XCTAssertNil(policy.recordingDeadline(Date()))
        policy.enabled = false
        XCTAssertFalse(policy.allows(Date()))
        policy.enabled = true; policy.manualOverride = false; policy.weekdays = [1,2,3,4,5,6,7]; policy.endMinute = policy.startMinute
        XCTAssertFalse(policy.allows(Date()))
        XCTAssertEqual(try JSONDecoder().decode(RecordingSchedule.self, from: JSONEncoder().encode(policy)), policy)
    }

    func testDeadlineAcrossDaylightSavingUsesWallTime() {
        var policy = RecordingSchedule(); policy.scheduled = true; policy.weekdays = [6]; policy.startMinute = 22 * 60; policy.endMinute = 6 * 60
        let start = date("2026-10-24T20:00:00Z")
        XCTAssertEqual(policy.recordingDeadline(start, calendar: calendar), date("2026-10-25T05:00:00Z"))
        XCTAssertTrue(policy.allows(date("2026-10-25T01:30:00Z"), calendar: calendar))
    }

    func testNextTransitionSkipsIdleWeekendAndUsesExclusiveEnd() {
        var policy = RecordingSchedule(); policy.scheduled = true
        XCTAssertEqual(policy.nextTransition(after: date("2026-10-16T14:59:59Z"), calendar: calendar), date("2026-10-16T15:00:00Z"))
        XCTAssertEqual(policy.nextTransition(after: date("2026-10-16T15:00:00Z"), calendar: calendar), date("2026-10-19T07:00:00Z"))
        XCTAssertEqual(policy.nextTransition(after: date("2026-10-19T07:00:00Z"), calendar: calendar), date("2026-10-19T15:00:00Z"))
    }

    func testNoTimerNeededForManualDisabledOrInvalidPolicies() {
        var policy = RecordingSchedule()
        XCTAssertNil(policy.nextTransition(after: Date()))
        policy.scheduled = true; policy.manualOverride = true
        XCTAssertNil(policy.nextTransition(after: Date()))
        policy.manualOverride = false; policy.enabled = false
        XCTAssertNil(policy.nextTransition(after: Date()))
        policy.enabled = true; policy.weekdays = [0, 8]
        XCTAssertNil(policy.nextTransition(after: Date()))
        policy.weekdays = [1]; policy.endMinute = policy.startMinute
        XCTAssertNil(policy.nextTransition(after: Date()))
    }

    func testNextTransitionOvernightAndClockChanges() {
        var policy = RecordingSchedule(); policy.scheduled = true; policy.weekdays = [6]
        policy.startMinute = 22 * 60; policy.endMinute = 6 * 60
        XCTAssertEqual(policy.nextTransition(after: date("2026-10-24T20:00:00Z"), calendar: calendar), date("2026-10-25T05:00:00Z"))
        policy.weekdays = [7]; policy.startMinute = 2 * 60 + 15; policy.endMinute = 2 * 60 + 45
        XCTAssertEqual(policy.nextTransition(after: date("2026-10-25T00:45:00Z"), calendar: calendar), date("2026-10-25T01:15:00Z"))
        XCTAssertEqual(policy.nextTransition(after: date("2026-10-25T01:15:00Z"), calendar: calendar), date("2026-10-25T01:45:00Z"))
        XCTAssertEqual(policy.recordingDeadline(date("2026-10-25T01:15:00Z"), calendar: calendar), date("2026-10-25T01:45:00Z"))
        policy.startMinute = 90; policy.endMinute = 150
        XCTAssertEqual(policy.nextTransition(after: date("2026-10-25T00:45:00Z"), calendar: calendar), date("2026-10-25T01:00:00Z"))
        policy.startMinute = 135; policy.endMinute = 165
        XCTAssertEqual(policy.nextTransition(after: date("2026-03-29T00:30:00Z"), calendar: calendar), date("2026-04-05T00:15:00Z"))
    }

    func testManualInputDoesNotReplaceTheScheduledSource() {
        var policy = RecordingSchedule(); policy.scheduled = true; policy.source = "omi"
        policy.manualOverride = true; policy.manualSource = "phone"
        XCTAssertEqual(policy.activeSource, "phone")
        XCTAssertTrue(policy.allows(date("2026-10-17T20:00:00Z"), calendar: calendar))
        policy.manualOverride = false
        XCTAssertEqual(policy.activeSource, "omi")
        XCTAssertFalse(policy.allows(date("2026-10-17T20:00:00Z"), calendar: calendar))
    }
}

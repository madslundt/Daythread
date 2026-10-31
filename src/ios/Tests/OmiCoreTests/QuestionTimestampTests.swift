import XCTest
@testable import OmiCore

final class QuestionTimestampTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_793_475_000)

    func testRelativeTimesEndAtExactlyOneHour() {
        XCTAssertEqual(QuestionTimestamp.style(for: now, now: now), .relative)
        XCTAssertEqual(QuestionTimestamp.style(for: now.addingTimeInterval(-3599.999), now: now), .relative)
        XCTAssertEqual(QuestionTimestamp.style(for: now.addingTimeInterval(-3600), now: now), .clock)
        XCTAssertEqual(QuestionTimestamp.style(for: now.addingTimeInterval(-7200), now: now), .clock)
    }

    func testRecentLabelIsHumanRelativeAndLocalized() {
        let earlier = now.addingTimeInterval(-300)
        XCTAssertEqual(QuestionTimestamp.label(for: earlier, now: now, locale: Locale(identifier: "en_US")), "5 minutes ago")
        XCTAssertEqual(QuestionTimestamp.label(for: earlier, now: now, locale: Locale(identifier: "da_DK")), "for 5 minutter siden")
    }

    func testOlderClockUsesHomeTimeZoneAcrossDayBoundary() throws {
        let date = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-10-31T23:20:00Z"))
        let later = date.addingTimeInterval(3600)
        let locale = Locale(identifier: "en_GB")
        XCTAssertEqual(QuestionTimestamp.label(for: date, now: later, locale: locale,
                                               timeZone: TimeZone(identifier: "Europe/Copenhagen")!), "00:20")
        XCTAssertEqual(QuestionTimestamp.label(for: date, now: later, locale: locale,
                                               timeZone: TimeZone(identifier: "America/Los_Angeles")!), "16:20")
    }

    func testClockSkewDoesNotTurnOldFutureTimeIntoRelativeLabel() {
        XCTAssertEqual(QuestionTimestamp.style(for: now.addingTimeInterval(30), now: now), .relative)
        XCTAssertEqual(QuestionTimestamp.style(for: now.addingTimeInterval(3600), now: now), .clock)
    }
}

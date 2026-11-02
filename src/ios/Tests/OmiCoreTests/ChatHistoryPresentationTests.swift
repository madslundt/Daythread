import XCTest
@testable import OmiCore

final class ChatHistoryPresentationTests: XCTestCase {
    private let locale = Locale(identifier: "en_US")
    private let timeZone = TimeZone(identifier: "Europe/Copenhagen")!
    private let now = ISO8601DateFormatter().date(from: "2026-11-02T12:00:00Z")!

    func testRecentConversationsShowHumanTimes() {
        XCTAssertEqual(label(now.addingTimeInterval(-10)), "now")
        XCTAssertEqual(label(now.addingTimeInterval(-5 * 60)), "5 minutes ago")
        XCTAssertEqual(label(now.addingTimeInterval(-3 * 60 * 60)), "3 hours ago")
    }

    func testHumanTimesFollowTheInterfaceLocale() {
        let date = now.addingTimeInterval(-5 * 60)
        let danish = ChatHistoryPresentation.timestamp(date, relativeTo: now, locale: Locale(identifier: "da_DK"), timeZone: timeZone)
        XCTAssertTrue(danish.contains("minutter"), danish)
        XCTAssertNotEqual(danish, label(date))
    }

    func testOlderConversationsIncludeDayAndClockTime() throws {
        let yesterday = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-11-01T09:15:00Z"))
        XCTAssertTrue(label(yesterday).contains("yesterday"), label(yesterday))
        XCTAssertTrue(label(yesterday).contains("10:15"), label(yesterday))
        let saturday = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-10-31T09:15:00Z"))
        XCTAssertTrue(label(saturday).contains("Sat"), label(saturday))
        XCTAssertTrue(label(saturday).contains("10:15"), label(saturday))
        let lastYear = try XCTUnwrap(ISO8601DateFormatter().date(from: "2025-11-02T09:15:00Z"))
        XCTAssertTrue(label(lastYear).contains("2025"), label(lastYear))
    }

    func testTitleUsesTheFirstQuestionWhenSavedNameIsMissingOrGeneric() {
        for name in ["", "Conversation", "Chat", "New conversation"] {
            XCTAssertEqual(ChatHistoryPresentation.title(name, firstQuestion: "  What did\nwe decide?  "), "What did we decide?")
        }
        XCTAssertEqual(ChatHistoryPresentation.title("Project planning", firstQuestion: "What did we decide?"), "Project planning")
        XCTAssertEqual(ChatHistoryPresentation.title("", fallback: "Samtale"), "Samtale")
    }

    private func label(_ date: Date) -> String {
        ChatHistoryPresentation.timestamp(date, relativeTo: now, locale: locale, timeZone: timeZone)
    }
}

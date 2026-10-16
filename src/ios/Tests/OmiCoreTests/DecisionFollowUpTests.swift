import Foundation
import XCTest
@testable import OmiCore

final class DecisionFollowUpTests: XCTestCase {
    func testSuggestionDecodesMissingDatesWithoutInventingSchedule() throws {
        let data = Data("""
        {"decision_id":"d1","decision_version":4,"timezone":"Europe/Copenhagen","suggestions":[
          {"kind":"event","title":"Review decision","notes":"","reason":"Follow up","start_at":null,"end_at":null,"due_at":null,"all_day":false,"location":""}
        ]}
        """.utf8)
        let response = try JSONDecoder().decode(DecisionFollowUps.self, from: data)
        let suggestion = try XCTUnwrap(response.suggestions.first)
        XCTAssertNil(DecisionFollowUp.date(suggestion.start_at))
        XCTAssertEqual(response.decision_version, 4)
    }

    func testDatesHonorOffsetAndFractionalSeconds() throws {
        XCTAssertEqual(DecisionFollowUp.date("2026-10-17T10:30:00+02:00"), DecisionFollowUp.date("2026-10-17T08:30:00Z"))
        XCTAssertNotNil(DecisionFollowUp.date("2026-10-17T08:30:00.123456Z"))
        XCTAssertNil(DecisionFollowUp.date("2026-10-17T10:30:00"))
        XCTAssertNil(DecisionFollowUp.date("tomorrow"))
    }

    func testUndatedEventHasNoActionOrComposerDates() {
        let suggestion = DecisionFollowUp(kind: .event, title: "Book a conversation", notes: "", reason: "", start_at: nil, end_at: "2026-10-17T11:30:00+02:00", due_at: "2026-10-17T10:30:00+02:00", all_day: false, location: "")
        XCTAssertNil(suggestion.actionDate(timeZone: .gmt))
        XCTAssertNil(suggestion.calendarComposerDates(timeZone: .gmt))
    }

    func testComposerPreservesKnownDates() throws {
        let suggestion = DecisionFollowUp(kind: .event, title: "Meeting", notes: "", reason: "", start_at: "2026-10-17T10:30:00+02:00", end_at: "2026-10-17T11:30:00+02:00", due_at: nil, all_day: false, location: "")
        let dates = try XCTUnwrap(suggestion.calendarComposerDates(timeZone: .gmt))
        XCTAssertEqual(dates.start, DecisionFollowUp.date(suggestion.start_at))
        XCTAssertEqual(dates.end, DecisionFollowUp.date(suggestion.end_at))
    }

    func testComposerSuppliesMissingEndAfterKnownStart() throws {
        let suggestion = DecisionFollowUp(kind: .event, title: "Meeting", notes: "", reason: "", start_at: "2026-10-17T10:30:00+02:00", end_at: nil, due_at: nil, all_day: false, location: "")
        let dates = try XCTUnwrap(suggestion.calendarComposerDates(timeZone: .gmt))
        XCTAssertEqual(dates.start, DecisionFollowUp.date(suggestion.start_at))
        XCTAssertEqual(dates.end.timeIntervalSince(dates.start), 3600)
        XCTAssertNil(suggestion.end_at)
    }

    func testAllDayComposerDefaultEndSurvivesDaylightSavingChange() throws {
        let zone = try XCTUnwrap(TimeZone(identifier: "Europe/Copenhagen"))
        let suggestion = DecisionFollowUp(kind: .event, title: "Meeting", notes: "", reason: "", start_at: "2026-10-25T00:00:00+02:00", end_at: nil, due_at: nil, all_day: true, location: "")
        let dates = try XCTUnwrap(suggestion.calendarComposerDates(timeZone: zone))
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = zone
        XCTAssertEqual(calendar.component(.day, from: dates.end), 26)
        XCTAssertEqual(calendar.component(.hour, from: dates.end), 0)
        XCTAssertEqual(dates.end.timeIntervalSince(dates.start), 25 * 3600)
    }

    func testAllDayKeepsSourceCalendarDateAcrossOffsets() throws {
        let zone = try XCTUnwrap(TimeZone(identifier: "Europe/Copenhagen"))
        let date = try XCTUnwrap(DecisionFollowUp.date("2026-10-18T00:00:00+14:00", allDay: true, timeZone: zone))
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = zone
        let components = calendar.dateComponents([.year, .month, .day, .hour], from: date)
        XCTAssertEqual(components.year, 2026); XCTAssertEqual(components.month, 10)
        XCTAssertEqual(components.day, 18); XCTAssertEqual(components.hour, 0)
    }

    func testReminderActionUsesOnlyKnownDueDate() {
        let undated = DecisionFollowUp(kind: .reminder, title: "Call", notes: "", reason: "", start_at: "2026-10-17T10:30:00+02:00", end_at: nil, due_at: nil, all_day: false, location: "")
        XCTAssertNil(undated.actionDate(timeZone: .gmt))
        let dated = DecisionFollowUp(kind: .reminder, title: "Call", notes: "", reason: "", start_at: nil, end_at: nil, due_at: "2026-10-17T10:30:00+02:00", all_day: false, location: "")
        XCTAssertEqual(dated.actionDate(timeZone: .gmt), DecisionFollowUp.date(dated.due_at))
        XCTAssertNil(dated.calendarComposerDates(timeZone: .gmt))
    }
}

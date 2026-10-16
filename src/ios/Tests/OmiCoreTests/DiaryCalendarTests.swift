import XCTest
@testable import OmiCore

final class DiaryCalendarTests: XCTestCase {
    private let diary = DiaryCalendar(timeZone: TimeZone(identifier: "Europe/Copenhagen")!, locale: Locale(identifier: "en_DK"))

    func testLeapMonthHasAllDaysAndCompleteWeeks() throws {
        let cells = diary.cells(in: try XCTUnwrap(diary.date(for: "2024-02-15")))
        let days = cells.compactMap { $0 }.map(diary.dayKey)
        XCTAssertEqual(cells.count % 7, 0)
        XCTAssertEqual(days.count, 29)
        XCTAssertEqual(days.first, "2024-02-01")
        XCTAssertEqual(days.last, "2024-02-29")
        let leading = try XCTUnwrap(cells.firstIndex { $0 != nil })
        XCTAssertEqual(leading, (diary.calendar.component(.weekday, from: try XCTUnwrap(cells[leading])) - diary.calendar.firstWeekday + 7) % 7)
    }

    func testMonthNavigationStartsAtFirstAndCrossesYear() throws {
        let december = try XCTUnwrap(diary.date(for: "2025-12-31"))
        XCTAssertEqual(diary.dayKey(diary.month(december, offset: 1)), "2026-01-01")
        let march = try XCTUnwrap(diary.date(for: "2026-03-31"))
        XCTAssertEqual(diary.dayKey(diary.month(march, offset: -1)), "2026-02-01")
    }

    func testHomeTimezoneDefinesDayAndDSTMonthHasEveryDay() throws {
        let instant = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-10-15T23:30:00Z"))
        XCTAssertEqual(diary.dayKey(instant), "2026-10-16")
        let days = diary.cells(in: instant).compactMap { $0 }.map(diary.dayKey)
        XCTAssertEqual(Set(days).count, 31)
        XCTAssertTrue(days.contains("2026-10-25"))
        XCTAssertEqual(diary.dayKey(try XCTUnwrap(diary.date(for: "2026-03-29"))), "2026-03-29")
    }

    func testInvalidOrNoncanonicalDatesAreRejected() {
        for key in ["2026-02-29", "2026-13-01", "2026-2-01", "nonsense", "2026-01-01-extra"] {
            XCTAssertNil(diary.date(for: key), key)
        }
    }

    func testCompletePeriodShortcutsCrossNewYearAndKeepFutureEndDates() throws {
        let firstJanuary = try XCTUnwrap(diary.date(for: "2026-01-01"))
        let expected: [(DiaryPeriod, Bool, String, String)] = [
            (.week, false, "2025-12-29", "2026-01-04"),
            (.week, true, "2025-12-22", "2025-12-28"),
            (.month, false, "2026-01-01", "2026-01-31"),
            (.month, true, "2025-12-01", "2025-12-31"),
            (.year, false, "2026-01-01", "2026-12-31"),
            (.year, true, "2025-01-01", "2025-12-31")
        ]
        for (period, previous, start, end) in expected {
            let range = diary.dateRange(for: period, containing: firstJanuary, previous: previous)
            XCTAssertEqual(diary.dayKey(range.lowerBound), start)
            XCTAssertEqual(diary.dayKey(range.upperBound), end)
        }
    }

    func testPreviousMonthFromMonthEndIncludesLeapDay() throws {
        let march = try XCTUnwrap(diary.date(for: "2024-03-31"))
        let month = diary.dateRange(for: .month, containing: march, previous: true)
        XCTAssertEqual(diary.dayKey(month.lowerBound), "2024-02-01")
        XCTAssertEqual(diary.dayKey(month.upperBound), "2024-02-29")
        let leapYear = diary.dateRange(for: .year, containing: march)
        XCTAssertEqual(diary.calendar.dateComponents([.day], from: leapYear.lowerBound, to: leapYear.upperBound).day, 365)
    }

    func testWeekUsesMondayRegardlessOfLocaleAndSurvivesDST() throws {
        let sundayFirst = DiaryCalendar(timeZone: diary.calendar.timeZone, locale: Locale(identifier: "en_US"))
        for (day, start, end) in [("2026-03-29", "2026-03-23", "2026-03-29"), ("2026-10-25", "2026-10-19", "2026-10-25"), ("2026-10-26", "2026-10-26", "2026-11-01")] {
            let date = try XCTUnwrap(sundayFirst.date(for: day))
            let range = sundayFirst.dateRange(for: .week, containing: date)
            XCTAssertEqual(sundayFirst.dayKey(range.lowerBound), start)
            XCTAssertEqual(sundayFirst.dayKey(range.upperBound), end)
            XCTAssertEqual(sundayFirst.calendar.dateComponents([.day], from: range.lowerBound, to: range.upperBound).day, 6)
        }
    }

    func testShortcutsUseHomeTimezoneAcrossUTCDateAndYearBoundary() throws {
        let instant = try XCTUnwrap(ISO8601DateFormatter().date(from: "2025-12-31T23:30:00Z"))
        let home = diary.dateRange(for: .year, containing: instant)
        XCTAssertEqual(diary.dayKey(home.lowerBound), "2026-01-01")
        let pacific = DiaryCalendar(timeZone: TimeZone(identifier: "America/Los_Angeles")!)
        let away = pacific.dateRange(for: .year, containing: instant)
        XCTAssertEqual(pacific.dayKey(away.lowerBound), "2025-01-01")
        let mondayAtHome = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-03-29T22:30:00Z"))
        XCTAssertEqual(diary.dayKey(diary.dateRange(for: .week, containing: mondayAtHome).lowerBound), "2026-03-30")
        XCTAssertEqual(pacific.dayKey(pacific.dateRange(for: .week, containing: mondayAtHome).lowerBound), "2026-03-23")
    }
}

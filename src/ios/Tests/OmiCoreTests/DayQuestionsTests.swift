import XCTest
@testable import OmiCore

final class DayQuestionsTests: XCTestCase {
    func testDayRetainsEarlierQuestionsAndHighlightsEveryQuestionInSelectedClick() throws {
        let page = try decode()
        XCTAssertEqual(page.questions.map(\.id), ["earlier", "first", "second", "latest"])
        XCTAssertEqual(page.highlightedIDs, ["first", "second", "latest"])
        XCTAssertEqual(page.firstHighlightedID, "first")
        XCTAssertEqual(page.questions.last?.answer, "The latest answer")
        XCTAssertNil(page.questions[1].answer)
        XCTAssertEqual(page.questions.first?.answer, "Earlier answer")
    }

    func testUnknownOrMissingClickDoesNotHighlightAnotherGroup() throws {
        let page = try decode()
        XCTAssertFalse(page.questions[0].belongsToClick(jobID: ""))
        XCTAssertFalse(page.questions[0].belongsToClick(jobID: "another-click"))
        XCTAssertTrue(page.questions[1].belongsToClick(jobID: "CLICK-B"))
    }

    func testExplicitSelectedIDsTakePriorityOverJobFallbackAndPreserveServerOrder() throws {
        let page = try JSONDecoder().decode(DayQuestions.self, from: Data(#"""
        {"day":"2026-10-31","selected_job_id":"click-b","selected_question_ids":["older","latest"],"processing":true,
         "questions":[{"id":"latest","job_id":"click-b","question":"Latest?","asked_at":"2026-10-31T10:15:02Z","time_precision":"segment"},
                      {"id":"other","job_id":"click-b","question":"Other?","asked_at":"2026-10-31T10:15:00Z"},
                      {"id":"older","job_id":"click-a","question":"Older?","asked_at":"2026-10-31T10:00:00Z"}]}
        """#.utf8))
        XCTAssertEqual(page.highlightedIDs, ["older", "latest"])
        XCTAssertEqual(page.firstHighlightedID, "latest")
        XCTAssertTrue(page.processing)
        XCTAssertEqual(page.questions.first?.timePrecision, "segment")
    }

    func testQuestionDatesAcceptFractionalAndWholeSeconds() throws {
        let page = try decode()
        XCTAssertNotNil(page.questions[0].askedDate)
        XCTAssertNotNil(page.questions[1].askedDate)
        XCTAssertEqual(page.timezone, "Europe/Copenhagen")
    }

    func testIndependentDayHasNoSelectedGroupAndKeepsNewestFirstOrder() throws {
        let page = try JSONDecoder().decode(DayQuestions.self, from: Data(#"""
        {"day":"2026-10-31","timezone":"Europe/Copenhagen","selected_job_id":null,"selected_question_ids":[],"processing":false,"error":null,
         "questions":[{"id":"latest","job_id":"click-b","question":"Latest?","answer":"Latest answer","asked_at":"2026-10-31T10:15:00Z"},
                      {"id":"earlier","job_id":"click-a","question":"Earlier?","answer":null,"asked_at":"2026-10-31T10:00:00Z"}]}
        """#.utf8))
        XCTAssertNil(page.selectedJobID)
        XCTAssertTrue(page.highlightedIDs.isEmpty)
        XCTAssertNil(page.firstHighlightedID)
        XCTAssertEqual(page.questions.map(\.id), ["latest", "earlier"])
    }

    private func decode() throws -> DayQuestions {
        try JSONDecoder().decode(DayQuestions.self, from: Data(#"""
        {"day":"2026-10-31","timezone":"Europe/Copenhagen","selected_job_id":"click-b","processing":false,"questions":[
          {"id":"earlier","job_id":"click-a","question":"Earlier question?","answer":"Earlier answer","asked_at":"2026-10-31T10:00:00Z"},
          {"id":"first","job_id":"click-b","question":"First question?","answer":null,"asked_at":"2026-10-31T10:15:00.123Z"},
          {"id":"second","job_id":"click-b","question":"Second question?","asked_at":"2026-10-31T10:15:01Z"},
          {"id":"latest","job_id":"click-b","question":"Latest question?","answer":"The latest answer","asked_at":"2026-10-31T10:15:02Z"}
        ]}
        """#.utf8))
    }
}

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

    func testClickGroupsPreserveNewestGroupAndQuestionOrderWithoutPromotingSelection() throws {
        let page = try groupedPage()
        XCTAssertEqual(page.clickGroups.map(\.id), ["click-new", "click-old"])
        XCTAssertEqual(page.clickGroups[0].questions.map(\.id), ["latest", "shared", "first"])
        XCTAssertEqual(page.clickGroups[0].questionCount, 3)
        XCTAssertFalse(page.isSelected(page.clickGroups[0]))
        XCTAssertTrue(page.isSelected(page.clickGroups[1]))
        XCTAssertEqual(page.firstHighlightedRowID, "click-old:shared")
        XCTAssertEqual(page.firstHighlightedGroupID, "question-group:click-old")
        XCTAssertNotNil(page.clickGroups[0].capturedDate)
    }

    func testOverlappingQuestionOccurrencesHaveDistinctRowsAndKeepEachAnswer() throws {
        let page = try groupedPage()
        let rows = page.clickGroups.flatMap(\.rows)
        XCTAssertEqual(rows.count, 4)
        XCTAssertEqual(Set(rows.map(\.id)).count, 4)
        let repeated = rows.filter { $0.question.id == "shared" }
        XCTAssertEqual(repeated.map { $0.question.answer }, ["New click answer", "Old click answer"])
    }

    func testFlatServerFallbackFormsClickGroupsWithoutChangingQuestionOrder() throws {
        let page = try decode()
        XCTAssertNil(page.groups)
        XCTAssertEqual(page.clickGroups.map(\.id), ["click-a", "click-b"])
        XCTAssertEqual(page.clickGroups[1].questions.map(\.id), ["first", "second", "latest"])
        XCTAssertEqual(page.clickGroups[1].questionCount, 3)
        XCTAssertEqual(page.firstHighlightedRowID, "click-b:first")
    }

    func testEmptyClickDoesNotRenderBareSectionOrHighlightEarlierQuestions() throws {
        let page = try JSONDecoder().decode(DayQuestions.self, from: Data(#"""
        {"day":"2026-10-31","selected_job_id":"empty-click","processing":false,"questions":[],"groups":[
          {"job_id":"empty-click","end":"2026-10-31T10:20:00Z","question_count":0,"questions":[]},
          {"job_id":"earlier","end":"2026-10-31T10:00:00Z","question_count":1,"questions":[
            {"id":"one","job_id":"earlier","question":"Earlier?","asked_at":"2026-10-31T10:00:00Z"}]}]}
        """#.utf8))
        XCTAssertEqual(page.clickGroups.map(\.id), ["earlier"])
        XCTAssertFalse(page.isSelected(page.clickGroups[0]))
        XCTAssertNil(page.firstHighlightedGroupID)
    }

    private func groupedPage() throws -> DayQuestions {
        try JSONDecoder().decode(DayQuestions.self, from: Data(#"""
        {"day":"2026-10-31","selected_job_id":"CLICK-OLD","selected_question_ids":["shared"],"processing":false,
         "questions":[],"groups":[
           {"job_id":"click-new","end":"2026-10-31T10:15:30Z","question_count":3,"questions":[
             {"id":"latest","job_id":"click-new","question":"Latest?","asked_at":"2026-10-31T10:15:00Z"},
             {"id":"shared","job_id":"click-new","question":"Repeated?","answer":"New click answer","asked_at":"2026-10-31T10:15:00Z"},
             {"id":"first","job_id":"click-new","question":"First?","asked_at":"2026-10-31T10:15:00Z"}]},
           {"job_id":"click-old","end":"2026-10-31T10:00:30.123Z","question_count":1,"questions":[
             {"id":"shared","job_id":"click-old","question":"Repeated?","answer":"Old click answer","asked_at":"2026-10-31T10:00:00Z"}]}]}
        """#.utf8))
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

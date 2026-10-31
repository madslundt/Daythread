package com.daythread.app.core;

import java.time.Instant;
import java.util.List;
import org.junit.Test;
import static org.junit.Assert.*;

public class QuestionDayTest {
    private static final String JOB = "11111111-1111-4111-8111-111111111111", PREVIOUS = "22222222-2222-4222-8222-222222222222";
    private Json response(boolean processing) {
        return new Json("""
            {"day":"2026-10-31","timezone":"Europe/Copenhagen","selected_job_id":"%s",
             "selected_question_ids":["selected-1","selected-2"],"processing":%s,"error":null,
             "questions":[
              {"id":"selected-1","job_id":"%s","question":"Latest question?","answer":"Latest answer.","asked_at":"2026-10-31T12:00:00Z","time_precision":"segment"},
              {"id":"selected-2","job_id":"%s","question":"Second question?","answer":null,"asked_at":"2026-10-31T11:59:50Z","time_precision":"recording"},
              {"id":"selected-3","job_id":"%s","question":"First question?","answer":null,"asked_at":"2026-10-31T11:59:40Z","time_precision":"request"},
              {"id":"previous-1","job_id":"%s","question":"Earlier today?","answer":"Earlier answer.","asked_at":"2026-10-31T10:00:00Z","time_precision":"request"}]}
            """.formatted(JOB, processing, JOB, JOB, JOB, PREVIOUS));
    }
    @Test public void selectedClickHighlightsEveryQuestionWhileEarlierDayQuestionsRemainVisible() {
        QuestionDay day = QuestionDay.parse(response(false), JOB);
        assertEquals("2026-10-31", day.day().toString()); assertEquals("Europe/Copenhagen", day.timezone().getId());
        assertEquals(4, day.questions().size());
        assertEquals(List.of(true, true, true, false), day.questions().stream().map(day::selected).toList());
        assertEquals("Earlier today?", day.questions().get(3).question());
        assertEquals("", day.questions().get(1).answer());
        assertFalse(day.processing()); assertEquals("", day.error());
    }
    @Test public void processingResponseKeepsPartialQuestionsAndRejectsAnotherSelectedClick() {
        QuestionDay day = QuestionDay.parse(response(true), JOB);
        assertTrue(day.processing()); assertEquals(4, day.questions().size());
        assertThrows(IllegalArgumentException.class, () -> QuestionDay.parse(response(false), PREVIOUS));
    }
    @Test public void highlightingAnOlderClickNeverMovesItAboveNewerQuestions() {
        Json response = new Json("""
            {"day":"2026-10-31","timezone":"Europe/Copenhagen","selected_job_id":"%s",
             "selected_question_ids":["selected"],"processing":false,"questions":[
              {"id":"newer","job_id":"%s","question":"Newer question?","asked_at":"2026-10-31T12:05:00Z"},
              {"id":"selected","job_id":"%s","question":"Selected older question?","asked_at":"2026-10-31T12:00:00Z"},
              {"id":"older","job_id":"%s","question":"Earlier question?","asked_at":"2026-10-31T11:00:00Z"}]}
            """.formatted(JOB, PREVIOUS, JOB, PREVIOUS));
        QuestionDay day = QuestionDay.parse(response, JOB);
        assertEquals(List.of("newer", "selected", "older"), day.questions().stream().map(QuestionDay.Question::id).toList());
        assertEquals(List.of(false, true, false), day.questions().stream().map(day::selected).toList());
        Json coarse = new Json(response(false).toString().replace("11:59:50Z", "12:00:00Z").replace("11:59:40Z", "12:00:00Z"));
        assertEquals(List.of("Latest question?", "Second question?", "First question?", "Earlier today?"),
            QuestionDay.parse(coarse, JOB).questions().stream().map(QuestionDay.Question::question).toList());
    }
    @Test public void notificationRouteKeepsOriginalArchiveAndProfileAfterSettingsChange() {
        Destination original = new Destination(List.of("https://original.example", "https://vpn.example"), "original-profile");
        String intent = new QuestionsRoute(JOB.toUpperCase(), original).json().toString();
        Destination changed = new Destination(List.of("https://different.example"), "different-profile");
        QuestionsRoute restored = QuestionsRoute.parse(intent);
        assertEquals(original, restored.destination()); assertNotEquals(changed, restored.destination());
        assertEquals("/v1/jobs/" + JOB + "/questions", restored.path());
        assertThrows(IllegalArgumentException.class, () -> new QuestionsRoute("invalid", original));
    }
    @Test public void elapsedTimeChangesToClockAtExactlyOneHour() {
        Instant now = Instant.parse("2026-10-31T12:00:00Z");
        assertEquals(QuestionTime.Kind.JUST_NOW, QuestionTime.at(now.minusSeconds(59), now).kind());
        assertEquals(1, QuestionTime.at(now.minusSeconds(60), now).minutes());
        assertEquals(59, QuestionTime.at(now.minusSeconds(3599), now).minutes());
        assertEquals(QuestionTime.Kind.CLOCK, QuestionTime.at(now.minusSeconds(3600), now).kind());
        assertEquals(QuestionTime.Kind.CLOCK, QuestionTime.at(now.minusSeconds(7200), now).kind());
        assertEquals(QuestionTime.Kind.JUST_NOW, QuestionTime.at(now.plusSeconds(1), now).kind());
    }
    @Test public void standaloneDayHasNoSelectedClickAndPreservesNewestFirstQuestions() {
        Json response = response(false).put("selected_job_id", org.json.JSONObject.NULL);
        QuestionDay day = QuestionDay.parse(response, null);
        assertNull(day.selectedJobID()); assertTrue(day.selectedQuestionIDs().isEmpty());
        assertEquals(List.of(false, false, false, false), day.questions().stream().map(day::selected).toList());
        assertEquals(List.of("Latest question?", "Second question?", "First question?", "Earlier today?"),
            day.questions().stream().map(QuestionDay.Question::question).toList());
        assertThrows(IllegalArgumentException.class, () -> QuestionDay.parse(response, JOB));
        assertThrows(IllegalArgumentException.class, () -> QuestionDay.parse(response(false), null));
    }
    @Test public void standaloneTodayAndSelectedDateRoutesRetainArchiveAndUseCorrectRetrySeparator() {
        Destination original = new Destination(List.of("https://original.example"), "original-profile");
        QuestionsRoute today = QuestionsRoute.today(original);
        assertNull(today.jobID()); assertNull(today.day()); assertEquals("/v1/questions", today.path());
        assertEquals("/v1/questions?retry=true", today.path(true));
        QuestionsRoute selected = QuestionsRoute.forDay(original, java.time.LocalDate.of(2026, 10, 30));
        assertEquals("/v1/questions?day=2026-10-30", selected.path());
        assertEquals("/v1/questions?day=2026-10-30&retry=true", selected.path(true));
        assertEquals(selected, QuestionsRoute.parse(selected.json().toString()));
        assertEquals(today, QuestionsRoute.parse(today.json().toString()));
        assertNotEquals(today, selected);
        assertThrows(IllegalArgumentException.class, () -> new QuestionsRoute(JOB, original, selected.day()));
    }
    private Json groupedResponse() {
        return new Json("""
            {"day":"2026-10-31","timezone":"Europe/Copenhagen","selected_job_id":"%s",
             "selected_question_ids":["shared"],"processing":false,"questions":[],"groups":[
              {"job_id":"%s","end":"2026-10-31T12:05:00Z","question_count":2,"questions":[
                {"id":"latest","job_id":"%s","question":"Newest question?","answer":"Newest answer.","asked_at":"2026-10-31T12:04:00Z"},
                {"id":"shared","job_id":"%s","question":"Shared question?","answer":"New click answer.","asked_at":"2026-10-31T12:00:00Z"}]},
              {"job_id":"%s","end":"2026-10-31T12:01:00Z","question_count":2,"questions":[
                {"id":"shared","job_id":"%s","question":"Shared question?","answer":"Original click answer.","asked_at":"2026-10-31T12:00:00Z"},
                {"id":"first","job_id":"%s","question":"First question?","answer":"First answer.","asked_at":"2026-10-31T12:00:00Z"}]},
              {"job_id":"33333333-3333-4333-8333-333333333333","end":"2026-10-31T11:00:00Z","question_count":0,"questions":[]}]}
            """.formatted(JOB, PREVIOUS, PREVIOUS, PREVIOUS, JOB, JOB, JOB));
    }
    @Test public void clickGroupsPreserveChronologyOwnAnswersAndRepeatedQuestionMembership() {
        QuestionDay day = QuestionDay.parse(groupedResponse(), JOB);
        assertEquals(List.of(PREVIOUS, JOB), day.visibleGroups().stream().map(QuestionDay.Group::jobID).toList());
        assertEquals(List.of(false, true), day.visibleGroups().stream().map(day::selected).toList());
        QuestionDay.Group newer = day.groups().get(0), selected = day.groups().get(1);
        assertEquals(Instant.parse("2026-10-31T12:01:00Z"), selected.end()); assertEquals(2, selected.questionCount());
        assertEquals(List.of("shared", "first"), selected.questions().stream().map(QuestionDay.Question::id).toList());
        assertEquals("New click answer.", newer.questions().get(1).answer());
        assertEquals("Original click answer.", selected.questions().get(0).answer());
        assertNotEquals(newer.rowKey(newer.questions().get(1)), selected.rowKey(selected.questions().get(0)));
        assertFalse(day.selected(newer.questions().get(1))); assertTrue(day.selected(selected.questions().get(0)));
        assertEquals(4, day.visibleGroups().stream().mapToInt(group -> group.questions().size()).sum());
    }
    @Test public void directDayHasGroupsWithoutHighlightAndEmptyClicksDoNotCreateSections() {
        Json response = groupedResponse().put("selected_job_id", org.json.JSONObject.NULL);
        QuestionDay day = QuestionDay.parse(response, null);
        assertEquals(List.of(false, false), day.visibleGroups().stream().map(day::selected).toList());
        assertEquals(3, day.groups().size()); assertEquals(2, day.visibleGroups().size());
        Json empty = new Json("""
            {"day":"2026-10-31","timezone":"Europe/Copenhagen","selected_job_id":null,"questions":[],"groups":[
              {"job_id":"%s","end":"2026-10-31T12:00:00Z","question_count":0,"questions":[]}]}
            """.formatted(JOB));
        assertTrue(QuestionDay.parse(empty, null).visibleGroups().isEmpty());
    }
    @Test public void legacyFlatQuestionsBecomeGroupsWithoutDroppingMembers() {
        QuestionDay day = QuestionDay.parse(response(false), JOB);
        assertEquals(List.of(JOB, PREVIOUS), day.groups().stream().map(QuestionDay.Group::jobID).toList());
        assertEquals(3, day.groups().get(0).questionCount());
        assertEquals(List.of("selected-1", "selected-2", "selected-3"), day.groups().get(0).questions().stream().map(QuestionDay.Question::id).toList());
    }
}

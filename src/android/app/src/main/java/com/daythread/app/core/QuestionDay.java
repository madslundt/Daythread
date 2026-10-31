package com.daythread.app.core;

import java.time.*;
import java.util.*;

/** The backend resolves the selected click's calendar day and returns its complete question list. */
public record QuestionDay(LocalDate day, ZoneId timezone, String selectedJobID,
                          Set<String> selectedQuestionIDs, List<Question> questions,
                          boolean processing, String error) {
    public record Question(String id, String jobID, String question, String answer,
                           Instant askedAt, String timePrecision) {}
    public boolean selected(Question question) {
        return selectedQuestionIDs.contains(question.id) || selectedJobID.equals(question.jobID);
    }
    public static QuestionDay parse(Json value, String expectedJobID) {
        String selected = UUID.fromString(value.getString("selected_job_id")).toString();
        if (!selected.equals(UUID.fromString(expectedJobID).toString())) throw new IllegalArgumentException("The question list belongs to a different click");
        Set<String> highlighted = new HashSet<>();
        if (value.has("selected_question_ids")) {
            Json.JsonArray ids = value.getJsonArray("selected_question_ids");
            for (int i = 0; i < ids.length(); i++) highlighted.add(ids.getString(i));
        }
        ArrayList<Question> questions = new ArrayList<>(); Json.JsonArray rows = value.getJsonArray("questions");
        for (int i = 0; i < rows.length(); i++) {
            Json row = rows.getJson(i);
            questions.add(new Question(row.getString("id"), UUID.fromString(row.getString("job_id")).toString(),
                row.getString("question"), row.optString("answer"), Instant.parse(row.getString("asked_at")), row.optString("time_precision", "request")));
        }
        return new QuestionDay(LocalDate.parse(value.getString("day")), ZoneId.of(value.getString("timezone")),
            selected, Set.copyOf(highlighted), List.copyOf(questions),
            value.has("processing") && Boolean.TRUE.equals(value.get("processing")), value.optString("error"));
    }
}

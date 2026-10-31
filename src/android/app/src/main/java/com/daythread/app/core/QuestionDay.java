package com.daythread.app.core;

import java.time.*;
import java.util.*;

/** The backend resolves the selected click's calendar day and returns its complete question list. */
public record QuestionDay(LocalDate day, ZoneId timezone, String selectedJobID,
                          Set<String> selectedQuestionIDs, List<Question> questions, List<Group> groups,
                          boolean processing, String error) {
    public record Question(String id, String jobID, String question, String answer,
                           Instant askedAt, String timePrecision) {}
    public record Group(String jobID, Instant end, int questionCount, List<Question> questions) {
        public String rowKey(Question question) { return jobID + ":" + question.id; }
    }
    public boolean selected(Question question) {
        return selectedJobID != null && selectedJobID.equals(question.jobID);
    }
    public boolean selected(Group group) { return selectedJobID != null && selectedJobID.equals(group.jobID); }
    public List<Group> visibleGroups() {
        ArrayList<Group> visible = new ArrayList<>();
        for (Group group : groups) if (!group.questions.isEmpty()) visible.add(group);
        return List.copyOf(visible);
    }
    public static QuestionDay parse(Json value, String expectedJobID) {
        String selected = value.optString("selected_job_id");
        selected = selected.isEmpty() ? null : UUID.fromString(selected).toString();
        String expected = expectedJobID == null ? null : UUID.fromString(expectedJobID).toString();
        if (!Objects.equals(selected, expected)) throw new IllegalArgumentException("The question list belongs to a different click");
        Set<String> highlighted = new HashSet<>();
        if (selected != null && value.has("selected_question_ids")) {
            Json.JsonArray ids = value.getJsonArray("selected_question_ids");
            for (int i = 0; i < ids.length(); i++) highlighted.add(ids.getString(i));
        }
        List<Question> questions = questions(value.getJsonArray("questions"));
        ArrayList<Group> groups = new ArrayList<>();
        if (value.has("groups")) {
            Json.JsonArray entries = value.getJsonArray("groups");
            for (int i = 0; i < entries.length(); i++) {
                Json group = entries.getJson(i); Object count = group.get("question_count");
                if (!(count instanceof Integer || count instanceof Long) || ((Number) count).longValue() < 0 || ((Number) count).longValue() > 1000)
                    throw new IllegalArgumentException("Invalid question count");
                groups.add(new Group(UUID.fromString(group.getString("job_id")).toString(), Instant.parse(group.getString("end")),
                    ((Number) count).intValue(), questions(group.getJsonArray("questions"))));
            }
        } else {
            // Older servers have no click cutoff; retain first-seen group and member order.
            Map<String, ArrayList<Question>> byClick = new LinkedHashMap<>();
            for (Question question : questions) byClick.computeIfAbsent(question.jobID, key -> new ArrayList<>()).add(question);
            byClick.forEach((job, members) -> groups.add(new Group(job,
                members.stream().map(Question::askedAt).max(Instant::compareTo).orElseThrow(), members.size(), List.copyOf(members))));
        }
        return new QuestionDay(LocalDate.parse(value.getString("day")), ZoneId.of(value.getString("timezone")),
            selected, Set.copyOf(highlighted), questions, List.copyOf(groups),
            value.has("processing") && Boolean.TRUE.equals(value.get("processing")), value.optString("error"));
    }
    private static List<Question> questions(Json.JsonArray rows) {
        ArrayList<Question> questions = new ArrayList<>();
        for (int i = 0; i < rows.length(); i++) {
            Json row = rows.getJson(i);
            questions.add(new Question(row.getString("id"), UUID.fromString(row.getString("job_id")).toString(),
                row.getString("question"), row.optString("answer"), Instant.parse(row.getString("asked_at")), row.optString("time_precision", "request")));
        }
        return List.copyOf(questions);
    }
}

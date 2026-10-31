package com.daythread.app.core;

import java.util.ArrayList;
import java.util.UUID;
import java.time.LocalDate;

/** Notification navigation owns the originating archive, independently of current settings. */
public record QuestionsRoute(String jobID, Destination destination, LocalDate day) {
    public QuestionsRoute(String jobID, Destination destination) { this(jobID, destination, null); }
    public QuestionsRoute {
        if (jobID != null) jobID = UUID.fromString(jobID).toString();
        if (jobID != null && day != null) throw new IllegalArgumentException("Choose either a click or a calendar day");
    }
    public static QuestionsRoute today(Destination destination) { return new QuestionsRoute(null, destination, null); }
    public static QuestionsRoute forDay(Destination destination, LocalDate day) { return new QuestionsRoute(null, destination, java.util.Objects.requireNonNull(day)); }
    public String path() { return jobID != null ? "/v1/jobs/" + jobID + "/questions" : "/v1/questions" + (day == null ? "" : "?day=" + day); }
    public String path(boolean retry) { String path = path(); return path + (retry ? (path.contains("?") ? "&" : "?") + "retry=true" : ""); }
    public Json json() {
        Json value = new Json().put("profile", destination.profile()).put("endpoints", new Json.JsonArray(destination.endpoints()));
        if (jobID != null) value.put("job_id", jobID);
        if (day != null) value.put("day", day.toString());
        return value;
    }
    public static QuestionsRoute parse(String text) {
        Json value = new Json(text); ArrayList<String> endpoints = new ArrayList<>();
        Json.JsonArray origins = value.getJsonArray("endpoints");
        for (int i = 0; i < origins.length(); i++) endpoints.add(origins.getString(i));
        String job = value.optString("job_id"), day = value.optString("day");
        return new QuestionsRoute(job.isEmpty() ? null : job, new Destination(endpoints, value.getString("profile")), day.isEmpty() ? null : LocalDate.parse(day));
    }
}

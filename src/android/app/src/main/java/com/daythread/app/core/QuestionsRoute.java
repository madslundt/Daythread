package com.daythread.app.core;

import java.util.ArrayList;
import java.util.UUID;

/** Notification navigation owns the originating archive, independently of current settings. */
public record QuestionsRoute(String jobID, Destination destination) {
    public QuestionsRoute { jobID = UUID.fromString(jobID).toString(); }
    public String path() { return "/v1/jobs/" + jobID + "/questions"; }
    public String path(boolean retry) { return path() + (retry ? "?retry=true" : ""); }
    public Json json() {
        return new Json().put("job_id", jobID).put("profile", destination.profile())
            .put("endpoints", new Json.JsonArray(destination.endpoints()));
    }
    public static QuestionsRoute parse(String text) {
        Json value = new Json(text); ArrayList<String> endpoints = new ArrayList<>();
        Json.JsonArray origins = value.getJsonArray("endpoints");
        for (int i = 0; i < origins.length(); i++) endpoints.add(origins.getString(i));
        return new QuestionsRoute(value.getString("job_id"), new Destination(endpoints, value.getString("profile")));
    }
}

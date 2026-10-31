package com.daythread.app.core;

/** Job results carry the latest question/answer text; older questions are never expanded here. */
public record WearableResultPreview(Integer questionCount, String text, String question, String answer) {
    public static WearableResultPreview from(String action, Json result, String fallback) {
        Integer count = null;
        if (action.equals("answer") && result.has("question_count")) {
            try {
                Object raw = result.get("question_count");
                if (raw instanceof Integer value && value >= 0 && value <= 1000) count = value;
                else if (raw instanceof Long value && value >= 0 && value <= 1000) count = value.intValue();
            } catch (IllegalArgumentException ignored) { /* Older results may lack valid count metadata. */ }
        }
        String question = action.equals("answer") && count != null && count > 0 ? result.optString("question").trim().replaceAll("\\s+", " ") : "";
        String answer = action.equals("answer") ? result.optString("answer").trim() : "";
        return new WearableResultPreview(count, result.optString("text", result.optString("summary", fallback)),
            question.isEmpty() ? null : question, answer.isEmpty() ? null : answer);
    }
    public String title(String fallback) { return question == null ? fallback : question; }
    public String notificationBody(String localizedCount) {
        String body = answer == null ? text : answer;
        return questionCount == null ? body : body + "\n\n" + localizedCount;
    }
}

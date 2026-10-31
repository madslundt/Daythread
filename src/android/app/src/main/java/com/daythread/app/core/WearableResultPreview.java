package com.daythread.app.core;

/** Job results carry the latest question/answer text; older questions are never expanded here. */
public record WearableResultPreview(Integer questionCount, String text) {
    public static WearableResultPreview from(String action, Json result, String fallback) {
        Integer count = null;
        if (action.equals("answer") && result.has("question_count")) {
            try {
                Object raw = result.get("question_count");
                if (raw instanceof Integer value && value >= 0 && value <= 1000) count = value;
                else if (raw instanceof Long value && value >= 0 && value <= 1000) count = value.intValue();
            } catch (IllegalArgumentException ignored) { /* Older results may lack valid count metadata. */ }
        }
        return new WearableResultPreview(count, result.optString("text", result.optString("summary", fallback)));
    }
}

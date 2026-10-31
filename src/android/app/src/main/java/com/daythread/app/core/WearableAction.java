package com.daythread.app.core;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.time.Instant;
import java.util.Set;

/** Only resolved short clicks are actionable; press, release and hold are ignored. */
public enum WearableAction {
    MARK("mark"), MEETING("meeting_toggle"), SUMMARIZE("summarize"), ANSWER("answer"),
    TRANSLATE("translate"), CAPTURE("capture_toggle"), NONE("none");

    public final String value;
    WearableAction(String value) { this.value = value; }
    public static WearableAction stored(String value, WearableAction fallback) {
        for (WearableAction action : values()) if (action.value.equals(value)) return action;
        return fallback;
    }
    public static WearableAction resolve(byte[] data, WearableAction single, WearableAction doubleClick) {
        return switch (clickCode(data)) {
            case 1 -> single;
            case 2 -> doubleClick;
            default -> NONE;
        };
    }
    public static int clickCode(byte[] data) {
        if (data.length < 4) return 0;
        int code = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN).getInt();
        return code == 1 || code == 2 ? code : 0;
    }
    public static int duration(int seconds) { return Set.of(15, 30, 60, 120, 300).contains(seconds) ? seconds : 30; }
    public boolean recentAudio() { return this == SUMMARIZE || this == ANSWER || this == TRANSLATE; }
    public Json request(int seconds, Instant end, String device) {
        if (!recentAudio()) throw new IllegalArgumentException("This action does not use recent audio");
        return new Json().put("kind", "wearable_action").put("parameters", new Json()
            .put("action", value).put("duration_seconds", duration(seconds)).put("end", end.toString()).put("device_id", device));
    }
}

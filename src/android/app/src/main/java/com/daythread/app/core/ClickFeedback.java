package com.daythread.app.core;

/** Local acknowledgement describes accepted intent, never a completed server result. */
public final class ClickFeedback {
    private ClickFeedback() {}
    public static final boolean DEFAULT_ENABLED = true;
    public enum Outcome { ACCEPTED, PAUSED, UNAVAILABLE, FAILED }
    public enum Message { SUMMARIZING, ANSWERING, TRANSLATING, MARKING, MEETING, PAUSING, RESUMING, NONE, PAUSED, UNAVAILABLE, FAILED }
    public static boolean enabled(Boolean stored) { return stored == null ? DEFAULT_ENABLED : stored; }
    public static Message message(WearableAction action, Outcome outcome, boolean wasCapturing) {
        if (outcome != Outcome.ACCEPTED) return switch (outcome) {
            case PAUSED -> Message.PAUSED;
            case UNAVAILABLE -> Message.UNAVAILABLE;
            default -> Message.FAILED;
        };
        return switch (action) {
            case SUMMARIZE -> Message.SUMMARIZING;
            case ANSWER -> Message.ANSWERING;
            case TRANSLATE -> Message.TRANSLATING;
            case MARK -> Message.MARKING;
            case MEETING -> Message.MEETING;
            case CAPTURE -> wasCapturing ? Message.PAUSING : Message.RESUMING;
            case NONE -> Message.NONE;
        };
    }
}

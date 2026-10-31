package com.daythread.app.core;

import java.time.Duration;
import java.time.Instant;

/** Relative labels last less than one hour; older questions use their wall-clock time. */
public record QuestionTime(Kind kind, int minutes) {
    public enum Kind { JUST_NOW, MINUTES_AGO, CLOCK }
    public static QuestionTime at(Instant asked, Instant now) {
        long elapsed = Math.max(0, Duration.between(asked, now).getSeconds());
        if (elapsed >= 3600) return new QuestionTime(Kind.CLOCK, 0);
        if (elapsed < 60) return new QuestionTime(Kind.JUST_NOW, 0);
        return new QuestionTime(Kind.MINUTES_AGO, (int) (elapsed / 60));
    }
}

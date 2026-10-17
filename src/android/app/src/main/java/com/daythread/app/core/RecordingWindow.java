package com.daythread.app.core;

import java.time.Instant;
import java.time.ZonedDateTime;
import java.time.temporal.ChronoUnit;

/** ISO weekdays: bit 0 = Monday, bit 6 = Sunday; end time is exclusive. */
public record RecordingWindow(boolean enabled, boolean scheduled, boolean manualOverride,
                              int days, int startMinute, int endMinute) {
    public boolean allows(ZonedDateTime date) {
        if (!enabled) return false;
        if (manualOverride || !scheduled) return true;
        if (startMinute < 0 || startMinute >= 1440 || endMinute < 0 || endMinute >= 1440 || startMinute == endMinute) return false;
        int day = date.getDayOfWeek().getValue() - 1;
        int minute = date.getHour() * 60 + date.getMinute();
        if (startMinute < endMinute) return selected(day) && minute >= startMinute && minute < endMinute;
        return selected(day) && minute >= startMinute || selected((day + 6) % 7) && minute < endMinute;
    }
    /** A single timer at the next change replaces polling, including repeated/skipped DST minutes. */
    public Instant nextChange(ZonedDateTime date) {
        if (!enabled || !scheduled || manualOverride || (days & 127) == 0 || startMinute == endMinute
            || startMinute < 0 || startMinute >= 1440 || endMinute < 0 || endMinute >= 1440) return null;
        boolean current = allows(date);
        ZonedDateTime limit = date.plusDays(8);
        for (ZonedDateTime next = date.truncatedTo(ChronoUnit.MINUTES).plusMinutes(1);
             !next.isAfter(limit); next = next.plusMinutes(1)) {
            if (allows(next) != current) return next.toInstant();
        }
        return null;
    }
    private boolean selected(int day) { return (days & (1 << day)) != 0; }
}

package com.daythread.app.core;

import org.junit.Test;
import static org.junit.Assert.*;
import java.time.*;

public class RecordingWindowTest {
    private ZonedDateTime at(String value) { return Instant.parse(value).atZone(ZoneId.of("Europe/Copenhagen")); }
    @Test public void boundariesAndWeekdays() {
        RecordingWindow window = new RecordingWindow(true, true, false, 31, 540, 1020);
        assertFalse(window.allows(at("2026-10-16T06:59:59Z")));
        assertTrue(window.allows(at("2026-10-16T07:00:00Z")));
        assertFalse(window.allows(at("2026-10-16T15:00:00Z")));
        assertFalse(window.allows(at("2026-10-17T08:00:00Z")));
    }
    @Test public void overnightUsesStartingDayIncludingSundayWrapAndDST() {
        RecordingWindow sunday = new RecordingWindow(true, true, false, 64, 1320, 360);
        assertTrue(sunday.allows(at("2026-10-18T21:00:00Z")));
        assertTrue(sunday.allows(at("2026-10-19T03:00:00Z")));
        assertFalse(sunday.allows(at("2026-10-19T04:00:00Z")));
        RecordingWindow saturday = new RecordingWindow(true, true, false, 32, 1320, 360);
        assertTrue(saturday.allows(at("2026-10-25T01:30:00Z")));
        assertFalse(saturday.allows(at("2026-10-25T05:00:00Z")));
    }
    @Test public void manualOverrideAlwaysWorksAndDisableWins() {
        assertTrue(new RecordingWindow(true, true, true, 0, 540, 540).allows(ZonedDateTime.now()));
        assertFalse(new RecordingWindow(false, true, true, 127, 540, 1020).allows(ZonedDateTime.now()));
        assertFalse(new RecordingWindow(true, true, false, 0, 540, 1020).allows(ZonedDateTime.now()));
        assertFalse(new RecordingWindow(true, true, false, 127, 540, 540).allows(ZonedDateTime.now()));
        assertTrue(new RecordingWindow(true, false, false, 0, -1, 2000).allows(ZonedDateTime.now()));
    }
    @Test public void nextChangeSkipsIdleDaysAndDoesNotPollManualCapture() {
        RecordingWindow weekdays = new RecordingWindow(true, true, false, 31, 540, 1020);
        assertEquals(Instant.parse("2026-10-19T07:00:00Z"), weekdays.nextChange(at("2026-10-16T15:00:00Z")));
        assertEquals(Instant.parse("2026-10-16T15:00:00Z"), weekdays.nextChange(at("2026-10-16T14:59:59.999Z")));
        assertNull(new RecordingWindow(true, true, true, 31, 540, 1020).nextChange(at("2026-10-16T15:00:00Z")));
        assertNull(new RecordingWindow(true, false, false, 31, 540, 1020).nextChange(at("2026-10-16T15:00:00Z")));
        assertNull(new RecordingWindow(false, true, false, 31, 540, 1020).nextChange(at("2026-10-16T15:00:00Z")));
        assertNull(new RecordingWindow(true, true, false, 0, 540, 1020).nextChange(at("2026-10-16T15:00:00Z")));
    }
    @Test public void nextChangeUsesBothDSTOccurrencesAndSkippedHour() {
        RecordingWindow sunday = new RecordingWindow(true, true, false, 64, 150, 165);
        assertEquals(Instant.parse("2026-10-25T00:45:00Z"), sunday.nextChange(at("2026-10-25T00:35:00Z")));
        assertEquals(Instant.parse("2026-10-25T01:30:00Z"), sunday.nextChange(at("2026-10-25T00:45:00Z")));
        RecordingWindow spring = new RecordingWindow(true, true, false, 64, 150, 240);
        assertEquals(Instant.parse("2026-03-29T01:00:00Z"), spring.nextChange(at("2026-03-29T00:00:00Z")));
    }
}

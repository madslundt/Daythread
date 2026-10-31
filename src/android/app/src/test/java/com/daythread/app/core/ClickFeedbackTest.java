package com.daythread.app.core;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import org.junit.Test;
import static org.junit.Assert.*;
import static com.daythread.app.core.ClickFeedback.*;

public class ClickFeedbackTest {
    private byte[] code(int value) { return ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(value).array(); }
    @Test public void feedbackDefaultsOnAndExplicitOffIsPreserved() {
        assertTrue(enabled(null)); assertTrue(enabled(true)); assertFalse(enabled(false));
    }
    @Test public void noActionClickIsDistinguishableFromHoldAndRawEvents() {
        assertEquals(1, WearableAction.clickCode(code(1))); assertEquals(2, WearableAction.clickCode(code(2)));
        assertEquals(WearableAction.NONE, WearableAction.resolve(code(1), WearableAction.NONE, WearableAction.MARK));
        assertEquals(Message.NONE, message(WearableAction.NONE, Outcome.ACCEPTED, true));
        for (int value : new int[]{0, 3, 4, 5, 257}) assertEquals(0, WearableAction.clickCode(code(value)));
        assertEquals(0, WearableAction.clickCode(new byte[]{1}));
    }
    @Test public void acknowledgementDescribesIntentAndHonestOutcomes() {
        assertEquals(Message.SUMMARIZING, message(WearableAction.SUMMARIZE, Outcome.ACCEPTED, true));
        assertEquals(Message.ANSWERING, message(WearableAction.ANSWER, Outcome.ACCEPTED, true));
        assertEquals(Message.TRANSLATING, message(WearableAction.TRANSLATE, Outcome.ACCEPTED, true));
        assertEquals(Message.MARKING, message(WearableAction.MARK, Outcome.ACCEPTED, true));
        assertEquals(Message.MEETING, message(WearableAction.MEETING, Outcome.ACCEPTED, true));
        assertEquals(Message.PAUSING, message(WearableAction.CAPTURE, Outcome.ACCEPTED, true));
        assertEquals(Message.RESUMING, message(WearableAction.CAPTURE, Outcome.ACCEPTED, false));
        assertEquals(Message.PAUSED, message(WearableAction.MARK, Outcome.PAUSED, false));
        assertEquals(Message.FAILED, message(WearableAction.ANSWER, Outcome.FAILED, true));
        assertEquals(Message.UNAVAILABLE, message(WearableAction.MEETING, Outcome.UNAVAILABLE, true));
    }
}

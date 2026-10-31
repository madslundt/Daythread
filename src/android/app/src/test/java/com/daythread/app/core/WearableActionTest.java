package com.daythread.app.core;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.time.Instant;
import org.junit.Test;
import static org.junit.Assert.*;

public class WearableActionTest {
    private byte[] code(int value) { return ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(value).array(); }
    @Test public void defaultsAndRemappingUseOnlyResolvedClicks() {
        assertEquals(WearableAction.MARK, WearableAction.stored(null, WearableAction.MARK));
        assertEquals(WearableAction.MEETING, WearableAction.stored("obsolete", WearableAction.MEETING));
        assertEquals(WearableAction.ANSWER, WearableAction.resolve(code(1), WearableAction.ANSWER, WearableAction.CAPTURE));
        assertEquals(WearableAction.CAPTURE, WearableAction.resolve(code(2), WearableAction.ANSWER, WearableAction.CAPTURE));
        for (int value : new int[]{0, 3, 4, 5, 257})
            assertEquals(WearableAction.NONE, WearableAction.resolve(code(value), WearableAction.MARK, WearableAction.MEETING));
        assertEquals(WearableAction.NONE, WearableAction.resolve(new byte[]{1}, WearableAction.MARK, WearableAction.MEETING));
        assertEquals(WearableAction.NONE, WearableAction.resolve(code(1), WearableAction.NONE, WearableAction.MEETING));
    }
    @Test public void recentAudioRequestFreezesOriginalWindowAndUsesSafeDefault() {
        Instant end = Instant.parse("2026-10-31T11:12:13Z");
        Json request = WearableAction.ANSWER.request(120, end, "omi-test");
        assertEquals("wearable_action", request.getString("kind"));
        assertEquals("answer", request.getJson("parameters").getString("action"));
        assertEquals(end.toString(), request.getJson("parameters").getString("end"));
        assertEquals(120, request.getJson("parameters").getInt("duration_seconds"));
        assertEquals("omi-test", request.getJson("parameters").getString("device_id"));
        assertEquals(30, WearableAction.TRANSLATE.request(-1, end, "omi-test").getJson("parameters").getInt("duration_seconds"));
        assertThrows(IllegalArgumentException.class, () -> WearableAction.MARK.request(30, end, "omi-test"));
    }
}

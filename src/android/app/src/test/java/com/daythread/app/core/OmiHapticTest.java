package com.daythread.app.core;

import org.junit.Test;
import static org.junit.Assert.*;

public class OmiHapticTest {
    @Test public void singleClickIsAlwaysShortAndDoubleClickIsAlwaysLong() {
        assertEquals(OmiHaptic.SHORT, OmiHaptic.forClick(1).orElseThrow());
        assertEquals(OmiHaptic.LONG, OmiHaptic.forClick(2).orElseThrow());
    }
    @Test public void unknownAndRawGesturesCannotBuzz() {
        for (int click : new int[]{-1, 0, 3, 4, 5, 100, 257})
            assertTrue(OmiHaptic.forClick(click).isEmpty());
    }
    @Test public void fixedWireCommandsAreOneAndThree() {
        assertArrayEquals(new byte[]{1}, OmiHaptic.SHORT.payload());
        assertArrayEquals(new byte[]{3}, OmiHaptic.LONG.payload());
        assertEquals(100, OmiHaptic.SHORT.milliseconds);
        assertEquals(500, OmiHaptic.LONG.milliseconds);
    }
}

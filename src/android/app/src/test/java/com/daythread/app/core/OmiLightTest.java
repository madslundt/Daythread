package com.daythread.app.core;

import org.junit.Test;
import static org.junit.Assert.*;

public class OmiLightTest {
    @Test public void turningOffAndOnRestoresBrightnessAfterConfirmedReadback() {
        OmiLight light = new OmiLight(); light.accept(new byte[]{27});
        assertArrayEquals(new byte[]{0}, light.command(false));
        assertEquals(Integer.valueOf(27), light.ratio()); assertTrue(light.pending());
        light.accept(new byte[]{0});
        assertArrayEquals(new byte[]{27}, light.command(true));
        light.accept(new byte[]{27}); assertFalse(light.pending());
    }
    @Test public void connectingToAnOffDeviceUsesDefaultBrightness() {
        OmiLight light = new OmiLight(); light.accept(new byte[]{0});
        assertArrayEquals(new byte[]{50}, light.command(true));
    }
    @Test public void malformedAndUnconfirmedReadsCannotClaimSuccess() {
        OmiLight light = new OmiLight();
        for (byte[] value : new byte[][]{null, {}, {1, 2}, {101}, {(byte) 255}})
            assertThrows(IllegalArgumentException.class, () -> light.accept(value));
        assertThrows(IllegalStateException.class, () -> light.command(true));
        light.accept(new byte[]{50}); light.command(false);
        assertThrows(IllegalStateException.class, () -> light.command(true));
        assertThrows(IllegalArgumentException.class, () -> light.accept(new byte[]{50}));
        assertEquals(Integer.valueOf(50), light.ratio()); assertTrue(light.pending());
    }
    @Test public void disconnectClearsStateAndDoesNotBorrowAnotherDevicesBrightness() {
        OmiLight light = new OmiLight(); light.accept(new byte[]{100}); light.command(false); light.clear();
        assertNull(light.ratio()); assertFalse(light.pending());
        light.accept(new byte[]{0}); assertArrayEquals(new byte[]{50}, light.command(true));
    }
}

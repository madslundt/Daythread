package com.daythread.app.core;

import org.junit.Test;
import static org.junit.Assert.*;

public class OmiChargingPolicyTest {
    @Test public void disabledByDefaultAndRejectsMalformedStatus() {
        OmiChargingPolicy policy = new OmiChargingPolicy(false);
        policy.update(true, false, false); assertFalse(policy.paused());
        assertEquals(Boolean.FALSE, OmiChargingPolicy.decode(new byte[]{0}));
        assertEquals(Boolean.TRUE, OmiChargingPolicy.decode(new byte[]{1}));
        for (byte[] value : new byte[][]{null, {}, {2}, {(byte)255}, {1, 0}})
            assertNull(OmiChargingPolicy.decode(value));
    }
    @Test public void dockUndockAndReconnectPreserveHoldUntilConfirmedRemoval() {
        OmiChargingPolicy policy = new OmiChargingPolicy(false);
        policy.update(true, true, true); assertTrue(policy.paused());
        policy.update(null, true, true); assertTrue(policy.paused());
        policy = new OmiChargingPolicy(policy.paused());
        policy.update(true, true, true); assertTrue(policy.paused());
        policy.update(false, true, true); assertFalse(policy.paused());
    }
    @Test public void resumeOptOutStaysPausedUntilExplicitResume() {
        OmiChargingPolicy policy = new OmiChargingPolicy(false);
        policy.update(true, true, false);
        policy.update(false, true, false); assertTrue(policy.paused());
        policy.resumeManually(false, true); assertFalse(policy.paused());
        policy.resumeManually(true, true); assertTrue(policy.paused());
        policy.update(true, false, false); assertFalse(policy.paused());
    }
    @Test public void enablingResumeAfterRemovalReleasesTheHeldRecording() {
        OmiChargingPolicy policy = new OmiChargingPolicy(false);
        policy.update(true, true, false);
        policy.update(false, true, false); assertTrue(policy.paused());
        policy.update(false, true, true); assertFalse(policy.paused());
    }
}

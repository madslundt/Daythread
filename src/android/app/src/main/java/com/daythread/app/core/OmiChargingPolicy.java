package com.daythread.app.core;

/** Independent pendant-only hold; never changes manual recording or schedule preferences. */
public final class OmiChargingPolicy {
    private boolean paused;
    public OmiChargingPolicy(boolean paused) { this.paused = paused; }
    public boolean paused() { return paused; }
    public static Boolean decode(byte[] value) {
        return value != null && value.length == 1 && (value[0] == 0 || value[0] == 1) ? value[0] == 1 : null;
    }
    public void update(Boolean charging, boolean autoPause, boolean autoResume) {
        if (!autoPause) paused = false;
        else if (Boolean.TRUE.equals(charging)) paused = true;
        else if (Boolean.FALSE.equals(charging) && autoResume) paused = false;
    }
    public void resumeManually(Boolean charging, boolean autoPause) {
        paused = autoPause && Boolean.TRUE.equals(charging);
    }
}

package com.daythread.app.core;

/** The pendant's persisted LED PWM percentage. State changes only after a valid readback. */
public final class OmiLight {
    private Integer ratio, pending;
    private int lastOnRatio = 50;

    public Integer ratio() { return ratio; }
    public boolean pending() { return pending != null; }
    public byte[] command(boolean enabled) {
        if (ratio == null || pending != null) throw new IllegalStateException("Light is not ready");
        pending = enabled ? lastOnRatio : 0;
        return new byte[]{pending.byteValue()};
    }
    public void accept(byte[] value) {
        if (value == null || value.length != 1 || (value[0] & 255) > 100)
            throw new IllegalArgumentException("Invalid LED ratio");
        int actual = value[0] & 255;
        if (pending != null && pending != actual) throw new IllegalArgumentException("LED readback differs");
        ratio = actual;
        if (actual > 0) lastOnRatio = actual;
        pending = null;
    }
    public void clear() { ratio = null; pending = null; lastOnRatio = 50; }
}

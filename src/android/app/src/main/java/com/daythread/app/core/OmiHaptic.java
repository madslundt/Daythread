package com.daythread.app.core;

import java.util.Optional;

/** Fixed feedback for resolved pendant clicks, sent using response-required writes. */
public enum OmiHaptic {
    SHORT(1, 100), LONG(3, 500);

    public final int command;
    public final int milliseconds;
    OmiHaptic(int command, int milliseconds) { this.command = command; this.milliseconds = milliseconds; }
    public static Optional<OmiHaptic> forClick(int clickCode) {
        return switch (clickCode) {
            case 1 -> Optional.of(SHORT);
            case 2 -> Optional.of(LONG);
            default -> Optional.empty();
        };
    }
    public byte[] payload() { return new byte[]{(byte) command}; }
}

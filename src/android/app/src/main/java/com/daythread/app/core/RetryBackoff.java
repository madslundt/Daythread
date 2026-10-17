package com.daythread.app.core;

/** Monotonic-time backoff shared by connection and foreground upload attempts. */
public final class RetryBackoff {
    private final long initialMillis, maximumMillis;
    private long delayMillis, readyAt;
    public RetryBackoff(long initialMillis, long maximumMillis) {
        if (initialMillis <= 0 || maximumMillis < initialMillis) throw new IllegalArgumentException("Invalid retry interval");
        this.initialMillis = initialMillis; this.maximumMillis = maximumMillis;
    }
    public synchronized boolean ready(long now) { return now >= readyAt; }
    public synchronized long fail(long now) {
        delayMillis = delayMillis == 0 ? initialMillis : Math.min(maximumMillis, delayMillis * 2);
        readyAt = now + delayMillis;
        return delayMillis;
    }
    public synchronized void reset() { delayMillis = 0; readyAt = 0; }
}

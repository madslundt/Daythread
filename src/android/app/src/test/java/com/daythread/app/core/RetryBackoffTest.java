package com.daythread.app.core;

import org.junit.Test;
import static org.junit.Assert.*;

public class RetryBackoffTest {
    @Test public void repeatedFailureBacksOffAndSuccessResets() {
        RetryBackoff retry = new RetryBackoff(10000, 300000);
        assertEquals(10000, retry.fail(100));
        assertFalse(retry.ready(10099));
        assertTrue(retry.ready(10100));
        assertEquals(20000, retry.fail(10100));
        assertEquals(40000, retry.fail(30100));
        for (int i = 0; i < 50; i++) assertTrue(retry.fail(1000000) <= 300000);
        retry.reset();
        assertTrue(retry.ready(0));
        assertEquals(10000, retry.fail(0));
    }
}

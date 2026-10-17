package com.daythread.app.core;

import java.io.*;

/** Bounded reads supported on Android 8, without loading an unbounded server response. */
public final class Streams {
    private Streams() {}
    public static byte[] read(InputStream input, int limit) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream(Math.min(limit, 8192));
        byte[] buffer = new byte[8192];
        while (output.size() < limit) {
            int count = input.read(buffer, 0, Math.min(buffer.length, limit - output.size()));
            if (count == -1) break;
            output.write(buffer, 0, count);
        }
        return output.toByteArray();
    }
    public static void copy(InputStream input, OutputStream output) throws IOException {
        byte[] buffer = new byte[16384];
        for (int count; (count = input.read(buffer)) != -1;) output.write(buffer, 0, count);
    }
}

package com.daythread.app.core;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;

/** Verified custody-v1 copy protocol. There are deliberately no advance or clear commands. */
public final class OmiRingProtocol {
    public static final int RECORD_BYTES = 444, MAX_BATCH = 32, REQUIRED_CAPS = 0x0d;
    public static final UUID SERVICE = UUID.fromString("30295780-4301-eabd-2904-2849adfeae43");
    public static final UUID CONTROL = UUID.fromString("30295781-4301-eabd-2904-2849adfeae43");
    public static final UUID STATUS = UUID.fromString("30295782-4301-eabd-2904-2849adfeae43");
    public static final byte[] INFO = {0x10}, CUSTODY = {0x14, 1, REQUIRED_CAPS};
    public enum Failure { UNSUPPORTED, TRANSFER, CLOCK, CHANGED, OVERLAP }
    public static final class Invalid extends IllegalArgumentException {
        public final Failure failure;
        public Invalid(Failure failure) { super(failure.name()); this.failure = failure; }
    }
    private static void require(boolean valid, Failure failure) { if (!valid) throw new Invalid(failure); }
    public static boolean supportsFirmware(String version) {
        if (!version.matches("[0-9]+\\.[0-9]+\\.[0-9]+")) return false;
        try {
            String[] parts = version.split("\\.");
            int major = Integer.parseInt(parts[0]), minor = Integer.parseInt(parts[1]), patch = Integer.parseInt(parts[2]);
            return major > 3 || major == 3 && (minor > 0 || patch >= 20);
        } catch (NumberFormatException e) { return false; }
    }
    public static boolean rtcValid(byte[] data) {
        return data != null && data.length == 16 && ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN).getInt(12) == 1;
    }
    public static boolean custodyAccepted(byte[] data) {
        return data.length >= 3 && data[0] == 1 && data[1] == 0 && (data[2] & REQUIRED_CAPS) == REQUIRED_CAPS;
    }
    public record Info(long read, long write, long capacity, long dropped, long ring) {
        public long unread() { return write - read; }
        public static Info parse(byte[] data) {
            require(data.length >= 41 && data[0] == 2 && data[32] == 1
                && (data[31] & REQUIRED_CAPS) == REQUIRED_CAPS
                && ByteBuffer.wrap(data).getShort(29) == RECORD_BYTES, Failure.UNSUPPORTED);
            ByteBuffer bytes = ByteBuffer.wrap(data);
            Info info = new Info(bytes.getLong(1), bytes.getLong(9), Integer.toUnsignedLong(bytes.getInt(17)), bytes.getLong(21), bytes.getLong(33));
            // The application bounds sequence counters to signed 63-bit; ring incarnations retain all 64 bits.
            require(info.read >= 0 && info.write >= info.read && info.capacity > 0
                && info.unread() <= info.capacity && info.ring != 0, Failure.TRANSFER);
            return info;
        }
    }
    public static byte[] read(long start, int count) {
        require(start >= 0 && count > 0 && count <= MAX_BATCH && start <= Long.MAX_VALUE - count, Failure.TRANSFER);
        return ByteBuffer.allocate(13).put((byte) 0x11).putLong(start).putInt(count).array();
    }
    public static String sourceID(String device, long ring, long sequence) {
        return "omi-ring:" + device + ":" + Long.toUnsignedString(ring) + ":" + sequence;
    }
    public static String stableID(String device, long ring, long sequence) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(sourceID(device, ring, sequence).getBytes(StandardCharsets.UTF_8));
            hash[6] = (byte) ((hash[6] & 15) | 0x50); hash[8] = (byte) ((hash[8] & 63) | 0x80);
            ByteBuffer bytes = ByteBuffer.wrap(hash);
            return new UUID(bytes.getLong(), bytes.getLong()).toString();
        } catch (java.security.NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
    public record Record(long sequence, byte[] raw, Instant capturedAt, List<byte[]> frames) {
        public static Record parse(long sequence, byte[] raw, Instant now) {
            require(raw.length == RECORD_BYTES, Failure.TRANSFER);
            long epoch = Integer.toUnsignedLong(ByteBuffer.wrap(raw).getInt());
            require(epoch >= 1577836800L && epoch < 0x80000000L && epoch <= now.getEpochSecond() + 30, Failure.CLOCK);
            ArrayList<byte[]> frames = new ArrayList<>();
            for (int offset = 4; offset < raw.length - 1;) {
                int size = raw[offset] & 255;
                if (size == 0) { offset++; continue; }
                // Firmware may leave a trailing size byte when its next frame does not fit.
                if (offset + 1 + size >= raw.length) break;
                frames.add(Arrays.copyOfRange(raw, offset + 1, offset + 1 + size)); offset += size + 1;
            }
            require(!frames.isEmpty(), Failure.TRANSFER);
            return new Record(sequence, raw.clone(), Instant.ofEpochSecond(epoch), List.copyOf(frames));
        }
    }
    public static final class Transfer {
        private final long start;
        private final int count;
        private boolean began, complete;
        private final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        public Transfer(long start, int count) { read(start, count); this.start = start; this.count = count; }
        public boolean complete() { return complete; }
        public void receive(byte[] value) {
            require(value.length > 0 && !complete, Failure.TRANSFER);
            ByteBuffer data = ByteBuffer.wrap(value);
            switch (value[0]) {
                case 5 -> { require(!began && value.length == 13 && data.getLong(1) == start && data.getInt(9) == count, Failure.TRANSFER); began = true; }
                case 3 -> {
                    require(began && bytes.size() + value.length - 1 <= count * RECORD_BYTES, Failure.TRANSFER);
                    bytes.write(value, 1, value.length - 1);
                }
                case 4 -> {
                    require(began && value.length == 10 && value[1] == 0 && data.getLong(2) == start + count
                        && bytes.size() == count * RECORD_BYTES, Failure.TRANSFER); complete = true;
                }
                case 1, 2, 6 -> { }
                default -> throw new Invalid(Failure.TRANSFER);
            }
        }
        public List<Record> records(Instant now) {
            require(complete, Failure.TRANSFER);
            byte[] raw = bytes.toByteArray(); ArrayList<Record> records = new ArrayList<>();
            for (int index = 0; index < count; index++) {
                Record record = Record.parse(start + index, Arrays.copyOfRange(raw, index * RECORD_BYTES, (index + 1) * RECORD_BYTES), now);
                if (!records.isEmpty()) {
                    long step = record.capturedAt.getEpochSecond() - records.get(records.size() - 1).capturedAt.getEpochSecond();
                    require(step >= 0 && step <= 60, Failure.CLOCK);
                }
                records.add(record);
            }
            return List.copyOf(records);
        }
    }
}

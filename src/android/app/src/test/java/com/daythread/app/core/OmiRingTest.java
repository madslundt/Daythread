package com.daythread.app.core;

import io.github.jaredmdobson.concentus.*;
import org.junit.*;
import org.junit.rules.TemporaryFolder;
import static org.junit.Assert.*;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.time.Instant;
import java.util.*;

public class OmiRingTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();
    private final Destination destination = new Destination(List.of("https://home.example"), "default");
    private final Instant at = Instant.parse("2026-10-17T10:00:00Z");
    private byte[] frame() throws Exception {
        OpusEncoder encoder = new OpusEncoder(16000, 1, OpusApplication.OPUS_APPLICATION_AUDIO);
        short[] samples = new short[320];
        for (int i = 0; i < samples.length; i++) samples[i] = (short) (Math.sin(i * .15) * 10000);
        byte[] bytes = new byte[255]; int size = encoder.encode(samples, 0, 320, bytes, 0, bytes.length);
        return Arrays.copyOf(bytes, size);
    }
    private byte[] record(Instant time) throws Exception {
        byte[] frame = frame();
        return ByteBuffer.allocate(444).putInt((int) time.getEpochSecond()).put((byte) frame.length).put(frame).array();
    }
    private byte[] info(long start, long end, long ring) {
        return ByteBuffer.allocate(41).put((byte) 2).putLong(start).putLong(end).putInt(1024).putLong(0)
            .putShort((short) 444).put((byte) 0x0d).put((byte) 1).putLong(ring).array();
    }
    private OmiRingProtocol.Transfer transfer(byte[] raw) {
        OmiRingProtocol.Transfer transfer = new OmiRingProtocol.Transfer(10, 1);
        transfer.receive(ByteBuffer.allocate(13).put((byte) 5).putLong(10).putInt(1).array());
        transfer.receive(ByteBuffer.allocate(101).put((byte) 3).put(raw, 0, 100).array());
        transfer.receive(ByteBuffer.allocate(345).put((byte) 3).put(raw, 100, 344).array());
        transfer.receive(ByteBuffer.allocate(10).put((byte) 4).put((byte) 0).putLong(11).array());
        return transfer;
    }
    @Test public void custodyCapabilityClockAndFirmwareGatesFailClosed() {
        for (String valid : List.of("3.0.20", "3.1.0", "4.0.0")) assertTrue(OmiRingProtocol.supportsFirmware(valid));
        for (String bad : List.of("3.0.19", "3.0.20-beta", "prefix3.0.20", "3.0.20.1", "-3.0.20")) assertFalse(OmiRingProtocol.supportsFirmware(bad));
        byte[] clock = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN).putInt(12, 1).array();
        assertTrue(OmiRingProtocol.rtcValid(clock)); assertFalse(OmiRingProtocol.rtcValid(new byte[16]));
        clock[13] = 1; assertFalse(OmiRingProtocol.rtcValid(clock));
        assertTrue(OmiRingProtocol.custodyAccepted(new byte[]{1, 0, 13}));
        assertFalse(OmiRingProtocol.custodyAccepted(new byte[]{1, 0, 1}));
        assertEquals(5, OmiRingProtocol.Info.parse(info(10, 15, -1)).unread());
        byte[] legacy = info(10, 15, 99); legacy[31] = 1;
        assertThrows(OmiRingProtocol.Invalid.class, () -> OmiRingProtocol.Info.parse(legacy));
        assertThrows(OmiRingProtocol.Invalid.class, () -> OmiRingProtocol.Info.parse(info(15, 10, 99)));
        assertThrows(OmiRingProtocol.Invalid.class, () -> OmiRingProtocol.Info.parse(info(0, 5000, 99)));
    }
    @Test public void arbitraryFragmentsAreAdmittedOnlyAfterExactBeginAndDone() throws Exception {
        byte[] raw = record(at); OmiRingProtocol.Transfer good = transfer(raw);
        assertTrue(good.complete()); assertEquals(at, good.records(Instant.now()).get(0).capturedAt());
        assertThrows(OmiRingProtocol.Invalid.class, () -> good.receive(new byte[]{3, 1}));
        OmiRingProtocol.Transfer missing = new OmiRingProtocol.Transfer(10, 1);
        assertThrows(OmiRingProtocol.Invalid.class, () -> missing.receive(new byte[]{3, 1}));
        OmiRingProtocol.Transfer shortRead = new OmiRingProtocol.Transfer(10, 1);
        shortRead.receive(ByteBuffer.allocate(13).put((byte) 5).putLong(10).putInt(1).array());
        assertThrows(OmiRingProtocol.Invalid.class, () -> shortRead.receive(ByteBuffer.allocate(10).put((byte) 4).put((byte) 0).putLong(11).array()));
        assertThrows(OmiRingProtocol.Invalid.class, () -> new OmiRingProtocol.Transfer(Long.MAX_VALUE, 1));
        byte[] uptime = raw.clone(); ByteBuffer.wrap(uptime).putInt(0, 0x80000010);
        assertThrows(OmiRingProtocol.Invalid.class, () -> transfer(uptime).records(Instant.now()));
    }
    @Test public void durableRealOpusCopiesHaveStableIdentityAndRestartCursor() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        List<OmiRingProtocol.Record> records = transfer(record(at)).records(Instant.now());
        assertEquals(1, outbox.copyRingRecords(destination, "pendant", 99, records, 21));
        Outbox.Item item = outbox.pending().get(0);
        assertEquals(OmiRingProtocol.stableID("pendant", 99, 10), item.id());
        assertEquals("fb58c342-6eba-52aa-a447-4807587c8606", item.id());
        assertEquals("omi-ring:pendant:99:10", item.data().getString("source_id"));
        assertEquals("manual_upload", item.data().getString("source_kind"));
        assertEquals(at.toString(), item.data().getString("started_at"));
        assertEquals(at.plusMillis(20).toString(), item.data().getString("ended_at"));
        assertEquals(684, item.file().length());
        assertArrayEquals(records.get(0).raw(), Files.readAllBytes(new File(directory, item.id() + ".ring").toPath()));
        Outbox restored = new Outbox(directory);
        assertEquals(11, restored.ringCursor(destination, "pendant", 99));
        assertEquals(0, restored.copyRingRecords(destination, "pendant", 99, records, 21));
        assertEquals(1, restored.pending().size());
        Files.delete(new File(directory, item.id() + ".json").toPath()); Files.delete(item.file().toPath());
        assertEquals(0, new Outbox(directory).copyRingRecords(destination, "pendant", 99, records, 21));
        assertEquals(0, restored.ringCursor(new Destination(destination.endpoints(), "other"), "pendant", 99));
        assertEquals(0, restored.ringCursor(destination, "pendant", 100));
    }
    @Test public void invalidLaterRecordsAndOverflowCannotPartiallyAdmitABatch() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        OmiRingProtocol.Record first = OmiRingProtocol.Record.parse(10, record(at), Instant.now());
        OmiRingProtocol.Record backwards = OmiRingProtocol.Record.parse(11, record(at.minusSeconds(1)), Instant.now());
        assertThrows(OmiRingProtocol.Invalid.class, () -> outbox.copyRingRecords(destination, "pendant", 99, List.of(first, backwards), 21));
        assertEquals(0, directory.listFiles().length);
        OmiRingProtocol.Record overflow = OmiRingProtocol.Record.parse(Long.MAX_VALUE, record(at), Instant.now());
        assertThrows(OmiRingProtocol.Invalid.class, () -> outbox.copyRingRecords(destination, "pendant", 99, List.of(overflow), 21));
        assertTrue(outbox.pending().isEmpty()); assertEquals(0, outbox.ringCursor(destination, "pendant", 99));
    }
    @Test public void replayChecksCommittedIdentityAndDecodedFilesBeforeTrustingReceipt() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        List<OmiRingProtocol.Record> records = transfer(record(at)).records(Instant.now());
        outbox.copyRingRecords(destination, "pendant", 99, records, 21);
        Outbox.Item item = outbox.pending().get(0);
        assertThrows(OmiRingProtocol.Invalid.class, () -> outbox.copyRingRecords(new Destination(destination.endpoints(), "other"), "pendant", 99, records, 21));
        byte[] changed = records.get(0).raw().clone(); changed[10] ^= 1;
        assertThrows(OmiRingProtocol.Invalid.class, () -> outbox.copyRingRecords(destination, "pendant", 99,
            List.of(OmiRingProtocol.Record.parse(10, changed, Instant.now())), 21));
        Outbox.atomic(item.file(), new byte[]{1, 2});
        assertThrows(OmiRingProtocol.Invalid.class, () -> outbox.copyRingRecords(destination, "pendant", 99, records, 21));
        assertEquals(11, outbox.ringCursor(destination, "pendant", 99));
    }
    @Test public void liveOverlapAndUncommittedWritesNeverAdvanceCursor() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        List<OmiRingProtocol.Record> records = transfer(record(at)).records(Instant.now());
        try (Outbox.Writer writer = outbox.begin(destination, "pendant", at)) { writer.append(new byte[640], 0, 640, null); }
        OmiRingProtocol.Invalid overlap = assertThrows(OmiRingProtocol.Invalid.class, () -> outbox.copyRingRecords(destination, "pendant", 99, records, 21));
        assertEquals(OmiRingProtocol.Failure.OVERLAP, overlap.failure);
        assertEquals(0, outbox.ringCursor(destination, "pendant", 99));
        File broken = temporary.newFolder(); Outbox interrupted = new Outbox(broken);
        String id = OmiRingProtocol.stableID("pendant", 99, 10);
        assertTrue(new File(broken, id + ".wav.tmp").mkdir());
        assertThrows(IOException.class, () -> interrupted.copyRingRecords(destination, "pendant", 99, records, 21));
        assertEquals(0, interrupted.ringCursor(destination, "pendant", 99)); assertTrue(interrupted.pending().isEmpty());
        Files.delete(new File(broken, id + ".wav.tmp").toPath());
        assertEquals(1, interrupted.copyRingRecords(destination, "pendant", 99, records, 21));
    }
}

package com.daythread.app.core;

import org.junit.*;
import org.junit.rules.TemporaryFolder;
import static org.junit.Assert.*;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.time.Instant;
import java.util.*;
import io.github.jaredmdobson.concentus.*;

public class CaptureCoreTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();
    private final Destination destination = new Destination(List.of("https://home.example/", "http://vpn.example:8787"), "default");
    private final Instant started = Instant.parse("2026-10-17T10:00:00Z");
    private byte[] packet(int sequence, int fragment, int... bytes) {
        ByteBuffer data = ByteBuffer.allocate(bytes.length + 3).order(ByteOrder.LITTLE_ENDIAN).putShort((short) sequence).put((byte) fragment);
        for (int value : bytes) data.put((byte) value);
        return data.array();
    }
    @Test public void reassemblesFragmentsAndSequenceWrapWithoutDuplicateAudio() {
        OmiFrames frames = new OmiFrames();
        assertTrue(frames.consume(packet(65534, 0, 1, 2)).started());
        assertNull(frames.consume(packet(65535, 1, 3, 4)).frame());
        assertNull(frames.consume(packet(65535, 1, 3, 4)).frame());
        OmiFrames.Result result = frames.consume(packet(0, 0, 5, 6));
        assertFalse(result.lost()); assertArrayEquals(new byte[]{1, 2, 3, 4}, result.frame());
        assertArrayEquals(new byte[]{5, 6}, frames.flush()); assertNull(frames.flush());
    }
    @Test public void packetLossDropsPartialFrameAndWaitsForNewBoundary() {
        OmiFrames frames = new OmiFrames();
        frames.consume(packet(1, 0, 1, 2));
        assertTrue(frames.consume(packet(3, 1, 3, 4)).lost());
        assertNull(frames.consume(packet(4, 0, 5, 6)).frame());
        assertArrayEquals(new byte[]{5, 6}, frames.consume(packet(5, 0, 7, 8)).frame());
    }
    @Test public void invalidFragmentsAndOversizedFramesDoNotEnterAudio() {
        OmiFrames frames = new OmiFrames();
        assertTrue(frames.consume(new byte[3]).lost());
        frames.consume(packet(1, 0, 1, 2)); assertTrue(frames.consume(packet(2, 2, 3, 4)).lost());
        assertNull(frames.flush());
        byte[] tooLarge = new byte[8196]; tooLarge[0] = 3;
        assertTrue(frames.consume(tooLarge).lost());
    }
    @Test public void decodesPcmAndRejectsAmbiguousCodecAndUnalignedSamples() throws Exception {
        assertArrayEquals(new byte[]{1, 2}, new OmiFrames.Decoder(0).decode(new byte[]{1, 2}));
        assertThrows(IllegalArgumentException.class, () -> new OmiFrames.Decoder(1));
        assertThrows(IllegalArgumentException.class, () -> new OmiFrames.Decoder(0).decode(new byte[]{1}));
    }
    private byte[] opus(int samples) throws Exception {
        OpusEncoder encoder = new OpusEncoder(16000, 1, OpusApplication.OPUS_APPLICATION_AUDIO);
        short[] pcm = new short[samples];
        for (int i = 0; i < samples; i++) pcm[i] = (short) (Math.sin(i * 0.15) * 10000);
        byte[] packet = new byte[1024]; int count = encoder.encode(pcm, 0, samples, packet, 0, packet.length);
        return Arrays.copyOf(packet, count);
    }
    @Test public void opusDevkitAndProductionCodecsEnforceTheirActualDurations() throws Exception {
        assertEquals(320, new OmiFrames.Decoder(20).decode(opus(160)).length);
        assertEquals(640, new OmiFrames.Decoder(21).decode(opus(320)).length);
        assertThrows(IllegalArgumentException.class, () -> new OmiFrames.Decoder(20).decode(opus(320)));
        assertThrows(IllegalArgumentException.class, () -> new OmiFrames.Decoder(21).decode(opus(160)));
    }
    @Test public void queueSurvivesRestartWithOriginalDestinationIdAndSampleClock() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        try (Outbox.Writer writer = outbox.begin(destination, "android-test", started)) { writer.append(new byte[32000], 0, 32000, null); }
        Outbox.Item original = outbox.pending().get(0), restored = new Outbox(directory).pending().get(0);
        assertEquals(original.id(), restored.id()); assertEquals(destination, restored.destination());
        assertEquals(started.plusSeconds(1).toString(), restored.data().getString("ended_at"));
        byte[] wav = Files.readAllBytes(restored.file().toPath());
        assertEquals(32044, wav.length); assertEquals(16000, ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN).getInt(24));
        assertEquals(32000, ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN).getInt(40));
    }
    @Test public void crashRecoveryUsesPreallocatedIdAndOnlyCompleteSamples() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        Outbox.Writer writer = outbox.begin(destination, "omi-test", started);
        writer.append(new byte[640], 0, 640, new byte[]{1, 2, 3});
        // A fresh instance models a process that died with a journal and an open PCM file.
        File journal = Arrays.stream(directory.listFiles()).filter(f -> f.getName().endsWith(".capture")).findFirst().orElseThrow();
        String id = new Json(new String(Files.readAllBytes(journal.toPath()), java.nio.charset.StandardCharsets.UTF_8)).getString("id");
        Outbox recovered = new Outbox(directory); assertEquals(1, recovered.recover());
        assertEquals(id, recovered.pending().get(0).id());
        assertEquals(started.plusMillis(20).toString(), recovered.pending().get(0).data().getString("ended_at"));
        assertEquals(0, recovered.recover());
        Outbox.Item gap = recovered.pending().stream().filter(item -> item.kind().equals("gap")).findFirst().orElseThrow();
        assertEquals("delivery_interrupted", gap.data().getString("cause"));
        assertEquals(started.plusMillis(20).toString(), gap.data().getString("started_at"));
        // Closing the stale writer is not part of a real process restart; release test resources without committing again.
    }
    @Test public void openingActivityCannotRecoverTheServiceLiveWriter() throws Exception {
        Outbox outbox = new Outbox(temporary.newFolder());
        assertFalse(outbox.hasPending()); assertEquals(0, outbox.pendingCount());
        try (Outbox.Writer writer = outbox.begin(destination, "android-test", started)) {
            writer.append(new byte[640], 0, 640, null); assertEquals(0, outbox.recover()); assertTrue(outbox.pending().isEmpty());
            assertFalse(outbox.hasPending()); assertEquals(0, outbox.pendingCount());
        }
        assertEquals(1, outbox.pending().size()); assertTrue(outbox.hasPending()); assertEquals(1, outbox.pendingCount());
    }
    private Json receipt(Outbox.Item item) { return new Json(item.data().toString()).put("id", item.id()).put("state", "queued"); }
    @Test public void arbitrarySuccessWrongProfileAndWrongCaptureBoundsKeepOriginalAudio() throws Exception {
        Outbox outbox = new Outbox(temporary.newFolder());
        try (Outbox.Writer writer = outbox.begin(destination, "android-test", started)) { writer.append(new byte[640], 0, 640, null); }
        Outbox.Item item = outbox.pending().get(0);
        assertThrows(IOException.class, () -> outbox.acknowledge(item, new Json().put("ok", true), "default"));
        assertThrows(IOException.class, () -> outbox.acknowledge(item, receipt(item), "another-profile"));
        assertThrows(IOException.class, () -> outbox.acknowledge(item, receipt(item).put("ended_at", started.plusSeconds(1).toString()), "default"));
        assertTrue(item.file().exists()); assertEquals(item.id(), outbox.pending().get(0).id());
        outbox.acknowledge(item, receipt(item), "default"); assertFalse(item.file().exists()); assertTrue(outbox.pending().isEmpty());
        assertFalse(outbox.hasPending()); assertEquals(0, outbox.pendingCount());
    }
    @Test public void rawEventRetriesKeepTheirIdAndCapturedPolicy() throws Exception {
        Outbox outbox = new Outbox(temporary.newFolder());
        String id = outbox.enqueue("event", destination, new Json().put("device_id", "omi-test").put("occurred_at", started.toString())
            .put("action", "mark").put("meeting_max_minutes", 90).put("meeting_min_seconds", 60).put("marker_cooldown_seconds", 30));
        Outbox.Item event = outbox.pending().get(0); assertEquals(id, event.data().getString("event_id"));
        assertThrows(IOException.class, () -> outbox.acknowledge(event, new Json(event.data().toString()).put("marker_cooldown_seconds", 60), "default"));
        outbox.acknowledge(event, event.data(), "default"); assertTrue(outbox.pending().isEmpty());
    }
    @Test public void newEventReceiptsNeedNoRetiredPolicyFields() throws Exception {
        Outbox outbox = new Outbox(temporary.newFolder());
        outbox.enqueue("event", destination, new Json().put("device_id", "omi-test").put("occurred_at", started.toString()).put("action", "mark"));
        Outbox.Item event = outbox.pending().get(0);
        Json response = new Json(event.data().toString()).put("meeting_max_minutes", 120).put("meeting_min_seconds", 60).put("marker_cooldown_seconds", 60);
        outbox.acknowledge(event, response, "default"); assertTrue(outbox.pending().isEmpty());
    }
    @Test public void corruptedQueueFailsClosedAndPreservesSavedFiles() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        Files.write(new File(directory, "broken.json").toPath(), "{broken".getBytes(java.nio.charset.StandardCharsets.UTF_8));
        assertThrows(IOException.class, outbox::pending); assertTrue(new File(directory, "broken.json").exists());
    }
    @Test public void wearableRequestSurvivesRestartAfterItsAudioWithFrozenBounds() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        try (Outbox.Writer writer = outbox.begin(destination, "omi-test", started)) { writer.append(new byte[32000], 0, 32000, null); }
        String id = outbox.enqueue("generation", destination, WearableAction.ANSWER.request(30, started.plusSeconds(1), "omi-test"));
        List<Outbox.Item> pending = new Outbox(directory).pending();
        assertEquals("audio", pending.get(0).kind());
        Outbox.Item request = pending.get(1);
        assertEquals(id, request.data().getString("id")); assertEquals(destination, request.destination());
        assertEquals(started.plusSeconds(1).toString(), request.data().getJson("parameters").getString("end"));
        Json receipt = new Json(request.data().toString()).put("state", "queued");
        Json wrong = new Json(receipt.toString()); wrong.getJson("parameters").put("end", started.plusSeconds(60).toString());
        assertThrows(IOException.class, () -> outbox.acknowledge(request, wrong, "default"));
        assertThrows(IOException.class, () -> outbox.acknowledge(request, receipt, "another-profile"));
        outbox.acknowledge(request, receipt.put("state", "completed"), "default"); assertEquals(1, outbox.pendingCount());
    }
    @Test public void endpointsRejectCredentialsPathsAndOtherSchemes() {
        assertEquals("https://home.example", destination.endpoints().get(0));
        for (String endpoint : List.of("https://user:secret@home.example", "file:///tmp", "https://home.example/path", "https://home.example?token=secret", "https://home.example#x", "javascript:alert(1)"))
            assertThrows(IllegalArgumentException.class, () -> Destination.normalize(endpoint));
        assertTrue(Destination.sameOrigin("https://home.example", "https://home.example:443/today"));
        assertFalse(Destination.sameOrigin("https://home.example", "https://home.example.evil/today"));
        assertFalse(Destination.sameOrigin("https://home.example", "http://home.example/today"));
        assertFalse(Destination.sameOrigin("https://home.example", "https://user@home.example/today"));
    }
}

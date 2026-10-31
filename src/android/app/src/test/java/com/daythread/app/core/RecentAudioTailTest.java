package com.daythread.app.core;

import java.io.File;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.time.Instant;
import java.util.*;
import org.junit.*;
import org.junit.rules.TemporaryFolder;
import static org.junit.Assert.*;

public class RecentAudioTailTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();
    private final Destination home = new Destination(List.of("https://home.example"), "default");
    private final Instant start = Instant.parse("2026-10-31T12:00:00Z");
    private byte[] packet(int sequence, int fragment, byte[] pcm) {
        return ByteBuffer.allocate(pcm.length + 3).order(ByteOrder.LITTLE_ENDIAN)
            .putShort((short) sequence).put((byte) fragment).put(pcm).array();
    }
    @Test public void requestCannotUploadBeforeTheAlreadyReceivedQuestionTailIsCommitted() throws Exception {
        Outbox outbox = new Outbox(temporary.newFolder()); OmiFrames frames = new OmiFrames();
        OmiFrames.Decoder decoder = new OmiFrames.Decoder(0);
        byte[] beginning = new byte[640], finalWord = new byte[640]; Arrays.fill(finalWord, (byte) 7);
        frames.consume(packet(1, 0, beginning));
        OmiFrames.Result first = frames.consume(packet(2, 0, finalWord));
        Outbox.Writer writer = outbox.begin(home, "omi-test", start);
        writer.append(decoder.decode(first.frame()), 0, 640, first.frame());
        Integer pendingAtClick = frames.pendingSequence();
        String job = outbox.stageRecentAction(home, WearableAction.ANSWER.request(30, start.plusMillis(40), "omi-test"));
        assertFalse(outbox.uploadPlan().stream().anyMatch(item -> item.id().equals(job)));
        OmiFrames.Result tail = frames.consume(packet(3, 0, new byte[640]));
        assertEquals(pendingAtClick, tail.completedSequence());
        writer.append(decoder.decode(tail.frame()), 0, 640, tail.frame()); writer.close();
        outbox.releaseRecentAction(job);
        List<Outbox.Item> plan = outbox.uploadPlan();
        assertEquals("audio", plan.get(0).kind()); assertEquals(job, plan.get(1).id());
        byte[] wave = Files.readAllBytes(plan.get(0).file().toPath());
        assertEquals(1280 + 44, wave.length);
        assertArrayEquals(finalWord, Arrays.copyOfRange(wave, 44 + 640, wave.length));
        assertEquals(start.plusMillis(40).toString(), plan.get(1).data().getJson("parameters").getString("end"));
    }
    @Test public void laterMatchingAudioCannotChangeAFrozenRequestsDependencies() throws Exception {
        Outbox outbox = new Outbox(temporary.newFolder());
        try (Outbox.Writer writer = outbox.begin(home, "omi-test", start)) { writer.append(new byte[640], 0, 640, null); }
        String original = outbox.pending().get(0).id();
        String job = outbox.enqueue("generation", home, WearableAction.ANSWER.request(30, start.plusMillis(40), "omi-test"));
        try (Outbox.Writer writer = outbox.begin(home, "omi-test", start.plusMillis(20))) { writer.append(new byte[640], 0, 640, null); }
        List<Outbox.Item> plan = outbox.uploadPlan();
        assertEquals(original, plan.get(0).id()); assertEquals(job, plan.get(1).id());
        assertEquals("audio", plan.get(2).kind());
    }
    @Test public void fragmentedPreClickFrameKeepsItsWatermarkUntilTheNaturalBoundary() {
        OmiFrames frames = new OmiFrames();
        byte[] first = new byte[317], last = new byte[323]; Arrays.fill(last, (byte) 9);
        frames.consume(packet(65534, 0, first));
        Integer clickWatermark = frames.pendingSequence();
        assertEquals(Integer.valueOf(65534), clickWatermark);
        assertNull(frames.consume(packet(65535, 1, last)).frame());
        assertEquals(clickWatermark, frames.pendingSequence());
        OmiFrames.Result finished = frames.consume(packet(0, 0, new byte[640]));
        assertEquals(clickWatermark, finished.completedSequence());
        assertEquals(640, finished.frame().length);
        assertArrayEquals(last, Arrays.copyOfRange(finished.frame(), 317, 640));
        assertEquals(Integer.valueOf(0), frames.pendingSequence());
        assertTrue(frames.consume(packet(2, 1, new byte[3])).lost());
        assertNull(frames.pendingSequence());
    }
    @Test public void overlappingClicksShareOneCommittedTailButRetainTheirOwnCutoffAndID() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        Outbox.Writer writer = outbox.begin(home, "omi-test", start);
        writer.append(new byte[640], 0, 640, null);
        String first = outbox.stageRecentAction(home, WearableAction.ANSWER.request(30, start.plusMillis(40), "omi-test"));
        String second = outbox.stageRecentAction(home, WearableAction.SUMMARIZE.request(30, start.plusMillis(50), "omi-test"));
        assertTrue(outbox.uploadPlan().isEmpty());
        assertEquals(0, outbox.recover()); // An activity cannot release a live service's barrier.
        assertTrue(outbox.uploadPlan().isEmpty());
        writer.append(new byte[640], 0, 640, null); writer.close();
        outbox.releaseRecentAction(first); outbox.releaseRecentAction(second);
        List<Outbox.Item> restored = new Outbox(directory).uploadPlan();
        assertEquals(List.of(restored.get(0).id(), first, second), restored.stream().map(Outbox.Item::id).toList());
        for (Outbox.Item request : restored.subList(1, 3))
            assertEquals(List.of(restored.get(0).id()), request.audioDependencies());
        assertEquals(start.plusMillis(40).toString(), restored.get(1).data().getJson("parameters").getString("end"));
        assertEquals(start.plusMillis(50).toString(), restored.get(2).data().getJson("parameters").getString("end"));
    }
    @Test public void restartRecoversDecodedPrefixThenReleasesOriginalDraftWithAnExplicitGap() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        Instant captured = Instant.now().minusSeconds(60), end = captured.plusMillis(40);
        Outbox.Writer writer = outbox.begin(home, "omi-test", captured);
        writer.append(new byte[640], 0, 640, null);
        String id = outbox.stageRecentAction(home, WearableAction.ANSWER.request(30, end, "omi-test"), captured.plusMillis(20));
        assertTrue(outbox.uploadPlan().isEmpty());
        Outbox recovered = new Outbox(directory); assertEquals(1, recovered.recover());
        List<Outbox.Item> plan = recovered.uploadPlan();
        assertEquals("audio", plan.get(0).kind()); assertEquals(id, plan.get(1).id());
        assertEquals(end.toString(), plan.get(1).data().getJson("parameters").getString("end"));
        assertEquals(List.of(plan.get(0).id()), plan.get(1).audioDependencies());
        Outbox.Item gap = plan.stream().filter(item -> item.kind().equals("gap") && item.data().getString("cause").equals("incomplete_frame")).findFirst().orElseThrow();
        assertEquals(captured.plusMillis(20).toString(), gap.data().getString("started_at"));
        assertEquals(end.toString(), gap.data().getString("ended_at"));
        assertEquals(0, recovered.recover());
        assertEquals(plan.stream().map(Outbox.Item::id).toList(), recovered.uploadPlan().stream().map(Outbox.Item::id).toList());
    }
    @Test public void timeoutWithoutAnyDecodedAudioReleasesTheDraftAndKeepsItsGapStable() throws Exception {
        Outbox outbox = new Outbox(temporary.newFolder());
        String job = outbox.stageRecentAction(home, WearableAction.ANSWER.request(30, start, "omi-test"));
        outbox.releaseRecentAction(job, true);
        List<Outbox.Item> first = outbox.uploadPlan();
        assertEquals(job, first.get(0).id()); assertTrue(first.get(0).audioDependencies().isEmpty());
        assertEquals("gap", first.get(1).kind());
        outbox.releaseRecentAction(job, true);
        assertEquals(first.stream().map(Outbox.Item::id).toList(), outbox.uploadPlan().stream().map(Outbox.Item::id).toList());
    }
}

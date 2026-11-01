package com.daythread.app.core;

import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;

/** Durable non-destructive copies. The local cursor is scoped to destination, profile and incarnation. */
final class RingRecoveryStore {
    private final File directory;
    RingRecoveryStore(File directory) { this.directory = directory; }
    private File receipt(Destination destination, String device, long ring) {
        String scope = destination.profile() + ":" + String.join("|", destination.endpoints()) + ":" + device;
        return new File(directory, OmiRingProtocol.stableID(scope, ring, 0) + ".ring-receipt");
    }
    long cursor(Destination destination, String device, long ring) throws IOException {
        File file = receipt(destination, device, ring);
        if (!file.exists()) return 0;
        try {
            String value = new String(Files.readAllBytes(file.toPath()), StandardCharsets.US_ASCII);
            long parsed = Long.parseLong(value);
            if (parsed < 0) throw new NumberFormatException();
            return parsed;
        } catch (RuntimeException e) { throw new IOException("Cannot read pendant copy receipt", e); }
    }
    private void syncDirectory() throws IOException {
        try (FileChannel channel = FileChannel.open(directory.toPath(), StandardOpenOption.READ)) { channel.force(true); }
    }
    int copy(Outbox outbox, Destination destination, String device, long ring,
             List<OmiRingProtocol.Record> records, int codec) throws Exception {
        if ((codec != 20 && codec != 21) || ring == 0 || records.isEmpty() || records.size() > OmiRingProtocol.MAX_BATCH)
            throw new OmiRingProtocol.Invalid(OmiRingProtocol.Failure.UNSUPPORTED);
        long previous = -1; Instant lastTime = null;
        // Public callers cannot bypass the wire parser with forged Record fields.
        ArrayList<OmiRingProtocol.Record> verified = new ArrayList<>();
        for (OmiRingProtocol.Record proposed : records) {
            OmiRingProtocol.Record record = OmiRingProtocol.Record.parse(proposed.sequence(), proposed.raw(), Instant.now());
            if (record.sequence() < 0 || record.sequence() == Long.MAX_VALUE || previous >= 0 && record.sequence() != previous + 1)
                throw new OmiRingProtocol.Invalid(OmiRingProtocol.Failure.TRANSFER);
            if (lastTime != null && (record.capturedAt().isBefore(lastTime) || record.capturedAt().isAfter(lastTime.plusSeconds(60))))
                throw new OmiRingProtocol.Invalid(OmiRingProtocol.Failure.CLOCK);
            previous = record.sequence(); lastTime = record.capturedAt(); verified.add(record);
        }
        records = List.copyOf(verified);
        List<Outbox.Item> pending = outbox.pending();
        long savedCursor = cursor(destination, device, ring);
        int count = 0;
        for (OmiRingProtocol.Record record : records) {
            String id = OmiRingProtocol.stableID(device, ring, record.sequence());
            String source = OmiRingProtocol.sourceID(device, ring, record.sequence());
            File original = new File(directory, id + ".ring");
            if (original.exists() && !Arrays.equals(Files.readAllBytes(original.toPath()), record.raw()))
                throw new OmiRingProtocol.Invalid(OmiRingProtocol.Failure.CHANGED);
            Outbox.Item existing = pending.stream().filter(item -> item.id().equals(id)).findFirst().orElse(null);
            if (existing != null) {
                if (!existing.kind().equals("audio") || !existing.destination().equals(destination)
                    || !source.equals(existing.data().optString("source_id")) || !original.exists()
                    || !existing.data().optString("source_kind").equals("manual_upload")
                    || existing.data().getInt("codec_id") != codec
                    || !existing.data().getString("started_at").equals(record.capturedAt().toString()))
                    throw new OmiRingProtocol.Invalid(OmiRingProtocol.Failure.CHANGED);
            }
            if (record.sequence() < savedCursor && existing == null) continue;
            OmiFrames.Decoder decoder = new OmiFrames.Decoder(codec);
            ByteArrayOutputStream pcm = new ByteArrayOutputStream(), frames = new ByteArrayOutputStream();
            for (byte[] frame : record.frames()) {
                pcm.write(decoder.decode(frame));
                frames.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(frame.length).array()); frames.write(frame);
            }
            if (pcm.size() == 0 || pcm.size() > Outbox.CHUNK_BYTES) throw new OmiRingProtocol.Invalid(OmiRingProtocol.Failure.TRANSFER);
            Instant end = record.capturedAt().plusNanos(pcm.size() / 2L * 62500);
            if (existing != null) {
                File sourceFrames = new File(directory, id + ".frames");
                if (!existing.data().getString("ended_at").equals(end.toString())
                    || !Arrays.equals(Files.readAllBytes(existing.file().toPath()), wav(pcm.toByteArray()))
                    || !sourceFrames.isFile() || !Arrays.equals(Files.readAllBytes(sourceFrames.toPath()), frames.toByteArray()))
                    throw new OmiRingProtocol.Invalid(OmiRingProtocol.Failure.CHANGED);
                continue;
            }
            for (Outbox.Item item : pending) if (item.kind().equals("audio") && item.destination().equals(destination)
                    && item.data().getString("device_id").equals(device) && !item.data().optString("source_kind").equals("manual_upload")
                    && Instant.parse(item.data().getString("started_at")).isBefore(end.plusSeconds(1))
                    && Instant.parse(item.data().getString("ended_at")).isAfter(record.capturedAt().minusSeconds(1)))
                throw new OmiRingProtocol.Invalid(OmiRingProtocol.Failure.OVERLAP);
            outbox.checkSpace(record.raw().length + frames.size() + pcm.size() + 8192L);
            byte[] wav = wav(pcm.toByteArray());
            Outbox.atomic(original, record.raw());
            Outbox.atomic(new File(directory, id + ".frames"), frames.toByteArray());
            Outbox.atomic(new File(directory, id + ".wav"), wav);
            Json data = new Json().put("device_id", device).put("started_at", record.capturedAt().toString())
                .put("ended_at", end.toString()).put("codec_id", codec).put("source_kind", "manual_upload").put("source_id", source);
            Json manifest = new Json().put("id", id).put("kind", "audio").put("profile", destination.profile())
                .put("endpoints", new Json.JsonArray(destination.endpoints())).put("data", data);
            Outbox.atomic(new File(directory, id + ".json"), manifest.toString().getBytes(StandardCharsets.UTF_8));
            syncDirectory(); count++;
        }
        long next = Math.max(cursor(destination, device, ring), records.get(records.size() - 1).sequence() + 1);
        Outbox.atomic(receipt(destination, device, ring), Long.toString(next).getBytes(StandardCharsets.US_ASCII));
        syncDirectory(); return count;
    }
    private static byte[] wav(byte[] pcm) {
        ByteBuffer bytes = ByteBuffer.allocate(44 + pcm.length).order(ByteOrder.LITTLE_ENDIAN);
        bytes.put("RIFF".getBytes(StandardCharsets.US_ASCII)).putInt(36 + pcm.length).put("WAVEfmt ".getBytes(StandardCharsets.US_ASCII))
            .putInt(16).putShort((short) 1).putShort((short) 1).putInt(16000).putInt(32000).putShort((short) 2).putShort((short) 16)
            .put("data".getBytes(StandardCharsets.US_ASCII)).putInt(pcm.length).put(pcm);
        return bytes.array();
    }
}

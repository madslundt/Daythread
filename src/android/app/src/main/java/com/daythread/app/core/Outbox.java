package com.daythread.app.core;

import java.io.*;
import com.daythread.app.core.Json.JsonArray;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.*;

/** One immutable manifest per item. Audio is committed before its manifest; no shared index to lose. */
public final class Outbox {
    public static final long BUDGET = 2_000_000_000L;
    public static final int CHUNK_BYTES = 30 * 32000;
    private final File directory;
    private final Set<String> openWriters = new HashSet<>();
    public Outbox(File directory) throws IOException {
        this.directory = directory;
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("Cannot open recording storage");
    }
    public record Item(String id, String kind, Destination destination, Json data, File file, boolean clickPriority, List<String> audioDependencies) {}
    public synchronized boolean hasPending() throws IOException {
        for (File file : files()) if (file.getName().endsWith(".json")) return true;
        return false;
    }
    public synchronized int pendingCount() throws IOException {
        int count = 0;
        for (File file : files()) if (file.getName().endsWith(".json")) count++;
        return count;
    }
    public synchronized List<Item> pending() throws IOException {
        List<Item> items = new ArrayList<>();
        for (File file : files()) if (file.getName().endsWith(".json")) {
            try {
                Json json = new Json(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
                String id = json.getString("id"); UUID.fromString(id);
                if (!file.getName().equals(id + ".json")) throw new IOException("Queued recording identity is inconsistent");
                List<String> endpoints = new ArrayList<>();
                JsonArray array = json.getJsonArray("endpoints");
                for (int i = 0; i < array.length(); i++) endpoints.add(array.getString(i));
                String kind = json.getString("kind");
                if (!Set.of("audio", "event", "gap", "state", "generation").contains(kind)) throw new IOException("Unknown queued item");
                if (json.has("awaiting_audio") && Boolean.TRUE.equals(json.get("awaiting_audio"))) continue;
                File audio = new File(directory, id + ".wav");
                if (kind.equals("audio") && !audio.isFile()) throw new IOException("Queued audio is missing");
                boolean clickPriority = json.has("click_priority") && Boolean.TRUE.equals(json.get("click_priority"));
                List<String> dependencies = null;
                if (json.has("audio_dependencies")) {
                    dependencies = new ArrayList<>(); JsonArray arrayIDs = json.getJsonArray("audio_dependencies");
                    for (int i = 0; i < arrayIDs.length(); i++) dependencies.add(UUID.fromString(arrayIDs.getString(i)).toString());
                    dependencies = List.copyOf(dependencies);
                }
                items.add(new Item(id, kind, new Destination(endpoints, json.getString("profile")), json.getJson("data"), audio, clickPriority, dependencies));
            } catch (RuntimeException e) { throw new IOException("Cannot read saved recording details; files were preserved", e); }
        }
        items.sort(Comparator.comparing((Item item) -> item.kind.equals("generation")
            ? Instant.parse(item.data.getJson("parameters").getString("end"))
            : Instant.parse(item.data.optString("started_at", item.data.optString("occurred_at"))))
            .thenComparing(Item::id));
        return items;
    }
    /** Click hints need no audio; recent actions promote only their own captured evidence. */
    public synchronized List<Item> uploadPlan() throws IOException {
        List<Item> chronological = pending();
        LinkedHashMap<String, Item> plan = new LinkedHashMap<>();
        for (Item item : chronological)
            if (item.kind.equals("event") || item.kind.equals("state") && item.clickPriority) plan.put(item.id, item);
        for (Item request : chronological) {
            if (!request.kind.equals("generation") || !request.data.getString("kind").equals("wearable_action")) continue;
            Json parameters = request.data.getJson("parameters");
            Instant end = Instant.parse(parameters.getString("end"));
            Instant start = end.minusSeconds(parameters.getInt("duration_seconds"));
            String device = parameters.optString("device_id");
            for (Item audio : chronological) {
                if (!audio.kind.equals("audio") || !audio.destination.equals(request.destination)
                    || !audio.data.getString("device_id").equals(device)) continue;
                if (request.audioDependencies != null && !request.audioDependencies.contains(audio.id)) continue;
                if (Instant.parse(audio.data.getString("started_at")).isBefore(end)
                    && Instant.parse(audio.data.getString("ended_at")).isAfter(start))
                    plan.putIfAbsent(audio.id, audio);
            }
            plan.putIfAbsent(request.id, request);
        }
        for (Item item : chronological) plan.putIfAbsent(item.id, item);
        return new ArrayList<>(plan.values());
    }
    public synchronized long bytes() throws IOException {
        long size = 0;
        for (File file : files()) size += file.length();
        return size;
    }
    private File[] files() throws IOException {
        File[] files = directory.listFiles();
        if (files == null) throw new IOException("Cannot read recording storage");
        return files;
    }
    public synchronized void checkSpace(long additional) throws IOException {
        if (bytes() + additional > BUDGET || directory.getUsableSpace() < additional + 10_000_000)
            throw new IOException("Recording storage is full. Unsent audio was preserved. Upload before resuming.");
    }
    private static Json manifest(String id, String kind, Destination destination, Json data) {
        return new Json().put("id", id).put("kind", kind).put("profile", destination.profile())
            .put("endpoints", new JsonArray(destination.endpoints())).put("data", data);
    }
    public synchronized String enqueue(String kind, Destination destination, Json data) throws IOException {
        return enqueue(kind, destination, data, false);
    }
    public synchronized String enqueue(String kind, Destination destination, Json data, boolean clickPriority) throws IOException {
        return enqueue(kind, destination, data, clickPriority, false);
    }
    public synchronized String stageRecentAction(Destination destination, Json request) throws IOException {
        return stageRecentAction(destination, request, null);
    }
    public synchronized String stageRecentAction(Destination destination, Json request, Instant prefixEnd) throws IOException {
        return enqueue("generation", destination, request, false, true, prefixEnd);
    }
    private String enqueue(String kind, Destination destination, Json data, boolean clickPriority, boolean awaitingAudio) throws IOException {
        return enqueue(kind, destination, data, clickPriority, awaitingAudio, null);
    }
    private String enqueue(String kind, Destination destination, Json data, boolean clickPriority, boolean awaitingAudio, Instant prefixEnd) throws IOException {
        checkSpace(8192);
        String id = UUID.randomUUID().toString();
        String key = switch (kind) { case "event" -> "event_id"; case "gap" -> "gap_id"; case "generation" -> "id"; default -> null; };
        if (key != null) data.put(key, id);
        Json saved = manifest(id, kind, destination, data);
        if (clickPriority) saved.put("click_priority", true);
        if (awaitingAudio) {
            Json parameters = data.getJson("parameters"); Instant end = Instant.parse(parameters.getString("end"));
            Instant start = prefixEnd != null && prefixEnd.isBefore(end) ? prefixEnd : end.minusMillis(20);
            String gapID = UUID.nameUUIDFromBytes((id + ":pending-audio").getBytes(StandardCharsets.UTF_8)).toString();
            saved.put("awaiting_audio", true).put("pending_audio_gap", new Json().put("gap_id", gapID)
                .put("device_id", parameters.getString("device_id")).put("started_at", start.toString()).put("ended_at", end.toString())
                .put("cause", "incomplete_frame").put("reason", "The final pre-click audio frame was unavailable when this request was released"));
        }
        else if (kind.equals("generation")) freezeDependencies(saved, destination);
        atomic(new File(directory, id + ".json"), saved.toString().getBytes(StandardCharsets.UTF_8));
        return id;
    }
    private void freezeDependencies(Json saved, Destination destination) throws IOException {
        Json parameters = saved.getJson("data").getJson("parameters");
        Instant end = Instant.parse(parameters.getString("end")), start = end.minusSeconds(parameters.getInt("duration_seconds"));
        ArrayList<String> dependencies = new ArrayList<>();
        for (Item audio : pending()) if (audio.kind.equals("audio") && audio.destination.equals(destination)
            && audio.data.getString("device_id").equals(parameters.getString("device_id"))
            && Instant.parse(audio.data.getString("started_at")).isBefore(end)
            && Instant.parse(audio.data.getString("ended_at")).isAfter(start)) dependencies.add(audio.id);
        saved.put("audio_dependencies", new JsonArray(dependencies));
    }
    public synchronized void releaseRecentAction(String id) throws IOException {
        releaseRecentAction(id, false);
    }
    public synchronized void releaseRecentAction(String id, boolean incomplete) throws IOException {
        UUID.fromString(id);
        File file = new File(directory, id + ".json");
        Json saved = new Json(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
        if (!id.equals(saved.getString("id")) || !"generation".equals(saved.getString("kind"))) throw new IOException("Queued request identity is inconsistent");
        if (!saved.has("awaiting_audio") || !Boolean.TRUE.equals(saved.get("awaiting_audio"))) return;
        ArrayList<String> endpoints = new ArrayList<>(); JsonArray origins = saved.getJsonArray("endpoints");
        for (int i = 0; i < origins.length(); i++) endpoints.add(origins.getString(i));
        Destination destination = new Destination(endpoints, saved.getString("profile"));
        freezeDependencies(saved, destination);
        if (incomplete && saved.has("pending_audio_gap")) {
            Json gap = saved.getJson("pending_audio_gap"); String gapID = gap.getString("gap_id");
            atomic(new File(directory, gapID + ".json"), manifest(gapID, "gap", destination, gap).toString().getBytes(StandardCharsets.UTF_8));
        }
        saved.put("awaiting_audio", false);
        atomic(file, saved.toString().getBytes(StandardCharsets.UTF_8));
    }
    public synchronized void acknowledge(Item item, Json receipt, String responseProfile) throws IOException {
        Receipt.validate(item, receipt, responseProfile);
        // Remove the commit marker first: a crash can leave harmless orphan audio, never a missing pending file.
        Files.delete(new File(directory, item.id + ".json").toPath());
        Files.deleteIfExists(item.file.toPath());
        Files.deleteIfExists(new File(directory, item.id + ".frames").toPath());
    }
    public synchronized Writer begin(Destination destination, String device, Instant started) throws IOException {
        checkSpace(CHUNK_BYTES * 3L);
        return new Writer(UUID.randomUUID().toString(), destination, device, started, null);
    }
    public synchronized Writer begin(Destination destination, String device, Instant started, int codec) throws IOException {
        checkSpace(CHUNK_BYTES * 3L);
        return new Writer(UUID.randomUUID().toString(), destination, device, started, codec);
    }
    public synchronized int recover() throws IOException {
        int recovered = 0;
        for (File file : files()) if (file.getName().endsWith(".capture")) {
            Json saved = new Json(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
            String id = saved.getString("id"); UUID.fromString(id);
            if (openWriters.contains(id)) continue;
            File raw = new File(directory, id + ".pcm");
            if (raw.length() >= 2) {
                // Journal the unknown interval before committing so a second crash reuses its ID and bounds.
                Instant end = Instant.parse(saved.getJson("data").getString("started_at")).plusNanos(raw.length() / 2L * 62500);
                if (!saved.has("recovery_gap") && Instant.now().isAfter(end)) {
                    saved.put("recovery_gap", new Json().put("gap_id", UUID.nameUUIDFromBytes((id + ":recovery").getBytes(StandardCharsets.UTF_8)).toString())
                        .put("device_id", saved.getJson("data").getString("device_id")).put("started_at", end.toString())
                        .put("ended_at", Instant.now().toString()).put("cause", "delivery_interrupted").put("reason", "Android capture ended before app restart"));
                    atomic(file, saved.toString().getBytes(StandardCharsets.UTF_8));
                }
                if (!new File(directory, id + ".json").exists()) {
                    // Reuse the preallocated UUID, destination and sample duration after process death.
                    commit(saved, raw); recovered++;
                }
            }
            if (saved.has("recovery_gap")) {
                Json gap = saved.getJson("recovery_gap");
                Json queued = new Json(saved.toString()).put("id", gap.getString("gap_id")).put("kind", "gap").put("data", gap);
                atomic(new File(directory, gap.getString("gap_id") + ".json"), queued.toString().getBytes(StandardCharsets.UTF_8));
            }
            Files.deleteIfExists(raw.toPath()); Files.delete(file.toPath());
        }
        // A committed recording no longer has a .capture journal. Clean only unreferenced final files.
        for (File file : files()) if (file.getName().endsWith(".wav") || file.getName().endsWith(".frames")) {
            String id = file.getName().substring(0, file.getName().lastIndexOf('.'));
            if (!new File(directory, id + ".json").exists() && !new File(directory, id + ".capture").exists()) Files.delete(file.toPath());
        }
        // A process restart commits the recovered decoded prefix before releasing its job.
        if (openWriters.isEmpty()) for (File file : files()) if (file.getName().endsWith(".json")) {
            Json saved = new Json(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
            if (saved.has("awaiting_audio") && Boolean.TRUE.equals(saved.get("awaiting_audio"))) releaseRecentAction(saved.getString("id"), true);
        }
        return recovered;
    }
    private void commit(Json saved, File raw) throws IOException {
        int length = (int) (raw.length() / 2 * 2);
        if (length <= 0 || length > CHUNK_BYTES) throw new IOException("Invalid recovered audio length");
        String id = saved.getString("id");
        ByteBuffer header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN);
        header.put("RIFF".getBytes(StandardCharsets.US_ASCII)).putInt(36 + length).put("WAVEfmt ".getBytes(StandardCharsets.US_ASCII))
            .putInt(16).putShort((short) 1).putShort((short) 1).putInt(16000).putInt(32000)
            .putShort((short) 2).putShort((short) 16).put("data".getBytes(StandardCharsets.US_ASCII)).putInt(length);
        File temporary = new File(directory, id + ".wav.tmp");
        try (FileOutputStream output = new FileOutputStream(temporary); FileInputStream input = new FileInputStream(raw)) {
            output.write(header.array()); output.write(Streams.read(input, length)); output.getFD().sync();
        }
        move(temporary, new File(directory, id + ".wav"));
        Json data = saved.getJson("data");
        data.put("ended_at", Instant.parse(data.getString("started_at")).plusNanos(length / 2L * 62500).toString());
        atomic(new File(directory, id + ".json"), saved.put("kind", "audio").toString().getBytes(StandardCharsets.UTF_8));
    }
    public static void atomic(File target, byte[] bytes) throws IOException {
        File temporary = new File(target.getParentFile(), target.getName() + ".tmp");
        try (FileOutputStream output = new FileOutputStream(temporary)) { output.write(bytes); output.getFD().sync(); }
        move(temporary, target);
    }
    private static void move(File source, File target) throws IOException {
        Files.move(source.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    }
    public final class Writer implements AutoCloseable {
        private final String id;
        private final File raw;
        private final Json saved;
        private final FileOutputStream output, originals;
        private int length, synced;
        private boolean closed;
        private Writer(String id, Destination destination, String device, Instant started, Integer codec) throws IOException {
            this.id = id; raw = new File(directory, id + ".pcm");
            saved = manifest(id, "audio", destination, new Json().put("device_id", device).put("started_at", started.toString()));
            if (codec != null) saved.getJson("data").put("codec_id", codec);
            atomic(new File(directory, id + ".capture"), saved.toString().getBytes(StandardCharsets.UTF_8));
            output = new FileOutputStream(raw); originals = new FileOutputStream(new File(directory, id + ".frames"));
            openWriters.add(id);
        }
        public int remaining() { return CHUNK_BYTES - length; }
        public Instant end() { return Instant.parse(saved.getJson("data").getString("started_at")).plusNanos(length / 2L * 62500); }
        public void append(byte[] bytes, int offset, int count, byte[] original) throws IOException {
            synchronized (Outbox.this) {
                if (closed || count % 2 != 0 || count > remaining()) throw new IOException("Invalid capture write");
                if (length + count - synced >= 32000) checkSpace(count + (original == null ? 0 : original.length + 4L));
                if (original != null) {
                    originals.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(original.length).array());
                    originals.write(original);
                }
                output.write(bytes, offset, count); length += count;
                if (length - synced >= 32000) { output.getFD().sync(); originals.getFD().sync(); synced = length; }
            }
        }
        @Override public void close() throws IOException {
            synchronized (Outbox.this) {
                if (closed) return;
                closed = true;
                try { output.getFD().sync(); originals.getFD().sync(); } finally { output.close(); originals.close(); openWriters.remove(id); }
                if (length > 0) commit(saved, raw);
                Files.deleteIfExists(raw.toPath()); Files.deleteIfExists(new File(directory, id + ".capture").toPath());
                if (length == 0) Files.deleteIfExists(new File(directory, id + ".frames").toPath());
            }
        }
    }
}

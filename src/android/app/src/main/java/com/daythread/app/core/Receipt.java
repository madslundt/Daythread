package com.daythread.app.core;

import java.io.IOException;
import java.time.Instant;
import java.util.Set;
import java.util.UUID;

public final class Receipt {
    private Receipt() {}
    public static void validate(Outbox.Item item, Json value, String profile) throws IOException {
        try {
            if (!item.destination().profile().equals(profile)) throw new IllegalArgumentException();
            Json expected = item.data();
            String idKey = switch (item.kind()) { case "audio" -> "id"; case "event" -> "event_id"; case "gap" -> "gap_id"; default -> "event_id"; };
            if (!UUID.fromString(item.id()).equals(UUID.fromString(value.getString(idKey)))) throw new IllegalArgumentException();
            for (String key : switch (item.kind()) {
                case "audio" -> new String[]{"device_id", "started_at", "ended_at"};
                case "event" -> new String[]{"device_id", "occurred_at", "action", "meeting_max_minutes", "meeting_min_seconds", "marker_cooldown_seconds"};
                case "gap" -> new String[]{"device_id", "started_at", "ended_at", "cause"};
                default -> new String[]{"phone_id", "occurred_at", "state", "cause"};
            }) {
                if (key.endsWith("_at")) {
                    if (Math.abs(Instant.parse(value.getString(key)).toEpochMilli() - Instant.parse(expected.getString(key)).toEpochMilli()) >= 2)
                        throw new IllegalArgumentException();
                } else if (!expected.get(key).toString().equals(value.get(key).toString())) throw new IllegalArgumentException();
            }
            if (item.kind().equals("audio") && !Set.of("queued", "transcribing", "done", "failed", "expired", "blocked").contains(value.getString("state")))
                throw new IllegalArgumentException();
        } catch (RuntimeException e) { throw new IOException("Server receipt did not match. Original recording retained.", e); }
    }
}

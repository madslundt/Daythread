package com.daythread.app.core;

import java.io.File;
import java.time.Instant;
import java.util.*;
import java.util.stream.Collectors;
import org.junit.*;
import org.junit.rules.TemporaryFolder;
import static org.junit.Assert.*;

/** Real persisted manifests model the backlog waiting when a pendant click arrives. */
public class OutboxPriorityTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();
    private final Destination home = new Destination(List.of("https://home.example"), "default");
    private final Instant start = Instant.parse("2026-10-31T10:00:00Z");
    private String audio(Outbox outbox, Destination destination, String device, int offset, int seconds) throws Exception {
        Set<String> before = outbox.pending().stream().map(Outbox.Item::id).collect(Collectors.toSet());
        try (Outbox.Writer writer = outbox.begin(destination, device, start.plusSeconds(offset))) {
            byte[] bytes = new byte[seconds * 32000]; writer.append(bytes, 0, bytes.length, null);
        }
        return outbox.pending().stream().map(Outbox.Item::id).filter(id -> !before.contains(id)).findFirst().orElseThrow();
    }
    private String click(Outbox outbox, String action, int offset) throws Exception {
        return outbox.enqueue("event", home, new Json().put("action", action).put("device_id", "omi-test")
            .put("occurred_at", start.plusSeconds(offset).toString()));
    }
    private List<String> ids(Outbox outbox) throws Exception { return outbox.uploadPlan().stream().map(Outbox.Item::id).toList(); }
    @Test public void marksAndMeetingsBypassOlderAudioGapsAndStateInCaptureOrder() throws Exception {
        Outbox outbox = new Outbox(temporary.newFolder());
        ArrayList<String> backlog = new ArrayList<>();
        for (int offset = 0; offset < 240; offset += 30) backlog.add(audio(outbox, home, "omi-test", offset, 30));
        outbox.enqueue("gap", home, new Json().put("started_at", start.minusSeconds(60).toString())
            .put("ended_at", start.toString()).put("device_id", "omi-test").put("cause", "connection"));
        outbox.enqueue("state", home, new Json().put("occurred_at", start.minusSeconds(1).toString())
            .put("phone_id", "phone").put("state", "capturing").put("cause", "resumed"));
        String mark = click(outbox, "mark", 240), meeting = click(outbox, "meeting_toggle", 241);
        List<String> plan = ids(outbox);
        assertEquals(List.of(mark, meeting), plan.subList(0, 2));
        assertEquals(12, plan.size()); assertTrue(plan.containsAll(backlog));
    }
    @Test public void recentActionPromotesOnlyItsOriginalWindowDeviceProfileAndArchive() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        String old = audio(outbox, home, "omi-test", 0, 30);
        String touchesLeft = audio(outbox, home, "omi-test", 180, 30);
        String otherDevice = audio(outbox, home, "other-device", 210, 30);
        String otherProfile = audio(outbox, new Destination(home.endpoints(), "other"), "omi-test", 210, 30);
        String otherArchive = audio(outbox, new Destination(List.of("https://other.example"), "default"), "omi-test", 210, 30);
        String left = audio(outbox, home, "omi-test", 200, 20);
        String right = audio(outbox, home, "omi-test", 220, 20);
        String touchesRight = audio(outbox, home, "omi-test", 240, 30);
        String job = outbox.enqueue("generation", home, WearableAction.ANSWER.request(30, start.plusSeconds(240), "omi-test"));
        List<String> plan = ids(outbox);
        assertEquals(List.of(left, right, job), plan.subList(0, 3));
        assertEquals(Set.of(old, touchesLeft, otherDevice, otherProfile, otherArchive, touchesRight), new HashSet<>(plan.subList(3, plan.size())));
        List<Outbox.Item> restored = new Outbox(directory).uploadPlan();
        assertEquals(plan, restored.stream().map(Outbox.Item::id).toList());
        Outbox.Item request = restored.get(2);
        assertEquals(home, request.destination()); assertEquals(job, request.data().getString("id"));
        assertEquals(start.plusSeconds(240).toString(), request.data().getJson("parameters").getString("end"));
    }
    @Test public void multipleActionsShareDependenciesWithoutDuplicateUploads() throws Exception {
        Outbox outbox = new Outbox(temporary.newFolder());
        String old = audio(outbox, home, "omi-test", 0, 30);
        String first = audio(outbox, home, "omi-test", 30, 30);
        String second = audio(outbox, home, "omi-test", 60, 30);
        String answer = outbox.enqueue("generation", home, WearableAction.ANSWER.request(30, start.plusSeconds(60), "omi-test"));
        String summary = outbox.enqueue("generation", home, WearableAction.SUMMARIZE.request(60, start.plusSeconds(90), "omi-test"));
        assertEquals(List.of(first, answer, second, summary, old), ids(outbox));
    }
    @Test public void aClickArrivingDuringBacklogAppearsFirstInTheRefreshedPlan() throws Exception {
        Outbox outbox = new Outbox(temporary.newFolder());
        String first = audio(outbox, home, "omi-test", 0, 30);
        String second = audio(outbox, home, "omi-test", 30, 30);
        assertEquals(List.of(first, second), ids(outbox));
        String click = click(outbox, "mark", 70);
        Outbox.Item inFlight = outbox.pending().stream().filter(item -> item.id().equals(first)).findFirst().orElseThrow();
        outbox.acknowledge(inFlight, new Json(inFlight.data().toString()).put("id", first).put("state", "queued"), "default");
        assertEquals(List.of(click, second), ids(outbox));
    }
    @Test public void clickCaptureStateIsPrioritizedWithoutPromotingRoutineStatusOrChangingWireData() throws Exception {
        File directory = temporary.newFolder(); Outbox outbox = new Outbox(directory);
        String audio = audio(outbox, home, "omi-test", 0, 30);
        Json routine = new Json().put("phone_id", "phone").put("occurred_at", start.minusSeconds(1).toString())
            .put("state", "capturing").put("cause", "resumed");
        String status = outbox.enqueue("state", home, routine);
        Json paused = new Json().put("phone_id", "phone").put("occurred_at", start.plusSeconds(30).toString())
            .put("state", "paused").put("cause", "user_pause");
        String click = outbox.enqueue("state", home, paused, true);
        List<Outbox.Item> restored = new Outbox(directory).uploadPlan();
        assertEquals(List.of(click, status, audio), restored.stream().map(Outbox.Item::id).toList());
        assertTrue(restored.get(0).clickPriority()); assertFalse(restored.get(1).clickPriority());
        assertEquals(paused.toString(), restored.get(0).data().toString());
        assertFalse(restored.get(0).data().has("click_priority"));
    }
}

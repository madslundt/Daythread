package com.daythread.app;

import com.daythread.app.core.ClickFeedback;
import com.daythread.app.core.WearableAction;
import org.junit.Test;
import static org.junit.Assert.*;

/** Activity-title selection is independent of Android notification delivery. */
public class ClickNotificationsTest {
    private int title(WearableAction action, ClickFeedback.Outcome outcome, boolean capturing) {
        return ClickNotifications.titleResource(ClickFeedback.message(action, outcome, capturing));
    }
    @Test public void acceptedClicksUseActivityTitlesAndCaptureToggleDescribesItsDirection() {
        assertEquals(R.string.important, title(WearableAction.MARK, ClickFeedback.Outcome.ACCEPTED, true));
        assertEquals(R.string.meeting_action, title(WearableAction.MEETING, ClickFeedback.Outcome.ACCEPTED, true));
        assertEquals(R.string.summarize_recent, title(WearableAction.SUMMARIZE, ClickFeedback.Outcome.ACCEPTED, true));
        assertEquals(R.string.answer_recent, title(WearableAction.ANSWER, ClickFeedback.Outcome.ACCEPTED, true));
        assertEquals(R.string.translate_recent, title(WearableAction.TRANSLATE, ClickFeedback.Outcome.ACCEPTED, true));
        assertEquals(R.string.click_pausing, title(WearableAction.CAPTURE, ClickFeedback.Outcome.ACCEPTED, true));
        assertEquals(R.string.click_resuming, title(WearableAction.CAPTURE, ClickFeedback.Outcome.ACCEPTED, false));
    }
    @Test public void noneAndFailuresHaveHonestTitlesAndNoTitleIsOnlyAClickCounter() {
        assertEquals(R.string.click_no_action, title(WearableAction.NONE, ClickFeedback.Outcome.ACCEPTED, true));
        assertEquals(R.string.action_failed, title(WearableAction.ANSWER, ClickFeedback.Outcome.FAILED, true));
        assertEquals(R.string.click_unavailable, title(WearableAction.MARK, ClickFeedback.Outcome.UNAVAILABLE, false));
        assertEquals(R.string.capture_paused_controls, title(WearableAction.MEETING, ClickFeedback.Outcome.PAUSED, false));
        for (ClickFeedback.Message message : ClickFeedback.Message.values()) {
            assertNotEquals(R.string.single_click_registered, ClickNotifications.titleResource(message));
            assertNotEquals(R.string.double_click_registered, ClickNotifications.titleResource(message));
        }
    }
}

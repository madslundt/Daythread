package com.daythread.app;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.os.Build;
import com.daythread.app.core.ClickFeedback;
import com.daythread.app.core.WearableAction;
import java.util.UUID;

/** BLE-only, offline feedback. Job acknowledgements share their result notification identity. */
final class ClickNotifications {
    static final String SETTING = "click_notifications_enabled";
    static final int NOTIFICATION_ID = 1704;
    private ClickNotifications() {}
    static boolean enabled(Context context) {
        SharedPreferences prefs = AppState.preferences(context);
        return ClickFeedback.enabled(prefs.contains(SETTING) ? prefs.getBoolean(SETTING, ClickFeedback.DEFAULT_ENABLED) : null);
    }
    static boolean permitted(Context context) {
        try {
            NotificationManager manager = context.getSystemService(NotificationManager.class);
            NotificationChannel channel = manager.getNotificationChannel("wearable-clicks");
            return (Build.VERSION.SDK_INT < 33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)
                && manager.areNotificationsEnabled() && (channel == null || channel.getImportance() != NotificationManager.IMPORTANCE_NONE);
        } catch (RuntimeException ignored) { return false; }
    }
    static void show(Context context, int clickCode, WearableAction action, int seconds,
                     ClickFeedback.Outcome outcome, boolean wasCapturing, String jobID) {
        try { present(context, clickCode, action, seconds, outcome, wasCapturing, jobID); }
        catch (RuntimeException ignored) { /* Notification policy changes must not stop capture. */ }
    }
    private static void present(Context context, int clickCode, WearableAction action, int seconds,
                                ClickFeedback.Outcome outcome, boolean wasCapturing, String jobID) {
        if ((clickCode != 1 && clickCode != 2) || !enabled(context) || !permitted(context)) return;
        if (Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        ClickFeedback.Message message = ClickFeedback.message(action, outcome, wasCapturing);
        String body = switch (message) {
            case SUMMARIZING -> context.getString(R.string.click_summarizing, seconds);
            case ANSWERING -> context.getString(R.string.click_answering, seconds);
            case TRANSLATING -> context.getString(R.string.click_translating, seconds);
            case MARKING -> context.getString(R.string.click_marking);
            case MEETING -> context.getString(R.string.click_meeting);
            case PAUSING -> context.getString(R.string.click_pausing);
            case RESUMING -> context.getString(R.string.click_resuming);
            case NONE -> context.getString(R.string.click_no_action);
            case PAUSED -> context.getString(R.string.click_paused);
            case UNAVAILABLE -> context.getString(R.string.click_unavailable);
            case FAILED -> context.getString(R.string.click_failed);
        };
        String id = jobID == null ? UUID.randomUUID().toString() : jobID;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("wearable-clicks", context.getString(R.string.click_notifications), NotificationManager.IMPORTANCE_DEFAULT));
        Intent intent = new Intent(context, MainActivity.class).setAction("wearable-click:" + id).putExtra("capture", true);
        PendingIntent open = PendingIntent.getActivity(context, 0, intent, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        manager.notify(id, NOTIFICATION_ID, new Notification.Builder(context, "wearable-clicks").setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(context.getString(clickCode == 1 ? R.string.single_click_registered : R.string.double_click_registered))
            .setContentText(body).setStyle(new Notification.BigTextStyle().bigText(body)).setOnlyAlertOnce(true)
            .setContentIntent(open).setAutoCancel(true).build());
    }
}

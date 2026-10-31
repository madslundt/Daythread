package com.daythread.app;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.os.Build;
import com.daythread.app.core.*;
import java.util.ArrayList;
import java.util.Map;

/** Persist originated jobs separately from upload receipts; each poll uses its original archive. */
final class ActionResults {
    private static final String PREFIX = "wearable_job.";
    private ActionResults() {}
    static boolean pending(Context context) {
        return AppState.preferences(context).getAll().keySet().stream().anyMatch(key -> key.startsWith(PREFIX));
    }
    static void track(Context context, Outbox.Item item) {
        if (!item.kind().equals("generation")) return;
        Json saved = new Json().put("endpoints", new Json.JsonArray(item.destination().endpoints()))
            .put("profile", item.destination().profile()).put("action", item.data().getJson("parameters").getString("action"));
        AppState.preferences(context).edit().putString(PREFIX + item.id(), saved.toString()).commit();
    }
    static void poll(Context context) {
        for (Map.Entry<String, ?> entry : AppState.preferences(context).getAll().entrySet()) {
            if (!entry.getKey().startsWith(PREFIX) || !(entry.getValue() instanceof String text)) continue;
            try {
                Json saved = new Json(text); ArrayList<String> endpoints = new ArrayList<>();
                Json.JsonArray origins = saved.getJsonArray("endpoints");
                for (int i = 0; i < origins.length(); i++) endpoints.add(origins.getString(i));
                String id = entry.getKey().substring(PREFIX.length());
                Backend.Response response = Backend.request(new Destination(endpoints, saved.getString("profile")), "/v1/jobs/" + id, "GET", null, null);
                Json job = response.json();
                if (!saved.getString("profile").equals(response.profile()) || !id.equals(job.getString("id")) || !"wearable_action".equals(job.getString("kind"))) continue;
                String state = job.getString("state");
                if (!state.equals("completed") && !state.equals("failed")) continue;
                String title = context.getString(state.equals("failed") ? R.string.action_failed : WearableControls.label(WearableAction.stored(saved.getString("action"), WearableAction.NONE)));
                WearableResultPreview preview = state.equals("completed") ? WearableResultPreview.from(saved.getString("action"), job.getJson("result"), context.getString(R.string.action_result_ready)) : null;
                if (preview != null && preview.questionCount() != null)
                    title = preview.questionCount() == 0 ? context.getString(R.string.no_recent_question_count, 0)
                        : context.getResources().getQuantityString(R.plurals.latest_answer_question_count, preview.questionCount(), preview.questionCount());
                String body = preview == null ? job.optString("error", context.getString(R.string.action_failed)) : preview.text();
                if (state.equals("completed") && job.getJson("result").has("approximate_window") && job.getJson("result").get("approximate_window").toString().equals("true"))
                    body += "\n\n" + context.getString(R.string.approximate_audio_window);
                show(context, id, title, body);
                AppState.preferences(context).edit().remove(entry.getKey()).commit();
            } catch (Exception ignored) {
                // A job may not have uploaded yet, or the original archive may be offline.
            }
        }
    }
    private static void show(Context context, String id, String title, String body) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("wearable-results", context.getString(R.string.action_results), NotificationManager.IMPORTANCE_DEFAULT));
        if (Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        String bounded = body.length() > 20000 ? body.substring(0, 20000) + "…" : body;
        Intent intent = new Intent(context, MainActivity.class).setAction("wearable-result:" + id)
            .putExtra("action_title", title).putExtra("action_text", bounded).putExtra("capture", true);
        PendingIntent open = PendingIntent.getActivity(context, 0, intent, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        manager.notify(id, ClickNotifications.NOTIFICATION_ID, new Notification.Builder(context, "wearable-results").setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title).setContentText(bounded).setStyle(new Notification.BigTextStyle().bigText(bounded))
            .setContentIntent(open).setOnlyAlertOnce(true).setAutoCancel(true).build());
    }
    static void open(Activity activity, Intent intent) {
        String body = intent.getStringExtra("action_text");
        if (body == null) return;
        new AlertDialog.Builder(activity).setTitle(intent.getStringExtra("action_title")).setMessage(body).setPositiveButton(android.R.string.ok, null).show();
        intent.removeExtra("action_text"); intent.removeExtra("action_title");
    }
}

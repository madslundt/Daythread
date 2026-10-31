package com.daythread.app;

import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Switch;
import android.content.Intent;
import android.provider.Settings;
import com.daythread.app.core.WearableAction;
import java.util.Arrays;

/** Device-local mappings are read at each click, so changes apply to a live connection. */
final class WearableControls {
    private WearableControls() {}
    static WearableAction resolve(Context context, byte[] bytes) {
        SharedPreferences prefs = AppState.preferences(context);
        return WearableAction.resolve(bytes,
            WearableAction.stored(prefs.getString("single_click_action", "mark"), WearableAction.MARK),
            WearableAction.stored(prefs.getString("double_click_action", "meeting_toggle"), WearableAction.MEETING));
    }
    static int duration(Context context) { return WearableAction.duration(AppState.preferences(context).getInt("recent_audio_seconds", 30)); }
    static void add(Context context, LinearLayout page) {
        TextView title = new TextView(context); title.setText(R.string.wearable_controls); title.setTextSize(18);
        title.setPadding(0, 24, 0, 10); page.addView(title);
        mapping(context, page, "single_click_action", R.string.single_click, WearableAction.MARK);
        mapping(context, page, "double_click_action", R.string.double_click, WearableAction.MEETING);
        Button duration = new Button(context); duration.setAllCaps(false); page.addView(duration);
        Runnable refresh = () -> duration.setText(context.getString(R.string.recent_audio_duration, duration(context)));
        refresh.run();
        duration.setOnClickListener(view -> {
            int[] values = {15, 30, 60, 120, 300};
            String[] labels = Arrays.stream(values).mapToObj(value -> context.getString(R.string.duration_seconds, value)).toArray(String[]::new);
            int selected = 1; for (int i = 0; i < values.length; i++) if (values[i] == duration(context)) selected = i;
            new AlertDialog.Builder(context).setTitle(R.string.recent_audio).setSingleChoiceItems(labels, selected, (dialog, which) -> {
                AppState.preferences(context).edit().putInt("recent_audio_seconds", values[which]).apply(); refresh.run(); dialog.dismiss();
            }).setNegativeButton(R.string.cancel, null).show();
        });
        Switch notifications = new Switch(context); notifications.setText(R.string.click_notifications);
        notifications.setChecked(ClickNotifications.enabled(context)); notifications.setMinHeight((int) (48 * context.getResources().getDisplayMetrics().density)); page.addView(notifications);
        notifications.setOnCheckedChangeListener((view, checked) -> AppState.preferences(context).edit().putBoolean(ClickNotifications.SETTING, checked).apply());
        TextView notificationHelp = new TextView(context); notificationHelp.setText(R.string.click_notifications_help); page.addView(notificationHelp);
        if (!ClickNotifications.permitted(context)) {
            TextView permissionHelp = new TextView(context); permissionHelp.setText(R.string.click_notification_permission); page.addView(permissionHelp);
            Button settings = new Button(context); settings.setAllCaps(false); settings.setText(R.string.notification_settings); page.addView(settings);
            settings.setOnClickListener(view -> context.startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, context.getPackageName())));
        }
        TextView help = new TextView(context); help.setText(R.string.wearable_controls_help); help.setPadding(0, 8, 0, 16); page.addView(help);
    }
    private static void mapping(Context context, LinearLayout page, String key, int title, WearableAction fallback) {
        SharedPreferences prefs = AppState.preferences(context);
        Button button = new Button(context); button.setAllCaps(false); page.addView(button);
        Runnable refresh = () -> button.setText(context.getString(title) + ": " + context.getString(label(WearableAction.stored(prefs.getString(key, fallback.value), fallback))));
        refresh.run();
        button.setOnClickListener(view -> {
            WearableAction[] values = WearableAction.values();
            String[] labels = Arrays.stream(values).map(action -> context.getString(label(action))).toArray(String[]::new);
            int selected = WearableAction.stored(prefs.getString(key, fallback.value), fallback).ordinal();
            new AlertDialog.Builder(context).setTitle(title).setSingleChoiceItems(labels, selected, (dialog, which) -> {
                prefs.edit().putString(key, values[which].value).apply(); refresh.run(); dialog.dismiss();
            }).setNegativeButton(R.string.cancel, null).show();
        });
    }
    static int label(WearableAction action) {
        return switch (action) {
            case MARK -> R.string.important;
            case MEETING -> R.string.meeting_action;
            case SUMMARIZE -> R.string.summarize_recent;
            case ANSWER -> R.string.answer_recent;
            case TRANSLATE -> R.string.translate_recent;
            case CAPTURE -> R.string.start_pause;
            case NONE -> R.string.do_nothing;
        };
    }
}

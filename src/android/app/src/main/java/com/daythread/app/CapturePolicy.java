package com.daythread.app;

import android.content.Context;
import android.content.SharedPreferences;
import com.daythread.app.core.RecordingWindow;
import java.time.ZonedDateTime;

final class CapturePolicy {
    // Visible to queued audio callbacks as soon as the user presses Off.
    private static volatile boolean liveEnabled = true;
    static boolean liveEnabled() { return liveEnabled; }
    static RecordingWindow window(Context context) {
        SharedPreferences prefs = AppState.preferences(context);
        return new RecordingWindow(prefs.getBoolean("microphone_enabled", true), prefs.getBoolean("recording_scheduled", false),
            prefs.getBoolean("recording_override", false), prefs.getInt("recording_days", 31),
            prefs.getInt("recording_start", 540), prefs.getInt("recording_end", 1020));
    }
    static boolean allowed(Context context) { return window(context).allows(ZonedDateTime.now()); }
    static String source(Context context) { return AppState.preferences(context).getString("recording_source", "microphone"); }
    static String activeSource(Context context) {
        return AppState.preferences(context).getBoolean("recording_override", false)
            ? AppState.preferences(context).getString("recording_manual_source", source(context)) : source(context);
    }
    static boolean manual(Context context, boolean enabled, String source) {
        liveEnabled = enabled;
        return AppState.preferences(context).edit().putBoolean("microphone_enabled", enabled)
            .putBoolean("recording_override", true).putString("recording_manual_source", source).commit();
    }
    static void follow(Context context) {
        liveEnabled = true;
        AppState.preferences(context).edit().putBoolean("microphone_enabled", true).putBoolean("recording_override", false).commit();
    }
}

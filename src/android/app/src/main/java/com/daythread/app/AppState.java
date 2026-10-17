package com.daythread.app;

import android.content.Context;
import android.content.SharedPreferences;
import com.daythread.app.core.*;
import java.io.File;
import java.io.IOException;
import java.util.Arrays;
import java.util.UUID;

final class AppState {
    private static Outbox outbox;
    static SharedPreferences preferences(Context context) { return context.getSharedPreferences("daythread", Context.MODE_PRIVATE); }
    static synchronized Outbox outbox(Context context) throws IOException {
        if (outbox == null) {
            Outbox restored = new Outbox(new File(context.getNoBackupFilesDir(), "outbox"));
            restored.recover(); outbox = restored;
        }
        return outbox;
    }
    static Destination destination(Context context) {
        SharedPreferences preferences = preferences(context);
        return new Destination(Arrays.stream(preferences.getString("endpoints", "").split("\\s+")).filter(s -> !s.isBlank()).collect(java.util.stream.Collectors.toList()),
            preferences.getString("profile", "default"));
    }
    static String phone(Context context) {
        SharedPreferences preferences = preferences(context);
        String id = preferences.getString("phone", null);
        if (id == null) { id = UUID.randomUUID().toString(); preferences.edit().putString("phone", id).commit(); }
        return id;
    }
    static void error(Context context, String message) { preferences(context).edit().putString("error", message).apply(); }
}

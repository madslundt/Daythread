package com.daythread.app;

import android.app.job.*;
import android.content.*;
import android.net.*;
import com.daythread.app.core.Outbox;
import java.util.concurrent.atomic.AtomicBoolean;

public final class UploadJob extends JobService {
    private final AtomicBoolean cancelled = new AtomicBoolean();
    private static final AtomicBoolean running = new AtomicBoolean();
    static void schedule(Context context) {
        JobScheduler scheduler = context.getSystemService(JobScheduler.class);
        scheduler.schedule(new JobInfo.Builder(1701, new ComponentName(context, UploadJob.class))
            .setRequiredNetworkType(AppState.preferences(context).getBoolean("wifi", false) ? JobInfo.NETWORK_TYPE_UNMETERED : JobInfo.NETWORK_TYPE_ANY)
            .setPersisted(true).setBackoffCriteria(30000, JobInfo.BACKOFF_POLICY_EXPONENTIAL).build());
    }
    static boolean allowed(Context context) {
        ConnectivityManager manager = context.getSystemService(ConnectivityManager.class);
        NetworkCapabilities caps = manager.getNetworkCapabilities(manager.getActiveNetwork());
        return caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            && (!AppState.preferences(context).getBoolean("wifi", false) || caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED));
    }
    static void pump(Context context) {
        new Thread(() -> { if (!drain(context, new AtomicBoolean())) schedule(context); }, "daythread-upload").start();
    }
    private static boolean drain(Context context, AtomicBoolean cancelled) {
        if (!running.compareAndSet(false, true)) return false;
        try {
            Outbox outbox = AppState.outbox(context);
            for (Outbox.Item item : outbox.pending()) {
                if (cancelled.get() || !allowed(context)) return false;
                Backend.upload(outbox, item);
            }
            AppState.error(context, ""); return true;
        } catch (Exception e) { AppState.error(context, e.getMessage()); return false; }
        finally { running.set(false); }
    }
    @Override public boolean onStartJob(JobParameters params) {
        cancelled.set(false);
        new Thread(() -> jobFinished(params, !drain(this, cancelled)), "daythread-sync-job").start();
        return true;
    }
    @Override public boolean onStopJob(JobParameters params) { cancelled.set(true); return true; }
}

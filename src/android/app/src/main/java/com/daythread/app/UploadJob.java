package com.daythread.app;

import android.app.job.*;
import android.content.*;
import android.net.*;
import android.os.SystemClock;
import com.daythread.app.core.Outbox;
import com.daythread.app.core.RetryBackoff;
import java.util.concurrent.atomic.AtomicBoolean;

public final class UploadJob extends JobService {
    private final AtomicBoolean cancelled = new AtomicBoolean();
    private static final AtomicBoolean running = new AtomicBoolean();
    private static final AtomicBoolean requested = new AtomicBoolean();
    private static final RetryBackoff retries = new RetryBackoff(30000, 15 * 60 * 1000);
    static void schedule(Context context) {
        try { if (!AppState.outbox(context).hasPending() && !ActionResults.pending(context)) return; }
        catch (java.io.IOException e) { AppState.error(context, e.getMessage()); return; }
        JobScheduler scheduler = context.getSystemService(JobScheduler.class);
        int network = AppState.preferences(context).getBoolean("wifi", false) ? JobInfo.NETWORK_TYPE_UNMETERED : JobInfo.NETWORK_TYPE_ANY;
        JobInfo pending = scheduler.getPendingJob(1701);
        // Replacing an unchanged job resets Android's failure backoff at every audio chunk.
        if (pending != null && pending.getNetworkType() == network) return;
        scheduler.schedule(new JobInfo.Builder(1701, new ComponentName(context, UploadJob.class))
            .setRequiredNetworkType(network)
            .setPersisted(true).setBackoffCriteria(30000, JobInfo.BACKOFF_POLICY_EXPONENTIAL).build());
    }
    static boolean allowed(Context context) {
        ConnectivityManager manager = context.getSystemService(ConnectivityManager.class);
        NetworkCapabilities caps = manager.getNetworkCapabilities(manager.getActiveNetwork());
        return caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            && (!AppState.preferences(context).getBoolean("wifi", false) || caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED));
    }
    static void pump(Context context) {
        pump(context, false);
    }
    static void pump(Context context, boolean force) {
        if (force) retries.reset();
        if (!allowed(context) || !retries.ready(SystemClock.elapsedRealtime())) return;
        requested.set(true);
        if (!running.compareAndSet(false, true)) return;
        Context application = context.getApplicationContext();
        new Thread(() -> { if (!drain(application, new AtomicBoolean())) schedule(application); }, "daythread-upload").start();
    }
    private static boolean drain(Context context, AtomicBoolean cancelled) {
        try {
            Outbox outbox = AppState.outbox(context);
            java.util.HashSet<String> deferredJobs = new java.util.HashSet<>();
            java.io.IOException deferred = null;
            long nextResultPollAt = SystemClock.elapsedRealtime() + 10000;
            do {
                requested.set(false);
                for (Outbox.Item item : outbox.uploadPlan()) {
                    if (deferredJobs.contains(item.id())) continue;
                    if (cancelled.get() || !allowed(context)) return false;
                    ActionResults.track(context, item);
                    try { Backend.upload(outbox, item); }
                    catch (java.io.IOException error) {
                        // A disabled AI action must not strand later recordings behind it.
                        if (!item.kind().equals("generation")) throw error;
                        deferredJobs.add(item.id());
                        deferred = error;
                    }
                    // New work may be a pendant click. Refresh between transfers instead
                    // of draining an outdated backlog snapshot before noticing the click.
                    if (requested.get()) break;
                    if (SystemClock.elapsedRealtime() >= nextResultPollAt && ActionResults.pending(context)) {
                        ActionResults.poll(context);
                        nextResultPollAt = SystemClock.elapsedRealtime() + 10000;
                    }
                }
            } while (requested.get() && !cancelled.get());
            if (deferred != null) { ActionResults.poll(context); throw deferred; }
            ActionResults.poll(context);
            retries.reset(); AppState.error(context, ""); return !ActionResults.pending(context);
        } catch (Exception e) { retries.fail(SystemClock.elapsedRealtime()); AppState.error(context, e.getMessage()); return false; }
        finally {
            running.set(false);
            // A chunk can arrive between the last snapshot and releasing the worker slot.
            if (requested.get() && !cancelled.get()) pump(context);
        }
    }
    @Override public boolean onStartJob(JobParameters params) {
        requested.set(true);
        if (!running.compareAndSet(false, true)) return false;
        cancelled.set(false);
        new Thread(() -> jobFinished(params, !drain(this, cancelled)), "daythread-sync-job").start();
        return true;
    }
    @Override public boolean onStopJob(JobParameters params) { cancelled.set(true); return true; }
}

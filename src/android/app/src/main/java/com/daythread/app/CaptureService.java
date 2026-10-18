package com.daythread.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.media.*;
import android.os.*;
import com.daythread.app.core.*;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.time.Instant;
import java.time.ZonedDateTime;
import java.util.concurrent.atomic.AtomicBoolean;

public final class CaptureService extends Service {
    static volatile boolean active;
    static volatile String source = "";
    static volatile String status = "";
    static volatile String captureProfile = "";
    private final AtomicBoolean recording = new AtomicBoolean();
    private HandlerThread thread;
    private Handler handler;
    private Outbox outbox;
    private Destination destination;
    private Outbox.Writer writer;
    private String device;
    private OmiConnection omi;
    private AudioRecord microphone;
    private OmiFrames.Decoder decoder;
    private int omiCodec;
    private final OmiFrames frames = new OmiFrames();
    private Instant frameStarted, lastEnd, missingSince;
    private Instant wall;
    private long uptime;
    private PowerManager.WakeLock wakeLock;
    private long renewWakeAt;
    private AlarmManager alarms;
    private PendingIntent policyIntent;
    private RecordingWindow policy;
    private boolean stopped, stopRequested, readerStarted, destroying;
    private boolean fatal;
    private boolean bluetoothSource;
    private String address;
    private int inputGeneration;
    private boolean policyWaiting;
    private final Runnable scheduleTick = this::checkPolicy;
    private final AlarmManager.OnAlarmListener policyAlarm = this::checkPolicy;
    private final BroadcastReceiver clockChanged = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) { handler.post(scheduleTick); }
    };
    private boolean captureAllowed() { return !stopRequested && !stopped && CapturePolicy.liveEnabled() && policy != null && policy.allows(ZonedDateTime.now()); }
    private void checkPolicy() {
        if (stopped || stopRequested || destination == null) return;
        try {
            policy = CapturePolicy.window(this);
            reconcilePolicy();
            cancelPolicyCheck();
            Instant next = stopped || stopRequested ? null : policy.nextChange(ZonedDateTime.now());
            if (next == null) return;
            handler.postDelayed(scheduleTick, Math.max(1, next.toEpochMilli() - System.currentTimeMillis()));
            // The live listener needs no exact-alarm permission. Doze may defer it; the idle
            // alarm also wakes a waiting service without keeping the CPU awake overnight.
            alarms.setExact(AlarmManager.RTC_WAKEUP, next.toEpochMilli(), "Daythread:Schedule", policyAlarm, handler);
            if (policyWaiting) alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, next.toEpochMilli(), policyIntent);
        } catch (Exception e) { fail(e); }
    }
    private void cancelPolicyCheck() {
        handler.removeCallbacks(scheduleTick);
        alarms.cancel(policyAlarm); alarms.cancel(policyIntent);
    }
    private void keepAwake() {
        long elapsed = SystemClock.elapsedRealtime();
        if (wakeLock.isHeld() && elapsed < renewWakeAt) return;
        wakeLock.acquire(10 * 60 * 1000L);
        renewWakeAt = elapsed + 5 * 60 * 1000L;
    }
    private void releaseWakeLock() { if (wakeLock != null && wakeLock.isHeld()) wakeLock.release(); }
    private void reconcilePolicy() throws IOException {
        if (!policy.enabled()) { finish(); return; }
        if (!captureAllowed()) {
            if (!policyWaiting) { captureState("paused", "user_pause"); policyWaiting = true; }
            recording.set(false);
            if (microphone != null && readerStarted) {
                try { microphone.stop(); } catch (IllegalStateException ignored) { }
            }
            if (omi != null) {
                inputGeneration++;
                OmiConnection previous = omi; omi = null; previous.close();
                frames.reset(); decoder = null; frameStarted = null; missingSince = null;
                closeChunk(); discontinuity = true; lastEnd = null;
            }
            if (!readerStarted) releaseWakeLock();
            if (writer == null) update(getString(R.string.schedule_waiting));
            return;
        }
        policyWaiting = false;
        if (bluetoothSource) { if (omi == null) startOmi(address); }
        else if (!readerStarted) startMicrophone();
    }
    private boolean discontinuity = true;
    private String gapCause = "connection";

    @Override public void onCreate() {
        super.onCreate();
        thread = new HandlerThread("daythread-capture"); thread.start(); handler = new Handler(thread.getLooper());
        alarms = getSystemService(AlarmManager.class);
        policyIntent = PendingIntent.getService(this, 1703, new Intent(this, CaptureService.class).setAction("policy"), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        IntentFilter clock = new IntentFilter(Intent.ACTION_TIME_CHANGED); clock.addAction(Intent.ACTION_TIMEZONE_CHANGED);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(clockChanged, clock, Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(clockChanged, clock);
        getSystemService(NotificationManager.class).createNotificationChannel(
            new NotificationChannel("capture", getString(R.string.capture), NotificationManager.IMPORTANCE_LOW));
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) { stopSelf(); return START_NOT_STICKY; }
        String action = intent.getAction();
        if ("replace".equals(action)) { handler.post(this::finish); return START_NOT_STICKY; }
        if ("policy".equals(action)) { if (active) handler.post(scheduleTick); else stopSelf(); return START_NOT_STICKY; }
        if ("reconnect".equals(action)) { if (active) handler.post(() -> { if (omi != null) omi.reconnect(); }); else stopSelf(); return START_NOT_STICKY; }
        if ("stop".equals(action)) { CapturePolicy.manual(this, false, CapturePolicy.source(this)); handler.post(this::finish); return START_NOT_STICKY; }
        if ("mark".equals(action) || "meeting_toggle".equals(action)) { handler.post(() -> event(action)); return START_NOT_STICKY; }
        if (active) return START_NOT_STICKY;
        active = true; status = getString(R.string.starting_capture);
        boolean bluetooth = "omi".equals(action);
        source = action;
        bluetoothSource = bluetooth; address = intent.getStringExtra("address");
        try {
            if (Build.VERSION.SDK_INT >= 30) startForeground(1702, notification(), bluetooth ? ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE : ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
            else startForeground(1702, notification());
        } catch (RuntimeException e) { active = false; AppState.error(this, getString(R.string.capture_start_failed)); stopSelf(); return START_NOT_STICKY; }
        handler.post(() -> {
            try {
                outbox = AppState.outbox(this); destination = AppState.destination(this);
                captureProfile = destination.profile();
                device = bluetooth ? "omi-" + intent.getStringExtra("address").replace(":", "") : "android-" + AppState.phone(this);
                wall = Instant.now(); uptime = SystemClock.elapsedRealtimeNanos();
                wakeLock = getSystemService(PowerManager.class).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Daythread:Capture");
                wakeLock.setReferenceCounted(false);
                checkPolicy();
            } catch (Exception e) { fail(e); }
        });
        return START_NOT_STICKY; // Android must never silently restart microphone capture after process death.
    }
    private Instant now() { return wall.plusNanos(Math.max(0, SystemClock.elapsedRealtimeNanos() - uptime)); }
    private Notification notification() {
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class).putExtra("capture", true), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent stop = PendingIntent.getService(this, 0, new Intent(this, CaptureService.class).setAction("stop"), PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, "capture").setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.app_name)).setContentText(status).setContentIntent(open).setOngoing(true)
            .addAction(new Notification.Action.Builder(null, getString(R.string.stop), stop).build()).build();
    }
    private void update(String text) {
        if (status.equals(text)) return;
        status = text; getSystemService(NotificationManager.class).notify(1702, notification());
    }
    @SuppressLint("MissingPermission")
    private void startMicrophone() throws IOException {
        if (!captureAllowed()) return;
        int size = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (size < 0) throw new IOException(getString(R.string.microphone_unavailable));
        microphone = new AudioRecord(MediaRecorder.AudioSource.MIC, 16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(size * 2, 32000));
        if (microphone.getState() != AudioRecord.STATE_INITIALIZED) throw new IOException(getString(R.string.microphone_unavailable));
        if (Build.VERSION.SDK_INT >= 29) microphone.registerAudioRecordingCallback(getMainExecutor(), new AudioManager.AudioRecordingCallback() {
            @Override public void onRecordingConfigChanged(java.util.List<AudioRecordingConfiguration> configs) {
                if (!recording.get()) return;
                for (AudioRecordingConfiguration config : configs) if (config.isClientSilenced()) handler.post(() -> {
                    if (recording.get()) fail(new IOException(getString(R.string.microphone_interrupted)));
                });
            }
        });
        if (!captureAllowed()) { microphone.release(); microphone = null; return; }
        keepAwake();
        writer = outbox.begin(destination, device, now()); discontinuity = false;
        microphone.startRecording(); recording.set(true);
        captureState("capturing", "resumed"); update(getString(R.string.recording_phone));
        // Blocking reads belong to a separate thread so Stop and BLE commands remain responsive.
        readerStarted = true;
        AudioRecord input = microphone;
        new Thread(() -> {
            byte[] buffer = new byte[16000];
            try {
                while (recording.get()) {
                    int count = input.read(buffer, 0, buffer.length, AudioRecord.READ_BLOCKING);
                    if (count <= 0) { if (recording.get()) throw new IOException(getString(R.string.microphone_interrupted)); break; }
                    byte[] copy = java.util.Arrays.copyOf(buffer, count / 2 * 2);
                    // A bounded handoff prevents a slow disk from accumulating unbounded raw audio in RAM.
                    java.util.concurrent.CountDownLatch written = new java.util.concurrent.CountDownLatch(1);
                    handler.post(() -> { try { if (captureAllowed() && recording.get()) append(copy, null, null); } catch (Exception e) { fail(e); } finally { written.countDown(); } });
                    written.await();
                }
            } catch (Exception e) { if (recording.get()) handler.post(() -> fail(e)); }
            finally { handler.post(() -> {
                readerStarted = false;
                input.release(); if (microphone == input) microphone = null;
                try { closeChunk(); discontinuity = true; lastEnd = null; } catch (Exception e) { fail(e); }
                releaseWakeLock();
                if (stopRequested) finishTail();
                else checkPolicy();
            }); }
        }, "daythread-microphone").start();
    }
    private void startOmi(String address) {
        if (!captureAllowed()) return;
        final int generation = ++inputGeneration;
        missingSince = now();
        omi = new OmiConnection(this, address, handler, new OmiConnection.Listener() {
            public void codec(int codec) throws Exception { if (generation != inputGeneration) return; omiCodec = codec; decoder = new OmiFrames.Decoder(codec); frames.reset(); }
            public void status(String text) { if (generation == inputGeneration) update(text); }
            public void failure(Exception error) { if (generation == inputGeneration) fail(error); }
            public void disconnected() {
                if (generation != inputGeneration) return;
                try {
                    closeChunk(); frames.reset(); decoder = null;
                    releaseWakeLock();
                    if (missingSince == null) missingSince = lastEnd == null ? now() : lastEnd;
                    gapCause = "connection"; discontinuity = true;
                    captureState("disconnected", "connection");
                } catch (Exception e) { fail(e); }
            }
            public void button(byte[] bytes) {
                if (generation != inputGeneration || !captureAllowed() || bytes.length < 4) return;
                int code = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).getInt();
                if (code == 1) event("mark"); else if (code == 2) event("meeting_toggle");
            }
            public void audio(byte[] bytes) {
                if (generation != inputGeneration || decoder == null || !captureAllowed()) return;
                try {
                    OmiFrames.Result result = frames.consume(bytes);
                    if (result.lost()) {
                        closeChunk(); gapCause = "packet_loss"; discontinuity = true;
                        if (missingSince == null) missingSince = lastEnd == null ? now() : lastEnd;
                        decoder = null;
                        // Codec state must be reset after packet loss; preserve the discovered codec.
                        decoder = new OmiFrames.Decoder(omiCodec);
                    }
                    if (result.frame() != null) {
                        byte[] pcm = decoder.decode(result.frame());
                        Instant start = frameStarted == null ? now() : frameStarted;
                        if (missingSince != null) {
                            gap(missingSince, start, gapCause); missingSince = null;
                            captureState("capturing", "resumed");
                        }
                        append(pcm, result.frame(), start);
                    }
                    if (result.started()) frameStarted = now();
                } catch (Exception e) { fail(e); }
            }
        });
    }
    private void append(byte[] pcm, byte[] original, Instant started) throws IOException {
        keepAwake();
        int offset = 0;
        while (offset < pcm.length) {
            if (writer == null) {
                Instant start = !discontinuity && lastEnd != null ? lastEnd : started == null ? now() : started.plusNanos(offset / 2L * 62500);
                writer = device.startsWith("omi-") ? outbox.begin(destination, device, start, omiCodec) : outbox.begin(destination, device, start); discontinuity = false;
            }
            int count = Math.min(writer.remaining(), pcm.length - offset);
            writer.append(pcm, offset, count, offset == 0 ? original : null); offset += count;
            if (writer.remaining() == 0) closeChunk();
        }
    }
    private void closeChunk() throws IOException {
        if (writer == null) return;
        lastEnd = writer.end(); writer.close(); writer = null;
        UploadJob.schedule(this); UploadJob.pump(this);
    }
    private void captureState(String state, String cause) throws IOException {
        outbox.enqueue("state", destination, new Json().put("phone_id", AppState.phone(this)).put("occurred_at", now().toString()).put("state", state).put("cause", cause));
        UploadJob.schedule(this);
    }
    private void gap(Instant start, Instant end, String cause) throws IOException {
        if (!end.isAfter(start)) return;
        outbox.enqueue("gap", destination, new Json().put("device_id", device).put("started_at", start.toString())
            .put("ended_at", end.toString()).put("cause", cause).put("reason", "Android capture interruption"));
    }
    private void event(String action) {
        if (!active || !captureAllowed() || destination == null) return;
        try {
            // The backend replays the raw gestures and enforces cooldown/minimum duration once.
            outbox.enqueue("event", destination, new Json().put("device_id", device).put("occurred_at", now().toString()).put("action", action));
            UploadJob.pump(this); UploadJob.schedule(this);
        } catch (Exception e) { fail(e); }
    }
    private void fail(Exception e) {
        fatal = true; AppState.error(this, e.getMessage() == null ? getString(R.string.capture_failed) : e.getMessage()); finish();
    }
    private void finish() {
        if (stopRequested) return;
        stopRequested = true; recording.set(false); cancelPolicyCheck();
        if (microphone != null && readerStarted) {
            try { microphone.stop(); } catch (RuntimeException e) { AppState.error(this, e.getMessage()); }
            return; // The read thread drains its final buffer, then posts finishTail.
        }
        finishTail();
    }
    private void finishTail() {
        if (stopped) return;
        stopped = true;
        try {
            inputGeneration++;
            if (omi != null) { omi.close(); omi = null; }
            // The last BLE frame has no terminator. Decode it only if the codec validates it.
            byte[] finalFrame = captureAllowed() ? frames.flush() : null;
            frames.reset();
            if (finalFrame != null && decoder != null) {
                try { append(decoder.decode(finalFrame), finalFrame, frameStarted); }
                catch (Exception e) { gap(lastEnd == null ? now().minusMillis(20) : lastEnd, now(), "incomplete_frame"); }
            }
            closeChunk();
            if (missingSince != null && destination != null) { gap(missingSince, now(), gapCause); missingSince = null; }
            if (destination != null && outbox != null) captureState(fatal ? "blocked" : "paused", fatal ? "unknown" : "user_pause");
        } catch (Exception e) { AppState.error(this, e.getMessage()); }
        releaseWakeLock();
        active = false; captureProfile = ""; status = "";
        stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(); UploadJob.schedule(this); UploadJob.pump(this);
        if (destroying) release();
    }
    @Override public void onDestroy() {
        unregisterReceiver(clockChanged);
        if (handler != null) handler.post(() -> { destroying = true; finish(); if (stopped) release(); });
        super.onDestroy();
    }
    private void release() { if (microphone != null) { microphone.release(); microphone = null; } thread.quitSafely(); }
    @Override public IBinder onBind(Intent intent) { return null; }
}

package com.daythread.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.*;
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.*;
import android.view.*;
import android.webkit.*;
import android.widget.*;
import com.daythread.app.core.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;

/** Native capture plus the existing mobile archive, without a JavaScript-to-native bridge. */
public final class MainActivity extends Activity {
    private static final int BACKGROUND = 0xfffaf8f4, INK = 0xff302b27, MUTED = 0xff726c65, ACCENT = 0xffb96143;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private LinearLayout root, content, navigation;
    private WebView web;
    private TextView captureStatus, queueStatus, captureError;
    private String screen = "", webOrigin = "";
    private int endpointIndex;
    private boolean offline;
    private boolean previousActive;
    private boolean resumed;
    private boolean refreshingQueue;
    private Switch microphoneEnabled;
    private boolean updatingPolicyUI;
    private Runnable permissionAction;
    private ValueCallback<Uri[]> chooser;
    private BluetoothLeScanner scanner;
    private ScanCallback scanCallback;
    private AlertDialog scanDialog;
    private Runnable scanTimeout;
    private byte[] exportBytes;
    private android.media.MediaPlayer player;
    private final Runnable ticker = new Runnable() {
        @Override public void run() {
            if (resumed && screen.equals("capture")) {
                if (previousActive != CaptureService.active) showCapture();
                updateCapture();
                ui.removeCallbacks(this);
                ui.postDelayed(this, 5000);
            }
        }
    };
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(BACKGROUND);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom); return insets;
            });
        } else root.setFitsSystemWindows(true);
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 33) getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::handleBack);
        LinearLayout brand = new LinearLayout(this); brand.setGravity(Gravity.CENTER_VERTICAL); brand.setPadding(dp(18), dp(10), dp(18), dp(6));
        ImageView mark = new ImageView(this); mark.setImageResource(R.mipmap.ic_launcher); mark.setContentDescription(null);
        brand.addView(mark, new LinearLayout.LayoutParams(dp(34), dp(34)));
        TextView title = text(getString(R.string.app_name), 22, INK); title.setTypeface(null, Typeface.BOLD); title.setPadding(dp(10), 0, 0, 0); brand.addView(title);
        root.addView(brand);
        navigation = new LinearLayout(this); navigation.setPadding(dp(12), 0, dp(12), dp(8)); root.addView(navigation);
        content = new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL); root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));
        configureWeb();
        io.execute(() -> {
            try { AppState.outbox(this); UploadJob.schedule(this); UploadJob.pump(this); }
            catch (Exception e) { AppState.error(this, e.getMessage()); }
        });
        String initial = saved == null ? "" : saved.getString("screen", "");
        if (getIntent().getBooleanExtra("capture", false) || CaptureService.active || initial.equals("capture")) showCapture();
        else if (configured() && !initial.equals("connection")) showArchive(); else showConnection();
    }
    @Override protected void onResume() { super.onResume(); resumed = true; refreshVisibleWork(); }
    @Override protected void onPause() { resumed = false; refreshVisibleWork(); stopScan(); stopPlayback(); super.onPause(); }
    private void refreshVisibleWork() {
        ui.removeCallbacks(ticker);
        if (resumed && screen.equals("capture")) ui.post(ticker);
        if (web == null) return;
        if (resumed && screen.equals("archive")) { web.onResume(); web.resumeTimers(); }
        else { web.onPause(); web.pauseTimers(); }
    }
    @Override protected void onNewIntent(Intent intent) { super.onNewIntent(intent); if (intent.getBooleanExtra("capture", false)) showCapture(); }
    @Override protected void onSaveInstanceState(Bundle out) { out.putString("screen", screen); super.onSaveInstanceState(out); }
    private boolean configured() { try { AppState.destination(this); return true; } catch (RuntimeException e) { return false; } }
    private void selectScreen(String selected) {
        screen = selected; content.removeAllViews(); navigation.removeAllViews();
        refreshVisibleWork();
        int[] labels = {R.string.archive, R.string.capture, R.string.connection}; String[] names = {"archive", "capture", "connection"};
        for (int i = 0; i < labels.length; i++) {
            String name = names[i]; Button button = button(getString(labels[i]), () -> {
                if (name.equals("archive")) showArchive(); else if (name.equals("capture")) showCapture(); else showConnection();
            }, selected.equals(name));
            button.setTextSize(12); navigation.addView(button, new LinearLayout.LayoutParams(0, dp(48), 1));
        }
    }
    private LinearLayout page() {
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true);
        LinearLayout page = new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL); page.setPadding(dp(24), dp(24), dp(24), dp(28));
        scroll.addView(page); content.addView(scroll, new LinearLayout.LayoutParams(-1, -1)); return page;
    }
    private TextView heading(LinearLayout page, int copy) { TextView title = text(getString(copy), 31, INK); title.setTypeface(Typeface.create("serif", Typeface.NORMAL)); page.addView(title); space(page, 14); return title; }
    private TextView paragraph(LinearLayout page, int copy) { TextView view = text(getString(copy), 15, MUTED); view.setLineSpacing(dp(3), 1); page.addView(view); space(page, 16); return view; }
    private void section(LinearLayout page, int copy) { space(page, 18); TextView view = text(getString(copy), 18, INK); view.setTypeface(null, Typeface.BOLD); page.addView(view); space(page, 10); }
    private void showConnection() {
        selectScreen("connection"); LinearLayout page = page();
        heading(page, R.string.connect_title); paragraph(page, R.string.connect_intro);
        section(page, R.string.backend_endpoints);
        EditText endpoints = new EditText(this); endpoints.setTextSize(16); endpoints.setMinLines(2); endpoints.setMaxLines(8);
        endpoints.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        endpoints.setHint(R.string.endpoint_hint); endpoints.setText(AppState.preferences(this).getString("endpoints", "")); page.addView(endpoints, new LinearLayout.LayoutParams(-1, -2));
        space(page, 10); paragraph(page, R.string.endpoint_help); paragraph(page, R.string.http_notice);
        section(page, R.string.profile);
        TextView profile = text(AppState.preferences(this).getString("profileName", AppState.preferences(this).getString("profile", "default")), 16, INK); page.addView(profile);
        paragraph(page, R.string.profile_help);
        TextView result = text("", 14, ACCENT); result.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); page.addView(result);
        Button save = button(getString(R.string.test_connection), () -> {}, true);
        save.setOnClickListener(view -> {
            if (CaptureService.active) { toast(R.string.capture_running); return; }
            final Destination candidate;
            try { candidate = new Destination(Arrays.stream(endpoints.getText().toString().split("\\s+")).filter(s -> !s.isBlank()).collect(java.util.stream.Collectors.toList()), "default"); }
            catch (RuntimeException e) { result.setText(e.getMessage()); return; }
            save.setEnabled(false); result.setText(R.string.testing);
            io.execute(() -> {
                try {
                    Json directory = Backend.request(candidate, "/v1/profiles", "GET", null, null).json();
                    Json.JsonArray profiles = directory.getJsonArray("profiles");
                    if (profiles.length() == 0) throw new IOException(getString(R.string.choose_profile));
                    String[] names = new String[profiles.length()];
                    for (int i = 0; i < names.length; i++) { names[i] = profiles.getJson(i).getString("name"); new Destination(candidate.endpoints(), profiles.getJson(i).getString("id")); }
                    Json settings = Backend.request(candidate, "/v1/settings", "GET", null, null).json();
                    runOnUiThread(() -> {
                        if (isFinishing() || !screen.equals("connection")) return;
                        save.setEnabled(true); result.setText("");
                        new AlertDialog.Builder(this).setTitle(R.string.choose_profile).setItems(names, (dialog, which) -> {
                            if (CaptureService.active) { toast(R.string.capture_running); return; }
                            AppState.preferences(this).edit().putString("endpoints", String.join("\n", candidate.endpoints()))
                                .putString("profile", profiles.getJson(which).getString("id")).putString("profileName", names[which])
                                .putInt("meeting_max_minutes", settings.getInt("meeting_max_minutes"))
                                .putInt("meeting_min_seconds", settings.getInt("meeting_min_seconds"))
                                .putInt("marker_cooldown_seconds", settings.getInt("marker_cooldown_seconds"))
                                .putString("error", "").apply();
                            profile.setText(names[which]); result.setText(R.string.connected); webOrigin = "";
                            UploadJob.schedule(this);
                        }).setNegativeButton(R.string.cancel, null).show();
                    });
                } catch (Exception e) { runOnUiThread(() -> { save.setEnabled(true); result.setText(e.getMessage()); }); }
            });
        });
        page.addView(save, new LinearLayout.LayoutParams(-1, dp(54)));
        space(page, 12);
        Switch wifi = new Switch(this); wifi.setText(R.string.wifi_only); wifi.setMinHeight(dp(48)); wifi.setChecked(AppState.preferences(this).getBoolean("wifi", false));
        wifi.setOnCheckedChangeListener((view, checked) -> { AppState.preferences(this).edit().putBoolean("wifi", checked).apply(); UploadJob.schedule(this); }); page.addView(wifi);
        paragraph(page, R.string.wifi_help); section(page, R.string.about); paragraph(page, R.string.about_body);
        page.addView(button(getString(R.string.notices), () -> {
            try (InputStream input = getAssets().open("NOTICE.txt")) {
                new AlertDialog.Builder(this).setTitle(R.string.notices).setMessage(new String(Streams.read(input, 100_000), StandardCharsets.UTF_8)).setPositiveButton(android.R.string.ok, null).show();
            } catch (IOException e) { toast(R.string.download_failed); }
        }, false));
    }
    private void showCapture() {
        selectScreen("capture"); previousActive = CaptureService.active;
        LinearLayout page = page(); heading(page, R.string.capture_title); paragraph(page, R.string.capture_intro);
        String profile = CaptureService.active ? CaptureService.captureProfile : AppState.preferences(this).getString("profileName", "default");
        TextView scope = text(getString(R.string.capture_scope, profile), 14, MUTED); page.addView(scope); space(page, 16);
        captureStatus = text("", 18, ACCENT); captureStatus.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); page.addView(captureStatus); space(page, 16);
        if (CaptureService.active) {
            page.addView(button(getString(R.string.stop_capture), () -> startService(new Intent(this, CaptureService.class).setAction("stop")), true));
            page.addView(button(getString(R.string.important), () -> hint("mark"), false));
            page.addView(button(getString(R.string.meeting), () -> hint("meeting_toggle"), false));
        }
        page.addView(button(getString(R.string.record_phone), () -> permission(false, () -> {
            CapturePolicy.manual(this, true, "microphone"); startCapture("microphone", null);
        }), true));
        page.addView(button(getString(R.string.connect_omi), () -> permission(true, () -> { CapturePolicy.manual(this, true, "omi"); scan(); }), false));
        scheduleControls(page);
        space(page, 12); paragraph(page, R.string.capture_background); paragraph(page, R.string.capture_uploads); paragraph(page, R.string.gesture_help);
        section(page, R.string.queue_title); queueStatus = text("", 16, INK); page.addView(queueStatus); space(page, 12);
        captureError = text("", 14, ACCENT); captureError.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); page.addView(captureError);
        page.addView(button(getString(R.string.sync_now), () -> { UploadJob.schedule(this); UploadJob.pump(this, true); toast(R.string.sync_scheduled); }, true));
        page.addView(button(getString(R.string.listen_saved), this::listenSaved, false));
        space(page, 12); paragraph(page, R.string.queue_help); updateCapture();
    }
    private void scheduleControls(LinearLayout page) {
        android.content.SharedPreferences prefs = AppState.preferences(this);
        Switch enabled = new Switch(this); enabled.setText(R.string.microphone_enabled); enabled.setMinHeight(dp(48));
        microphoneEnabled = enabled;
        enabled.setChecked(CapturePolicy.allowed(this));
        enabled.setOnCheckedChangeListener((view, checked) -> {
            if (updatingPolicyUI) return;
            if (checked) permission(CapturePolicy.source(this).equals("omi"), () -> {
                CapturePolicy.manual(this, true, CapturePolicy.source(this)); startSelectedCapture();
            });
            else {
                CapturePolicy.manual(this, false, CapturePolicy.source(this));
                if (CaptureService.active) startService(new Intent(this, CaptureService.class).setAction("stop"));
                showCapture();
            }
        }); page.addView(enabled); paragraph(page, R.string.microphone_off_help);
        Switch scheduled = new Switch(this); scheduled.setText(R.string.recording_schedule); scheduled.setMinHeight(dp(48));
        scheduled.setChecked(prefs.getBoolean("recording_scheduled", false));
        scheduled.setOnCheckedChangeListener((view, checked) -> {
            prefs.edit().putBoolean("recording_scheduled", checked).commit();
            if (checked) armSchedule();
            else {
                // Disarming an automatic session stops capture rather than recording indefinitely.
                CapturePolicy.manual(this, false, CapturePolicy.source(this));
                if (CaptureService.active) startService(new Intent(this, CaptureService.class).setAction("stop"));
                showCapture();
            }
        }); page.addView(scheduled);
        if (!scheduled.isChecked()) return;
        page.addView(button(getString(R.string.schedule_source) + ": " + getString(CapturePolicy.source(this).equals("omi") ? R.string.connect_omi : R.string.record_phone), () -> {
            if (CaptureService.active) { toast(R.string.capture_running); return; }
            new AlertDialog.Builder(this).setTitle(R.string.schedule_source)
                .setSingleChoiceItems(new String[] {getString(R.string.record_phone), getString(R.string.connect_omi)}, CapturePolicy.source(this).equals("omi") ? 1 : 0,
                    (dialog, which) -> { prefs.edit().putString("recording_source", which == 0 ? "microphone" : "omi").commit(); dialog.dismiss(); showCapture(); })
                .setNegativeButton(R.string.cancel, null).show();
        }, false));
        String[] weekdays = new java.text.DateFormatSymbols(Locale.getDefault()).getWeekdays();
        for (int day = 0; day < 7; day++) {
            final int bit = 1 << day;
            CheckBox selected = new CheckBox(this); selected.setText(weekdays[(day + 1) % 7 + 1]);
            selected.setMinHeight(dp(48)); selected.setChecked((prefs.getInt("recording_days", 31) & bit) != 0);
            selected.setOnCheckedChangeListener((view, checked) -> {
                int days = prefs.getInt("recording_days", 31);
                prefs.edit().putInt("recording_days", checked ? days | bit : days & ~bit).commit(); notifyPolicy();
            }); page.addView(selected);
        }
        scheduleTime(page, "recording_start", 540, R.string.schedule_enable_at);
        scheduleTime(page, "recording_end", 1020, R.string.schedule_disable_at);
        if (prefs.getInt("recording_days", 31) == 0 || prefs.getInt("recording_start", 540) == prefs.getInt("recording_end", 1020)) paragraph(page, R.string.schedule_invalid);
        if (!prefs.getBoolean("microphone_enabled", true) || prefs.getBoolean("recording_override", false)) paragraph(page, R.string.schedule_override);
        page.addView(button(getString(R.string.follow_schedule), this::armSchedule, false));
        paragraph(page, R.string.schedule_time_help); paragraph(page, R.string.schedule_background_help);
    }
    private void scheduleTime(LinearLayout page, String key, int fallback, int label) {
        int minute = AppState.preferences(this).getInt(key, fallback);
        java.util.Calendar calendar = java.util.Calendar.getInstance(); calendar.set(java.util.Calendar.HOUR_OF_DAY, minute / 60); calendar.set(java.util.Calendar.MINUTE, minute % 60);
        page.addView(button(getString(label) + ": " + android.text.format.DateFormat.getTimeFormat(this).format(calendar.getTime()), () ->
            new TimePickerDialog(this, (picker, hour, minutes) -> {
                AppState.preferences(this).edit().putInt(key, hour * 60 + minutes).commit(); notifyPolicy(); showCapture();
            }, minute / 60, minute % 60, android.text.format.DateFormat.is24HourFormat(this)).show(), false));
    }
    private void armSchedule() {
        permission(CapturePolicy.source(this).equals("omi"), () -> {
            CapturePolicy.follow(this); startSelectedCapture();
        });
    }
    private void startSelectedCapture() {
        String source = CapturePolicy.activeSource(this);
        String address = AppState.preferences(this).getString("recording_omi_address", "");
        if (source.equals("omi") && address.isEmpty()) { scan(); return; }
        startCapture(source, address);
    }
    private void notifyPolicy() {
        if (CaptureService.active) startService(new Intent(this, CaptureService.class).setAction("policy"));
    }
    private void listenSaved() {
        io.execute(() -> {
            try {
                List<Outbox.Item> audio = AppState.outbox(this).pending().stream().filter(item -> item.kind().equals("audio")).collect(java.util.stream.Collectors.toList());
                Collections.reverse(audio);
                if (audio.size() > 20) audio = audio.subList(0, 20);
                List<Outbox.Item> items = audio;
                String[] names = items.stream().map(item -> item.data().getString("started_at") + " · " + item.destination().profile()).toArray(String[]::new);
                runOnUiThread(() -> {
                    if (items.isEmpty()) { toast(R.string.no_saved_audio); return; }
                    new AlertDialog.Builder(this).setTitle(R.string.listen_saved).setItems(names, (dialog, which) -> {
                        try {
                            stopPlayback(); player = new android.media.MediaPlayer();
                            player.setDataSource(items.get(which).file().getAbsolutePath());
                            android.media.MediaPlayer selected = player;
                            AlertDialog playback = new AlertDialog.Builder(this).setTitle(R.string.listen_saved).setMessage(names[which])
                                .setPositiveButton(R.string.stop, (d, w) -> stopPlayback()).create();
                            playback.setOnDismissListener(d -> stopPlayback());
                            selected.setOnPreparedListener(p -> { if (p == player) p.start(); });
                            selected.setOnCompletionListener(p -> playback.dismiss());
                            selected.setOnErrorListener((p, what, extra) -> { playback.dismiss(); toast(R.string.no_saved_audio); return true; });
                            playback.show(); selected.prepareAsync();
                        } catch (Exception e) { stopPlayback(); toast(R.string.no_saved_audio); }
                    }).setNegativeButton(R.string.cancel, null).show();
                });
            } catch (Exception e) { runOnUiThread(() -> toast(R.string.no_saved_audio)); }
        });
    }
    private void stopPlayback() { if (player != null) { player.release(); player = null; } }
    private void updateCapture() {
        if (captureStatus == null || !screen.equals("capture")) return;
        if (microphoneEnabled != null) {
            updatingPolicyUI = true;
            microphoneEnabled.setChecked(CapturePolicy.allowed(this));
            updatingPolicyUI = false;
        }
        captureStatus.setText(CaptureService.active ? CaptureService.status : getString(R.string.capture_ready));
        if (refreshingQueue) return;
        refreshingQueue = true;
        io.execute(() -> {
            String summary;
            try {
                Outbox outbox = AppState.outbox(this); int count = outbox.pendingCount();
                summary = count == 0 ? getString(R.string.queue_empty) : getString(R.string.queue_summary, count, String.format(Locale.getDefault(), "%.1f", outbox.bytes() / 1_000_000.0));
            } catch (Exception e) { summary = e.getMessage(); }
            String result = summary;
            runOnUiThread(() -> { refreshingQueue = false; if (screen.equals("capture")) { queueStatus.setText(result); captureError.setText(AppState.preferences(this).getString("error", "")); } });
        });
    }
    private void hint(String action) { startService(new Intent(this, CaptureService.class).setAction(action)); toast(R.string.hint_saved); }
    private void startCapture(String action, String address) {
        if (!configured()) { toast(R.string.connect_first); showConnection(); return; }
        AppState.error(this, "");
        if (action.equals("omi") && address != null) AppState.preferences(this).edit().putString("recording_omi_address", address).commit();
        if (CaptureService.active) {
            if (CaptureService.source.equals(action)) { notifyPolicy(); showCapture(); return; }
            startService(new Intent(this, CaptureService.class).setAction("replace"));
            ui.postDelayed(() -> startCapture(action, address), 300); return;
        }
        try { startForegroundService(new Intent(this, CaptureService.class).setAction(action).putExtra("address", address)); }
        catch (RuntimeException e) { AppState.error(this, getString(R.string.capture_start_failed)); }
        ui.postDelayed(this::showCapture, 300);
    }
    private void permission(boolean bluetooth, Runnable action) {
        if (!configured()) { toast(R.string.connect_first); showConnection(); return; }
        List<String> needed = new ArrayList<>();
        if (bluetooth) {
            if (Build.VERSION.SDK_INT >= 31) { needed.add(Manifest.permission.BLUETOOTH_SCAN); needed.add(Manifest.permission.BLUETOOTH_CONNECT); }
            else needed.add(Manifest.permission.ACCESS_FINE_LOCATION);
        } else needed.add(Manifest.permission.RECORD_AUDIO);
        if (Build.VERSION.SDK_INT >= 33) needed.add(Manifest.permission.POST_NOTIFICATIONS);
        List<String> missing = needed.stream().filter(p -> checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED).collect(java.util.stream.Collectors.toList());
        if (missing.isEmpty()) action.run(); else { permissionAction = action; requestPermissions(missing.toArray(new String[0]), 10); }
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(request, permissions, grants);
        if (request != 10) return;
        Runnable action = permissionAction; permissionAction = null;
        for (int i = 0; i < grants.length; i++) if (!permissions[i].equals(Manifest.permission.POST_NOTIFICATIONS) && grants[i] != PackageManager.PERMISSION_GRANTED) { toast(R.string.permission_needed); return; }
        if (grants.length > 0 && action != null) action.run();
    }
    @SuppressLint("MissingPermission")
    private void scan() {
        stopScan();
        BluetoothManager manager = getSystemService(BluetoothManager.class);
        BluetoothAdapter adapter = manager == null ? null : manager.getAdapter();
        if (adapter == null) { toast(R.string.bluetooth_unavailable); return; }
        if (!adapter.isEnabled()) { toast(R.string.enable_bluetooth); startActivity(new Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE)); return; }
        scanner = adapter.getBluetoothLeScanner(); if (scanner == null) { toast(R.string.bluetooth_unavailable); return; }
        List<String> addresses = new ArrayList<>();
        ArrayAdapter<String> devices = new ArrayAdapter<>(this, android.R.layout.simple_list_item_1);
        scanDialog = new AlertDialog.Builder(this).setTitle(R.string.searching_omi).setAdapter(devices, (dialog, position) -> {
            String address = addresses.get(position); stopScan(); startCapture("omi", address);
        }).setNegativeButton(R.string.cancel, (dialog, which) -> stopScan()).create();
        scanDialog.setOnCancelListener(dialog -> stopScan()); scanDialog.show();
        scanCallback = new ScanCallback() {
            @Override public void onScanResult(int type, ScanResult result) {
                ScanRecord record = result.getScanRecord(); String name = record == null ? null : record.getDeviceName();
                if (name == null) name = result.getDevice().getName();
                String lower = name == null ? "" : name.trim().toLowerCase(Locale.ROOT);
                boolean advertised = record != null && record.getServiceUuids() != null && record.getServiceUuids().contains(new ParcelUuid(OmiConnection.SERVICE));
                if (!advertised && !(lower.equals("omi") || lower.startsWith("omi ") || lower.startsWith("omi-") || lower.startsWith("omi_"))) return;
                String address = result.getDevice().getAddress(); String label = (name == null ? "Omi" : name) + "\n" + address;
                runOnUiThread(() -> { if (scanCallback != this || addresses.contains(address)) return; addresses.add(address); devices.add(label); });
            }
            @Override public void onScanFailed(int error) { runOnUiThread(() -> { if (scanCallback == this) { stopScan(); toast(R.string.scan_failed); } }); }
        };
        try { scanner.startScan(null, new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_BALANCED).build(), scanCallback); }
        catch (RuntimeException e) { stopScan(); toast(R.string.scan_failed); }
        scanTimeout = () -> { if (scanCallback != null) { boolean empty = addresses.isEmpty(); stopScan(); if (empty) toast(R.string.no_omi); } };
        ui.postDelayed(scanTimeout, 15000);
    }
    @SuppressLint("MissingPermission") private void stopScan() {
        if (scanTimeout != null) { ui.removeCallbacks(scanTimeout); scanTimeout = null; }
        if (scanner != null && scanCallback != null) { try { scanner.stopScan(scanCallback); } catch (RuntimeException ignored) {} }
        scanCallback = null; scanner = null;
        if (scanDialog != null) { scanDialog.dismiss(); scanDialog = null; }
    }
    private void showArchive() {
        if (!configured()) { showConnection(); return; }
        selectScreen("archive");
        if (offline) { showOffline(); return; }
        if (web.getParent() instanceof android.view.ViewGroup parent) parent.removeView(web);
        content.addView(web, new LinearLayout.LayoutParams(-1, -1));
        if (webOrigin.isEmpty()) { endpointIndex = 0; loadEndpoint(); }
    }
    private void loadEndpoint() {
        List<String> endpoints = AppState.destination(this).endpoints();
        if (endpointIndex >= endpoints.size()) { offline = true; if (screen.equals("archive")) showOffline(); return; }
        webOrigin = endpoints.get(endpointIndex); web.loadUrl(webOrigin + "/");
    }
    private void showOffline() {
        content.removeAllViews(); LinearLayout page = page(); heading(page, R.string.offline_title); paragraph(page, R.string.offline_help);
        page.addView(button(getString(R.string.retry), () -> { offline = false; webOrigin = ""; showArchive(); }, true));
        page.addView(button(getString(R.string.capture), this::showCapture, false));
        page.addView(button(getString(R.string.connection), this::showConnection, false));
    }
    @SuppressLint("SetJavaScriptEnabled")
    private void configureWeb() {
        web = new WebView(this); web.setBackgroundColor(BACKGROUND);
        WebSettings settings = web.getSettings(); settings.setJavaScriptEnabled(true); settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false); settings.setAllowContentAccess(true); settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true); settings.setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (Destination.sameOrigin(webOrigin, request.getUrl().toString())) return false;
                if (request.isForMainFrame() && request.hasGesture() && (request.getUrl().getScheme().equals("https") || request.getUrl().getScheme().equals("http"))) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl())); } catch (ActivityNotFoundException ignored) {}
                }
                return true;
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame() && Destination.sameOrigin(webOrigin, request.getUrl().toString())) { endpointIndex++; loadEndpoint(); }
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame() && Set.of(502, 503, 504).contains(response.getStatusCode())) { endpointIndex++; loadEndpoint(); }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            // Native foreground capture handles the microphone, including on trusted HTTP LANs.
            @Override public void onPermissionRequest(PermissionRequest request) { request.deny(); }
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (chooser != null) chooser.onReceiveValue(null);
                chooser = callback;
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
                intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"audio/*", "text/plain", "application/x-subrip", "application/json", "text/calendar", "application/zip"});
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                try { startActivityForResult(intent, 20); } catch (ActivityNotFoundException e) { chooser.onReceiveValue(null); chooser = null; }
                return true;
            }
        });
        web.setDownloadListener((url, agent, disposition, mime, length) -> download(url, disposition, mime));
    }
    private void download(String url, String disposition, String mime) {
        if (url.startsWith("blob:") && Destination.sameOrigin(webOrigin, url.substring(5))) {
            String script = "(async()=>{const b=await(await fetch(" + org.json.JSONObject.quote(url) + ")).blob();if(b.size>25000000)return '';return await new Promise(r=>{const f=new FileReader();f.onload=()=>r(f.result);f.onerror=()=>r('');f.readAsDataURL(b)})})()";
            // evaluateJavascript does not await Promises. Poll a page-local value with no native bridge.
            String key = "__daythreadExport" + SystemClock.uptimeMillis();
            web.evaluateJavascript("window[" + org.json.JSONObject.quote(key) + "]=null;" + script + ".then(v=>window[" + org.json.JSONObject.quote(key) + "]=v).catch(()=>window[" + org.json.JSONObject.quote(key) + "]='')", null);
            pollExport(key, mime, 0); return;
        }
        if (!Destination.sameOrigin(webOrigin, url)) { toast(R.string.download_failed); return; }
        io.execute(() -> {
            java.net.HttpURLConnection connection = null;
            try {
                connection = (java.net.HttpURLConnection) new java.net.URL(url).openConnection(); connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(10000); connection.setReadTimeout(30000);
                String cookie = CookieManager.getInstance().getCookie(url); if (cookie != null) connection.setRequestProperty("Cookie", cookie);
                if (connection.getResponseCode() != 200) throw new IOException();
                byte[] bytes;
                try (InputStream input = connection.getInputStream()) { bytes = Streams.read(input, 25_000_001); }
                if (bytes.length > 25_000_000) { runOnUiThread(() -> toast(R.string.export_large)); return; }
                String filename = URLUtil.guessFileName(url, disposition, mime);
                runOnUiThread(() -> saveExport(bytes, mime, filename));
            } catch (Exception e) { runOnUiThread(() -> toast(R.string.download_failed)); }
            finally { if (connection != null) connection.disconnect(); }
        });
    }
    private void pollExport(String key, String mime, int attempt) {
        if (isDestroyed()) return;
        if (attempt > 100) { web.evaluateJavascript("delete window[" + org.json.JSONObject.quote(key) + "]", null); toast(R.string.download_failed); return; }
        web.evaluateJavascript("window[" + org.json.JSONObject.quote(key) + "]", result -> {
            if (result == null || result.equals("null")) { ui.postDelayed(() -> pollExport(key, mime, attempt + 1), 100); return; }
            try {
                String data = new Json("{\"value\":" + result + "}").getString("value");
                if (!data.startsWith("data:") || !data.contains(";base64,")) { toast(R.string.export_large); return; }
                String type = data.substring(5, data.indexOf(';'));
                String extension = type.contains("calendar") ? ".ics" : type.contains("json") ? ".json" : type.contains("zip") ? ".zip" : ".md";
                saveExport(android.util.Base64.decode(data.substring(data.indexOf(',') + 1), android.util.Base64.DEFAULT), type.isEmpty() ? mime : type, "daythread" + extension);
            } catch (RuntimeException e) { toast(R.string.download_failed); }
            finally { web.evaluateJavascript("delete window[" + org.json.JSONObject.quote(key) + "]", null); }
        });
    }
    private void saveExport(byte[] bytes, String mime, String filename) {
        exportBytes = bytes;
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType(mime == null || mime.isBlank() ? "application/octet-stream" : mime).putExtra(Intent.EXTRA_TITLE, filename);
        try { startActivityForResult(intent, 21); } catch (ActivityNotFoundException e) { exportBytes = null; toast(R.string.download_failed); }
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == 20 && chooser != null) {
            List<Uri> files = new ArrayList<>();
            if (result == RESULT_OK && data != null) {
                if (data.getClipData() != null) for (int i = 0; i < data.getClipData().getItemCount(); i++) files.add(data.getClipData().getItemAt(i).getUri());
                else if (data.getData() != null) files.add(data.getData());
            }
            chooser.onReceiveValue(files.isEmpty() ? null : files.toArray(new Uri[0])); chooser = null;
        }
        if (request == 21) {
            byte[] bytes = exportBytes; exportBytes = null;
            if (bytes == null || result != RESULT_OK || data == null || data.getData() == null) return;
            Uri uri = data.getData();
            io.execute(() -> { try (OutputStream output = getContentResolver().openOutputStream(uri)) { if (output == null) throw new IOException(); output.write(bytes); }
                catch (IOException e) { runOnUiThread(() -> toast(R.string.download_failed)); } });
        }
    }
    // Android 13+ uses the registered predictive-back callback; this handles Android 8–12.
    @SuppressLint("GestureBackNavigation")
    @Override public void onBackPressed() { handleBack(); }
    private void handleBack() {
        if (screen.equals("archive") && !offline && web.canGoBack()) web.goBack();
        else if (!screen.equals("archive") && configured()) showArchive(); else finish();
    }
    @Override protected void onDestroy() {
        ui.removeCallbacksAndMessages(null); stopScan(); if (chooser != null) chooser.onReceiveValue(null);
        if (web != null) { if (web.getParent() instanceof android.view.ViewGroup parent) parent.removeView(web); web.destroy(); }
        io.shutdown(); super.onDestroy();
    }
    private TextView text(String value, float size, int color) { TextView text = new TextView(this); text.setText(value); text.setTextSize(size); text.setTextColor(color); return text; }
    private Button button(String label, Runnable action, boolean primary) {
        Button button = new Button(this); button.setText(label); button.setAllCaps(false); button.setTextSize(15);
        button.setTextColor(primary ? Color.WHITE : INK); button.setMinHeight(dp(52)); button.setPadding(dp(12), dp(8), dp(12), dp(8));
        GradientDrawable background = new GradientDrawable(); background.setColor(primary ? ACCENT : 0xffeeeae3); background.setCornerRadius(dp(14)); button.setBackground(background);
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, -2); layout.setMargins(dp(3), dp(4), dp(3), dp(4)); button.setLayoutParams(layout);
        button.setOnClickListener(view -> action.run()); return button;
    }
    private void space(LinearLayout layout, int height) { layout.addView(new View(this), new LinearLayout.LayoutParams(1, dp(height))); }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void toast(int message) { Toast.makeText(this, message, Toast.LENGTH_LONG).show(); }
}

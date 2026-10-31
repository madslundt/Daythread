package com.daythread.app;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.*;
import android.view.View;
import android.widget.*;
import com.daythread.app.core.*;
import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.time.format.FormatStyle;
import java.util.*;
import java.util.concurrent.*;

/** A notification opens its original archive's question day without changing the active profile. */
public final class QuestionsActivity extends Activity {
    static final String ROUTE = "questions_route";
    private static final int INK = 0xff302b27, MUTED = 0xff726c65, ACCENT = 0xffb96143;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final ArrayList<TimeLabel> dates = new ArrayList<>();
    private record TimeLabel(TextView view, Instant askedAt) {}
    private QuestionsRoute route;
    private QuestionDay day;
    private ScrollView scroll;
    private LinearLayout page;
    private TextView status;
    private Button retry;
    private boolean active, loading, focused;
    private int restoredPosition = -1;
    private final Runnable poll = this::load;
    private final Runnable clock = new Runnable() {
        @Override public void run() { updateTimes(); if (active) ui.postDelayed(this, 30000); }
    };

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        try { route = QuestionsRoute.parse(getIntent().getStringExtra(ROUTE)); }
        catch (RuntimeException error) { finish(); return; }
        if (saved != null) { focused = saved.getBoolean("focused", false); restoredPosition = saved.getInt("scroll_y", 0); }
        scroll = new ScrollView(this); scroll.setFillViewport(true); scroll.setBackgroundColor(0xfffaf8f4);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            scroll.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(android.view.WindowInsets.Type.systemBars() | android.view.WindowInsets.Type.displayCutout());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom); return insets;
            });
        } else scroll.setFitsSystemWindows(true);
        page = new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL); page.setPadding(dp(24), dp(24), dp(24), dp(32));
        scroll.addView(page); setContentView(scroll); render();
    }
    @Override protected void onResume() {
        super.onResume(); active = true;
        if (route == null) return;
        load();
        ui.removeCallbacks(clock); ui.post(clock);
    }
    @Override protected void onPause() { active = false; ui.removeCallbacks(poll); ui.removeCallbacks(clock); super.onPause(); }
    @Override protected void onSaveInstanceState(Bundle out) { out.putBoolean("focused", focused); if (scroll != null) out.putInt("scroll_y", scroll.getScrollY()); super.onSaveInstanceState(out); }
    @Override protected void onDestroy() { ui.removeCallbacksAndMessages(null); io.shutdownNow(); super.onDestroy(); }

    private void load() {
        load(false);
    }
    private void load(boolean retryHistory) {
        if (loading || route == null || isFinishing() || isDestroyed()) return;
        loading = true;
        if (status != null) status.setText(R.string.questions_loading);
        if (retry != null) retry.setVisibility(View.GONE);
        io.execute(() -> {
            try {
                QuestionDay loaded = Backend.questions(route, retryHistory);
                ui.post(() -> {
                    if (isFinishing() || isDestroyed()) return;
                    loading = false; day = loaded; render();
                    if (day.processing() && active) { ui.removeCallbacks(poll); ui.postDelayed(poll, 2500); }
                });
            } catch (Exception error) {
                ui.post(() -> {
                    if (isFinishing() || isDestroyed()) return;
                    loading = false; ui.removeCallbacks(poll);
                    if (error instanceof Backend.QuestionsInvalidated) {
                        day = null; focused = false; restoredPosition = -1; render();
                    }
                    status.setText(R.string.questions_load_failed); retry.setVisibility(View.VISIBLE);
                });
            }
        });
    }
    private void render() {
        int position = restoredPosition >= 0 ? restoredPosition : scroll.getScrollY(); page.removeAllViews(); dates.clear();
        Button back = new Button(this); back.setAllCaps(false); back.setText(R.string.back_to_daythread);
        back.setOnClickListener(view -> {
            startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP)); finish();
        }); page.addView(back);
        TextView title = text(getString(R.string.day_questions), 30, INK); title.setTypeface(Typeface.create("serif", Typeface.NORMAL));
        title.setPadding(0, dp(20), 0, dp(8)); page.addView(title);
        if (day != null) {
            String date = day.day().format(DateTimeFormatter.ofLocalizedDate(FormatStyle.FULL).withLocale(Locale.getDefault()));
            page.addView(text(date, 16, MUTED));
            page.addView(text(day.timezone().getId(), 12, MUTED));
            int selected = (int) day.questions().stream().filter(day::selected).count();
            if (selected > 0) {
                TextView group = text(getResources().getQuantityString(R.plurals.questions_from_click, selected, selected), 14, ACCENT);
                group.setPadding(0, dp(10), 0, dp(6)); page.addView(group);
            }
        }
        status = text(day == null ? getString(R.string.questions_loading) : day.processing() ? getString(R.string.questions_processing) : "", 14, MUTED);
        status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); page.addView(status);
        retry = new Button(this); retry.setAllCaps(false); retry.setText(R.string.retry); retry.setOnClickListener(view -> load(true));
        retry.setVisibility(day != null && !day.error().isEmpty() ? View.VISIBLE : View.GONE); page.addView(retry);
        if (day != null && !day.error().isEmpty()) status.setText(R.string.questions_load_failed);
        if (day != null && day.questions().isEmpty() && !day.processing() && day.error().isEmpty()) page.addView(text(getString(R.string.questions_empty), 16, MUTED));
        View selectedRow = null;
        if (day != null) for (QuestionDay.Question question : day.questions()) {
            boolean selected = day.selected(question); LinearLayout card = new LinearLayout(this); card.setOrientation(LinearLayout.VERTICAL);
            card.setPadding(dp(16), dp(14), dp(16), dp(16));
            GradientDrawable background = new GradientDrawable(); background.setColor(selected ? 0xffffeee3 : Color.WHITE);
            background.setCornerRadius(dp(14)); if (selected) background.setStroke(dp(1), ACCENT); card.setBackground(background);
            LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, -2); layout.setMargins(0, dp(12), 0, 0); page.addView(card, layout);
            if (selected) { card.addView(text(getString(R.string.question_from_click), 12, ACCENT)); if (selectedRow == null) selectedRow = card; }
            TextView label = text(question.question(), 18, INK); label.setTypeface(null, Typeface.BOLD); label.setTextIsSelectable(true); card.addView(label);
            TextView time = text("", 13, MUTED); time.setPadding(0, dp(4), 0, dp(8)); card.addView(time); dates.add(new TimeLabel(time, question.askedAt()));
            if (!question.answer().isEmpty()) { TextView answer = text(question.answer(), 16, INK); answer.setTextIsSelectable(true); card.addView(answer); }
        }
        updateTimes();
        View focus = selectedRow;
        scroll.post(() -> {
            if (!focused && focus != null) { scroll.scrollTo(0, Math.max(0, focus.getTop() - dp(12))); focused = true; }
            else scroll.scrollTo(0, position);
            if (day != null) restoredPosition = -1;
        });
    }
    private void updateTimes() {
        if (day == null) return;
        Instant now = Instant.now(); java.text.DateFormat clock = android.text.format.DateFormat.getTimeFormat(this);
        clock.setTimeZone(TimeZone.getTimeZone(day.timezone()));
        for (TimeLabel label : dates) {
            QuestionTime elapsed = QuestionTime.at(label.askedAt, now);
            label.view.setText(switch (elapsed.kind()) {
                case JUST_NOW -> getString(R.string.question_just_now);
                case MINUTES_AGO -> getResources().getQuantityString(R.plurals.question_minutes_ago, elapsed.minutes(), elapsed.minutes());
                case CLOCK -> clock.format(Date.from(label.askedAt));
            });
        }
    }
    private TextView text(String value, int size, int color) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color); return view;
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
}

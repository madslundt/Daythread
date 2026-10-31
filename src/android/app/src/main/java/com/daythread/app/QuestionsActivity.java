package com.daythread.app;

import android.app.Activity;
import android.app.DatePickerDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.*;
import android.view.View;
import android.widget.*;
import com.daythread.app.core.*;
import java.time.Instant;
import java.time.LocalDate;
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
    private final Set<String> expandedAnswers = new HashSet<>();
    private record TimeLabel(TextView view, Instant askedAt) {}
    private QuestionsRoute route;
    private QuestionDay day;
    private ScrollView scroll;
    private LinearLayout page;
    private TextView status;
    private Button retry;
    private boolean active, loading, focused;
    private volatile int requestGeneration;
    private int restoredPosition = -1;
    private final Runnable poll = this::load;
    private final Runnable clock = new Runnable() {
        @Override public void run() { updateTimes(); if (active) ui.postDelayed(this, 30000); }
    };

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        try { route = QuestionsRoute.parse(saved == null ? getIntent().getStringExtra(ROUTE) : saved.getString(ROUTE, getIntent().getStringExtra(ROUTE))); }
        catch (RuntimeException error) { finish(); return; }
        if (saved != null) {
            focused = saved.getBoolean("focused", false); restoredPosition = saved.getInt("scroll_y", 0);
            ArrayList<String> expanded = saved.getStringArrayList("expanded_answers");
            if (expanded != null) expandedAnswers.addAll(expanded);
        }
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
    @Override protected void onSaveInstanceState(Bundle out) { out.putBoolean("focused", focused); out.putStringArrayList("expanded_answers", new ArrayList<>(expandedAnswers)); if (route != null) out.putString(ROUTE, route.json().toString()); if (scroll != null) out.putInt("scroll_y", scroll.getScrollY()); super.onSaveInstanceState(out); }
    @Override protected void onDestroy() { ui.removeCallbacksAndMessages(null); io.shutdownNow(); super.onDestroy(); }

    private void load() {
        load(false);
    }
    private void load(boolean retryHistory) {
        if (loading || route == null || isFinishing() || isDestroyed()) return;
        loading = true;
        QuestionsRoute requested = route; int generation = requestGeneration;
        if (status != null) status.setText(R.string.questions_loading);
        if (retry != null) retry.setVisibility(View.GONE);
        io.execute(() -> {
            if (generation != requestGeneration) return;
            try {
                QuestionDay loaded = Backend.questions(requested, retryHistory);
                ui.post(() -> {
                    if (isFinishing() || isDestroyed() || generation != requestGeneration || !requested.equals(route)) return;
                    loading = false; day = loaded; render();
                    if (day.processing() && active) { ui.removeCallbacks(poll); ui.postDelayed(poll, 2500); }
                });
            } catch (Exception error) {
                ui.post(() -> {
                    if (isFinishing() || isDestroyed() || generation != requestGeneration || !requested.equals(route)) return;
                    loading = false; ui.removeCallbacks(poll);
                    if (error instanceof Backend.QuestionsInvalidated) {
                        day = null; focused = false; restoredPosition = -1; render();
                    }
                    status.setText(R.string.questions_load_failed); retry.setVisibility(View.VISIBLE);
                });
            }
        });
    }
    private void openDay(LocalDate chosen) {
        route = chosen == null ? QuestionsRoute.today(route.destination()) : QuestionsRoute.forDay(route.destination(), chosen);
        expandedAnswers.clear();
        requestGeneration++; ui.removeCallbacks(poll); loading = false; day = null; focused = false; restoredPosition = -1;
        getIntent().putExtra(ROUTE, route.json().toString()); render(); load();
    }
    private void chooseDay() {
        LocalDate selected = day != null ? day.day() : route.day() != null ? route.day() : LocalDate.now();
        DatePickerDialog picker = new DatePickerDialog(this, (view, year, month, date) -> openDay(LocalDate.of(year, month + 1, date)),
            selected.getYear(), selected.getMonthValue() - 1, selected.getDayOfMonth());
        picker.setTitle(R.string.questions_choose_day); picker.show();
    }
    private void render() {
        int position = restoredPosition >= 0 ? restoredPosition : scroll.getScrollY(); page.removeAllViews(); dates.clear();
        Button back = new Button(this); back.setAllCaps(false); back.setText(R.string.back_to_daythread);
        back.setOnClickListener(view -> {
            startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP)); finish();
        }); page.addView(back);
        TextView title = text(getString(R.string.day_questions), 30, INK); title.setTypeface(Typeface.create("serif", Typeface.NORMAL));
        title.setPadding(0, dp(20), 0, dp(8)); page.addView(title);
        LinearLayout dayControls = new LinearLayout(this);
        Button today = new Button(this); today.setAllCaps(false); today.setText(R.string.questions_today); today.setOnClickListener(view -> openDay(null));
        Button chooseDate = new Button(this); chooseDate.setAllCaps(false); chooseDate.setText(R.string.questions_choose_day); chooseDate.setOnClickListener(view -> chooseDay());
        dayControls.addView(today, new LinearLayout.LayoutParams(0, -2, 1)); dayControls.addView(chooseDate, new LinearLayout.LayoutParams(0, -2, 1)); page.addView(dayControls);
        if (day != null) {
            String date = day.day().format(DateTimeFormatter.ofLocalizedDate(FormatStyle.FULL).withLocale(Locale.getDefault()));
            page.addView(text(date, 16, MUTED));
            page.addView(text(day.timezone().getId(), 12, MUTED));
        }
        status = text(day == null ? getString(R.string.questions_loading) : day.processing() ? getString(R.string.questions_processing) : "", 14, MUTED);
        status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); page.addView(status);
        retry = new Button(this); retry.setAllCaps(false); retry.setText(R.string.retry); retry.setOnClickListener(view -> load(true));
        retry.setVisibility(day != null && !day.error().isEmpty() ? View.VISIBLE : View.GONE); page.addView(retry);
        if (day != null && !day.error().isEmpty()) status.setText(R.string.questions_load_failed);
        if (day != null && day.visibleGroups().isEmpty() && !day.processing() && day.error().isEmpty()) page.addView(text(getString(R.string.questions_empty), 16, MUTED));
        View selectedRow = null;
        if (day != null) for (QuestionDay.Group group : day.visibleGroups()) {
            boolean selected = day.selected(group); LinearLayout card = new LinearLayout(this); card.setOrientation(LinearLayout.VERTICAL);
            card.setTag("question-group:" + group.jobID());
            card.setPadding(dp(16), dp(14), dp(16), dp(16));
            GradientDrawable background = new GradientDrawable(); background.setColor(selected ? 0xffffeee3 : Color.WHITE);
            background.setCornerRadius(dp(14)); if (selected) background.setStroke(dp(1), ACCENT); card.setBackground(background);
            LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, -2); layout.setMargins(0, dp(12), 0, 0); page.addView(card, layout);
            if (selected) { card.addView(text(getString(R.string.question_from_click), 12, ACCENT)); if (selectedRow == null) selectedRow = card; }
            TextView count = text(getResources().getQuantityString(R.plurals.question_count, group.questionCount(), group.questionCount()), 16, selected ? ACCENT : INK);
            count.setTypeface(null, Typeface.BOLD); card.addView(count);
            TextView clickedAt = text("", 13, MUTED); clickedAt.setPadding(0, dp(4), 0, dp(4)); card.addView(clickedAt);
            dates.add(new TimeLabel(clickedAt, group.end()));
            boolean first = true;
            for (QuestionDay.Question question : group.questions()) {
                LinearLayout row = new LinearLayout(this); row.setOrientation(LinearLayout.VERTICAL); row.setTag(group.rowKey(question));
                row.setPadding(0, dp(12), 0, dp(8)); card.addView(row);
                if (!first) {
                    View divider = new View(this); divider.setBackgroundColor(selected ? 0xffe9cbbc : 0xffeae5df);
                    row.addView(divider, new LinearLayout.LayoutParams(-1, dp(1)));
                }
                first = false;
                TextView label = text(question.question(), 18, INK); label.setTypeface(null, Typeface.BOLD); label.setTextIsSelectable(true); row.addView(label);
                TextView time = text("", 13, MUTED); time.setPadding(0, dp(4), 0, dp(8)); row.addView(time); dates.add(new TimeLabel(time, question.askedAt()));
                if (!question.answer().isEmpty()) {
                    String answerKey = group.rowKey(question);
                    TextView answer = text(question.answer(), 16, INK);
                    TextView hint = text("", 14, ACCENT); hint.setPadding(0, dp(6), 0, dp(6)); hint.setVisibility(View.GONE);
                    answerPreview(answer, expandedAnswers.contains(answerKey));
                    View.OnClickListener toggle = view -> {
                        if (hint.getVisibility() != View.VISIBLE) return;
                        boolean expanded = !expandedAnswers.remove(answerKey);
                        if (expanded) expandedAnswers.add(answerKey);
                        answerPreview(answer, expanded);
                        answer.post(() -> updateAnswerHint(answer, hint));
                    };
                    answer.setOnClickListener(toggle); hint.setOnClickListener(toggle);
                    answer.addOnLayoutChangeListener((view, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom) -> updateAnswerHint(answer, hint));
                    answer.setOnCreateContextMenuListener((menu, view, info) ->
                        menu.add(android.R.string.copy).setOnMenuItemClickListener(item -> {
                            ClipboardManager clipboard = getSystemService(ClipboardManager.class);
                            if (clipboard != null) clipboard.setPrimaryClip(ClipData.newPlainText(null, question.answer()));
                            return true;
                        }));
                    row.addView(answer); row.addView(hint);
                }
            }
        }
        updateTimes();
        View focus = selectedRow;
        scroll.post(() -> {
            if (!focused && focus != null) { scroll.scrollTo(0, Math.max(0, focus.getTop() - dp(12))); focused = true; }
            else scroll.scrollTo(0, position);
            if (day != null) restoredPosition = -1;
        });
    }
    private void answerPreview(TextView answer, boolean expanded) {
        answer.setMaxLines(expanded ? Integer.MAX_VALUE : 3);
        answer.setEllipsize(expanded ? null : android.text.TextUtils.TruncateAt.END);
        answer.setClickable(true);
        answer.setLongClickable(true);
    }
    private void updateAnswerHint(TextView answer, TextView hint) {
        android.text.Layout layout = answer.getLayout();
        if (layout == null) return;
        boolean overflow = layout.getLineCount() > 3;
        for (int line = 0; line < Math.min(3, layout.getLineCount()); line++) overflow |= layout.getEllipsisCount(line) > 0;
        hint.setText(answer.getMaxLines() > 3 ? R.string.question_show_less : R.string.question_read_more);
        hint.setVisibility(overflow ? View.VISIBLE : View.GONE);
        answer.setClickable(overflow);
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

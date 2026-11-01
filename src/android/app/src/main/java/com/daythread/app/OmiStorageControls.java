package com.daythread.app;

import android.content.Context;
import android.content.Intent;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Explicit copy controls; starting live capture never implicitly drains pendant storage. */
final class OmiStorageControls {
    static Runnable add(Context context, LinearLayout page) {
        TextView title = new TextView(context); title.setText(R.string.ring_title); title.setTextSize(18); page.addView(title);
        TextView help = new TextView(context); help.setText(R.string.ring_help); page.addView(help);
        TextView status = new TextView(context); status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); page.addView(status);
        Button check = new Button(context); check.setText(R.string.ring_check); page.addView(check);
        Button copy = new Button(context); copy.setText(R.string.ring_copy); page.addView(copy);
        check.setOnClickListener(view -> context.startService(new Intent(context, CaptureService.class).setAction("omi_storage_pause_check")));
        copy.setOnClickListener(view -> context.startService(new Intent(context, CaptureService.class).setAction("omi_storage").putExtra("copy", true)));
        Runnable refresh = () -> {
            status.setText(CaptureService.omiStorage);
            boolean available = CaptureService.active && "omi".equals(CaptureService.source);
            check.setEnabled(available); copy.setEnabled(available);
        };
        refresh.run(); return refresh;
    }
}

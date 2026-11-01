package com.daythread.app;

import android.content.Context;
import android.content.Intent;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.Switch;
import android.widget.TextView;

/** Displays confirmed pendant state; the capture service owns Bluetooth operations. */
final class OmiLightControls {
    private OmiLightControls() {}

    static Runnable add(Context context, LinearLayout page) {
        Switch toggle = new Switch(context); toggle.setText(R.string.omi_light);
        toggle.setMinHeight((int) (48 * context.getResources().getDisplayMetrics().density)); page.addView(toggle);
        TextView help = new TextView(context); help.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); page.addView(help);
        boolean[] refreshing = {false};
        Runnable refresh = () -> {
            OmiConnection.LightState state = CaptureService.omiLight;
            refreshing[0] = true;
            toggle.setChecked(state.ratio() != null && state.ratio() > 0);
            toggle.setEnabled(state.ratio() != null && !state.busy());
            refreshing[0] = false;
            help.setText(!state.error().isEmpty() ? state.error() : context.getString(state.busy() ? R.string.omi_light_updating
                : state.ratio() == null ? R.string.omi_light_connect : R.string.omi_light_help));
        };
        toggle.setOnCheckedChangeListener((view, enabled) -> {
            if (refreshing[0]) return;
            OmiConnection.LightState state = CaptureService.omiLight;
            refreshing[0] = true; toggle.setChecked(state.ratio() != null && state.ratio() > 0); refreshing[0] = false;
            if (state.ratio() == null || state.busy()) { refresh.run(); return; }
            toggle.setEnabled(false); help.setText(R.string.omi_light_updating);
            context.startService(new Intent(context, CaptureService.class).setAction("omi_light")
                .putExtra("enabled", enabled).putExtra("address", state.address()));
        });
        refresh.run();
        return refresh;
    }
}

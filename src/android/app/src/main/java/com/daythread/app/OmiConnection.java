package com.daythread.app;

import android.annotation.SuppressLint;
import android.app.AlarmManager;
import android.app.PendingIntent;
import android.bluetooth.*;
import android.content.*;
import android.os.Build;
import android.os.Handler;
import android.os.SystemClock;
import com.daythread.app.core.RetryBackoff;
import com.daythread.app.core.OmiHaptic;
import com.daythread.app.core.OmiLight;
import com.daythread.app.core.OmiChargingPolicy;
import com.daythread.app.core.Outbox;
import com.daythread.app.core.Destination;
import com.daythread.app.core.OmiRingProtocol;
import java.util.function.BooleanSupplier;
import java.util.*;

/** All GATT operations and callbacks run on the capture handler; Android permits one operation at a time. */
@SuppressLint("MissingPermission")
final class OmiConnection {
    static final UUID SERVICE = UUID.fromString("19b10000-e8f2-537e-4f6c-d104768a1214");
    static final UUID AUDIO = UUID.fromString("19b10001-e8f2-537e-4f6c-d104768a1214");
    static final UUID CODEC = UUID.fromString("19b10002-e8f2-537e-4f6c-d104768a1214");
    private static final UUID BUTTON_SERVICE = UUID.fromString("23ba7924-0000-1000-7450-346eac492e92");
    private static final UUID BUTTON = UUID.fromString("23ba7925-0000-1000-7450-346eac492e92");
    private static final UUID HAPTIC_SERVICE = UUID.fromString("cab1ab95-2ea5-4f4d-bb56-874b72cfc984");
    private static final UUID HAPTIC = UUID.fromString("cab1ab96-2ea5-4f4d-bb56-874b72cfc984");
    private static final UUID SETTINGS = UUID.fromString("19b10010-e8f2-537e-4f6c-d104768a1214");
    private static final UUID LIGHT = UUID.fromString("19b10011-e8f2-537e-4f6c-d104768a1214");
    private static final UUID CHARGING = UUID.fromString("19b10013-e8f2-537e-4f6c-d104768a1214");
    private static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    interface Listener {
        void codec(int codec) throws Exception;
        void audio(byte[] bytes);
        void button(byte[] bytes);
        void status(String text);
        void disconnected();
        void failure(Exception error);
        void light(LightState state);
        void charging(Boolean charging);
        void storage(String text);
    }
    record LightState(String address, Integer ratio, boolean busy, String error) {
        static LightState disconnected() { return new LightState("", null, false, ""); }
    }
    private final Context context;
    private final Handler handler;
    private final Listener listener;
    private final BluetoothDevice device;
    private enum Kind { READ, SUBSCRIBE, HAPTIC, LIGHT_READ, LIGHT_WRITE, CHARGING_READ, CHARGING_SUBSCRIBE, AUDIO_SUBSCRIBE, STORAGE }
    private record Operation(Kind kind, UUID characteristic, Runnable start) {}
    private final ArrayDeque<Operation> operations = new ArrayDeque<>();
    private BluetoothGatt gatt;
    private BluetoothGattCharacteristic haptic;
    private BluetoothGattCharacteristic light;
    private BluetoothGattCharacteristic charging;
    private BluetoothGattCharacteristic audio;
    private boolean audioEnabled = true;
    private Boolean audioSubscribed;
    private boolean audioSubscriptionPending, audioSubscriptionRequested;
    private final OmiStorageGatt storage;
    private final OmiLight lightValue = new OmiLight();
    private boolean lightReading;
    private boolean closed;
    private Runnable timeout;
    private boolean reconnecting;
    private final RetryBackoff retries = new RetryBackoff(10000, 300000);
    private final Runnable reconnect = this::reconnect;
    private final AlarmManager alarms;
    private final PendingIntent reconnectIntent;
    private final BroadcastReceiver bluetoothState = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR) == BluetoothAdapter.STATE_ON)
                handler.post(() -> { if (!closed && gatt == null) connect(); });
        }
    };

    OmiConnection(Context context, String address, Handler handler, Listener listener) {
        this.context = context; this.handler = handler; this.listener = listener;
        storage = new OmiStorageGatt(context, handler, new OmiStorageGatt.Host() {
            public void enqueue(UUID characteristic, Runnable start) {
                boolean idle = operations.isEmpty();
                operations.add(new Operation(Kind.STORAGE, characteristic, start));
                if (idle) startOperation();
            }
            public void next() { OmiConnection.this.next(); }
            public void abort() { dropStorage(); }
            public boolean audioPaused() { return !audioEnabled && !audioSubscriptionPending && Boolean.FALSE.equals(audioSubscribed); }
            public void status(String value) { listener.storage(value); }
        });
        device = context.getSystemService(BluetoothManager.class).getAdapter().getRemoteDevice(address);
        alarms = context.getSystemService(AlarmManager.class);
        reconnectIntent = PendingIntent.getService(context, 1704, new Intent(context, CaptureService.class).setAction("reconnect"), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        IntentFilter states = new IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED);
        // Bluetooth broadcasts can originate in the privileged Bluetooth app rather than the system UID.
        if (Build.VERSION.SDK_INT >= 33) context.registerReceiver(bluetoothState, states, Context.RECEIVER_EXPORTED);
        else context.registerReceiver(bluetoothState, states);
        connect();
    }
    private void connect() {
        if (closed) return;
        handler.removeCallbacks(reconnect); alarms.cancel(reconnectIntent);
        if (timeout != null) handler.removeCallbacks(timeout);
        listener.status(context.getString(R.string.connecting_omi));
        try {
            // After the initial direct attempt, let Android keep a passive pending connection.
            // It reconnects with the screen locked without a wake lock or repeated scan timers.
            gatt = device.connectGatt(context, reconnecting, callback, BluetoothDevice.TRANSPORT_LE, BluetoothDevice.PHY_LE_1M_MASK, handler);
            if (!reconnecting || gatt == null) deadline(30000);
        } catch (RuntimeException e) { reject(context.getString(R.string.scan_failed)); }
    }
    private void deadline(long millis) {
        deadline(millis, () -> retry(context.getString(R.string.bluetooth_timeout)));
    }
    private void deadline(long millis, Runnable expired) {
        if (timeout != null) handler.removeCallbacks(timeout);
        timeout = expired;
        handler.postDelayed(timeout, millis);
    }
    private void next() {
        if (timeout != null) handler.removeCallbacks(timeout);
        operations.poll();
        startOperation();
    }
    private boolean awaiting(Kind kind, UUID characteristic) {
        Operation head = operations.peek();
        return head != null && head.kind == kind && head.characteristic.equals(characteristic);
    }
    private void startOperation() {
        Operation head = operations.peek();
        if (head == null) return;
        if (head.kind == Kind.HAPTIC) deadline(3000, () -> { if (operations.peek() == head) dropHaptics(); });
        else if (head.kind == Kind.LIGHT_READ || head.kind == Kind.LIGHT_WRITE)
            deadline(3000, () -> { if (operations.peek() == head) dropLight(); });
        else if (head.kind == Kind.CHARGING_READ || head.kind == Kind.CHARGING_SUBSCRIBE)
            deadline(3000, () -> { if (operations.peek() == head) dropCharging(); });
        else if (head.kind == Kind.STORAGE)
            deadline(5000, () -> { if (operations.peek() == head) storage.fail(R.string.ring_failed); });
        else deadline(15000);
        head.start.run();
    }
    private void dropHaptics() {
        // A late response must not advance a later write. Disable this optional feature
        // until discovery runs again and leave audio/subscription operations intact.
        haptic = null;
        if (timeout != null) handler.removeCallbacks(timeout);
        operations.removeIf(operation -> operation.kind == Kind.HAPTIC);
        startOperation();
    }
    void vibrate(OmiHaptic preset) {
        byte[] value = preset.payload();
        BluetoothGatt current = gatt;
        BluetoothGattCharacteristic characteristic = haptic;
        if (closed || current == null || characteristic == null) return;
        boolean idle = operations.isEmpty();
        operations.add(new Operation(Kind.HAPTIC, HAPTIC, () -> {
            if (closed || current != gatt || characteristic != haptic) { next(); return; }
            try {
                boolean accepted;
                if (Build.VERSION.SDK_INT >= 33) accepted = current.writeCharacteristic(characteristic, value, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothStatusCodes.SUCCESS;
                else {
                    characteristic.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
                    characteristic.setValue(value);
                    accepted = current.writeCharacteristic(characteristic);
                }
                if (!accepted) dropHaptics();
            } catch (RuntimeException ignored) { dropHaptics(); }
        }));
        if (idle) startOperation();
    }
    void setAudioEnabled(boolean enabled) {
        if (enabled && storage.busy()) storage.fail(R.string.ring_pause);
        audioEnabled = enabled;
        reconcileAudioSubscription();
    }
    private void reconcileAudioSubscription() {
        if (closed || gatt == null || audio == null || audioSubscriptionPending || Boolean.valueOf(audioEnabled).equals(audioSubscribed)) return;
        BluetoothGatt current = gatt;
        BluetoothGattCharacteristic characteristic = audio;
        boolean idle = operations.isEmpty();
        audioSubscriptionPending = true;
        operations.add(new Operation(Kind.AUDIO_SUBSCRIBE, AUDIO, () -> {
            audioSubscriptionRequested = audioEnabled;
            BluetoothGattDescriptor descriptor = characteristic.getDescriptor(CCCD);
            if (!current.setCharacteristicNotification(characteristic, audioSubscriptionRequested)) {
                retry(context.getString(R.string.bluetooth_subscription_failed)); return;
            }
            descriptor.setValue(!audioSubscriptionRequested ? BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE
                : (characteristic.getProperties() & BluetoothGattCharacteristic.PROPERTY_NOTIFY) != 0
                    ? BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE : BluetoothGattDescriptor.ENABLE_INDICATION_VALUE);
            if (!current.writeDescriptor(descriptor)) retry(context.getString(R.string.bluetooth_subscription_failed));
        }));
        if (idle) startOperation();
    }
    private void dropCharging() {
        charging = null; listener.charging(null);
        if (timeout != null) handler.removeCallbacks(timeout);
        operations.removeIf(operation -> operation.kind == Kind.CHARGING_READ || operation.kind == Kind.CHARGING_SUBSCRIBE);
        startOperation();
    }
    private void setupCharging(BluetoothGatt current) {
        BluetoothGattService settings = current.getService(SETTINGS);
        charging = settings == null ? null : settings.getCharacteristic(CHARGING);
        BluetoothGattCharacteristic characteristic = charging;
        if (characteristic == null) { listener.charging(null); return; }
        if (characteristic.getDescriptor(CCCD) != null && (characteristic.getProperties()
                & (BluetoothGattCharacteristic.PROPERTY_NOTIFY | BluetoothGattCharacteristic.PROPERTY_INDICATE)) != 0) {
            operations.add(new Operation(Kind.CHARGING_SUBSCRIBE, CHARGING, () -> {
                try {
                    BluetoothGattDescriptor descriptor = characteristic.getDescriptor(CCCD);
                    if (!current.setCharacteristicNotification(characteristic, true)) { dropCharging(); return; }
                    descriptor.setValue((characteristic.getProperties() & BluetoothGattCharacteristic.PROPERTY_NOTIFY) != 0
                        ? BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE : BluetoothGattDescriptor.ENABLE_INDICATION_VALUE);
                    if (!current.writeDescriptor(descriptor)) dropCharging();
                } catch (RuntimeException e) { dropCharging(); }
            }));
        }
        if ((characteristic.getProperties() & BluetoothGattCharacteristic.PROPERTY_READ) != 0) {
            operations.add(new Operation(Kind.CHARGING_READ, CHARGING, () -> {
                try { if (!current.readCharacteristic(characteristic)) dropCharging(); }
                catch (RuntimeException e) { dropCharging(); }
            }));
        }
    }
    private void publishLight(String error) {
        listener.light(new LightState(device.getAddress(), lightValue.ratio(), lightReading || lightValue.pending(), error));
    }
    private void clearCaptureCharacteristics() {
        storage.disable();
        audio = null; audioSubscribed = null; audioSubscriptionPending = false;
        charging = null; listener.charging(null);
    }
    private void dropStorage() {
        storage.disable();
        if (timeout != null) handler.removeCallbacks(timeout);
        operations.removeIf(operation -> operation.kind == Kind.STORAGE);
        startOperation();
    }
    void copyStorage(boolean copy, Outbox outbox, Destination destination, String device, int codec, BooleanSupplier allowed, boolean waitForPause) {
        if (!waitForPause) { storage.begin(copy, outbox, destination, device, codec, allowed); return; }
        long deadline = SystemClock.elapsedRealtime() + 10000;
        BluetoothGatt connection = gatt;
        handler.post(new Runnable() {
            public void run() {
                if (closed || connection != gatt) return;
                if (!allowed.getAsBoolean() || SystemClock.elapsedRealtime() >= deadline) {
                    listener.storage(context.getString(R.string.ring_pause)); return;
                }
                // A policy pause stops admission immediately. Wait for the actual CCCD
                // unsubscribe acknowledgement before issuing any storage command.
                if (audioEnabled || audioSubscriptionPending || !Boolean.FALSE.equals(audioSubscribed)) {
                    handler.postDelayed(this, 100); return;
                }
                storage.begin(false, outbox, destination, device, codec, allowed);
            }
        });
    }
    private void clearLight() {
        light = null; lightValue.clear(); lightReading = false;
        listener.light(LightState.disconnected());
    }
    private void dropLight() {
        // Ignore late optional callbacks and leave capture/subscription operations intact.
        light = null; lightValue.clear(); lightReading = false;
        if (timeout != null) handler.removeCallbacks(timeout);
        operations.removeIf(operation -> operation.kind == Kind.LIGHT_READ || operation.kind == Kind.LIGHT_WRITE);
        publishLight(context.getString(R.string.omi_light_failed));
        startOperation();
    }
    private void readLight() {
        BluetoothGatt current = gatt;
        BluetoothGattCharacteristic characteristic = light;
        if (characteristic == null) return;
        lightReading = true; publishLight("");
        operations.add(new Operation(Kind.LIGHT_READ, LIGHT, () -> {
            if (closed || current != gatt || characteristic != light) { next(); return; }
            try { if (!current.readCharacteristic(characteristic)) dropLight(); }
            catch (RuntimeException ignored) { dropLight(); }
        }));
    }
    void setLight(boolean enabled) {
        BluetoothGatt current = gatt;
        BluetoothGattCharacteristic characteristic = light;
        if (closed || current == null || characteristic == null || lightReading || lightValue.ratio() == null || lightValue.pending()) return;
        byte[] value = lightValue.command(enabled);
        publishLight("");
        boolean idle = operations.isEmpty();
        operations.add(new Operation(Kind.LIGHT_WRITE, LIGHT, () -> {
            if (closed || current != gatt || characteristic != light) { next(); return; }
            try {
                boolean accepted;
                if (Build.VERSION.SDK_INT >= 33) accepted = current.writeCharacteristic(characteristic, value, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothStatusCodes.SUCCESS;
                else {
                    characteristic.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT); characteristic.setValue(value);
                    accepted = current.writeCharacteristic(characteristic);
                }
                if (!accepted) dropLight();
            } catch (RuntimeException ignored) { dropLight(); }
        }));
        if (idle) startOperation();
    }
    private void retry(String reason) {
        if (closed) return;
        listener.status(reason); listener.disconnected(); operations.clear(); haptic = null; clearCaptureCharacteristics(); clearLight();
        if (closed) return;
        if (timeout != null) handler.removeCallbacks(timeout);
        if (gatt != null) { BluetoothGatt old = gatt; gatt = null; try { old.disconnect(); } catch (RuntimeException ignored) {}
            try { old.close(); } catch (RuntimeException ignored) {} }
        if (!reconnecting) { reconnecting = true; connect(); return; }
        long delay = retries.fail(SystemClock.elapsedRealtime());
        handler.removeCallbacks(reconnect); handler.postDelayed(reconnect, delay);
        // Error recovery must also run when the CPU sleeps during the delay; Doze can defer it.
        alarms.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, SystemClock.elapsedRealtime() + delay, reconnectIntent);
    }
    void reconnect() { if (!closed && gatt == null && retries.ready(SystemClock.elapsedRealtime())) connect(); }
    private void read(BluetoothGattCharacteristic characteristic) {
        if (characteristic == null) return;
        operations.add(new Operation(Kind.READ, characteristic.getUuid(), () -> { if (!gatt.readCharacteristic(characteristic)) retry(context.getString(R.string.bluetooth_read_failed)); }));
    }
    private void subscribe(BluetoothGattCharacteristic characteristic) {
        if (characteristic == null) return;
        if (!characteristic.getUuid().equals(AUDIO) && (characteristic.getDescriptor(CCCD) == null
            || (characteristic.getProperties() & (BluetoothGattCharacteristic.PROPERTY_NOTIFY | BluetoothGattCharacteristic.PROPERTY_INDICATE)) == 0)) return;
        operations.add(new Operation(Kind.SUBSCRIBE, characteristic.getUuid(), () -> {
            BluetoothGattDescriptor descriptor = characteristic.getDescriptor(CCCD);
            if (descriptor == null || !gatt.setCharacteristicNotification(characteristic, true)) {
                retry(context.getString(R.string.bluetooth_subscription_failed)); return;
            }
            descriptor.setValue((characteristic.getProperties() & BluetoothGattCharacteristic.PROPERTY_NOTIFY) != 0
                ? BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE : BluetoothGattDescriptor.ENABLE_INDICATION_VALUE);
            if (!gatt.writeDescriptor(descriptor)) retry(context.getString(R.string.bluetooth_subscription_failed));
        }));
    }
    private void reject(String message) { listener.status(message); close(); listener.failure(new java.io.IOException(message)); }
    void close() {
        if (closed) return;
        closed = true;
        context.unregisterReceiver(bluetoothState);
        handler.removeCallbacks(reconnect); alarms.cancel(reconnectIntent);
        if (timeout != null) handler.removeCallbacks(timeout);
        if (gatt != null) { BluetoothGatt old = gatt; gatt = null; try { old.disconnect(); } catch (RuntimeException ignored) {}
            try { old.close(); } catch (RuntimeException ignored) {} }
        operations.clear(); haptic = null; clearCaptureCharacteristics(); clearLight();
    }
    private final BluetoothGattCallback callback = new BluetoothGattCallback() {
        @Override public void onConnectionStateChange(BluetoothGatt current, int status, int state) {
            if (closed || current != gatt) return;
            if (status == BluetoothGatt.GATT_SUCCESS && state == BluetoothProfile.STATE_CONNECTED) {
                deadline(15000);
                if (!current.requestMtu(185) && !current.discoverServices()) retry(context.getString(R.string.bluetooth_read_failed));
            } else if (state == BluetoothProfile.STATE_DISCONNECTED && status == BluetoothGatt.GATT_SUCCESS && reconnecting) {
                // Keep the registered autoConnect GATT: Android owns normal link recovery.
                if (timeout != null) handler.removeCallbacks(timeout);
                operations.clear(); haptic = null; clearCaptureCharacteristics(); clearLight(); listener.status(context.getString(R.string.omi_reconnecting)); listener.disconnected();
            } else if (state == BluetoothProfile.STATE_DISCONNECTED || status != BluetoothGatt.GATT_SUCCESS) retry(context.getString(R.string.omi_reconnecting));
        }
        @Override public void onMtuChanged(BluetoothGatt current, int mtu, int status) {
            if (current == gatt && !closed && !current.discoverServices()) retry(context.getString(R.string.bluetooth_read_failed));
        }
        @Override public void onServicesDiscovered(BluetoothGatt current, int status) {
            if (closed || current != gatt) return;
            BluetoothGattService service = current.getService(SERVICE);
            BluetoothGattCharacteristic audio = service == null ? null : service.getCharacteristic(AUDIO);
            BluetoothGattCharacteristic codec = service == null ? null : service.getCharacteristic(CODEC);
            if (status != BluetoothGatt.GATT_SUCCESS || audio == null || codec == null
                || (codec.getProperties() & BluetoothGattCharacteristic.PROPERTY_READ) == 0
                || audio.getDescriptor(CCCD) == null
                || (audio.getProperties() & (BluetoothGattCharacteristic.PROPERTY_NOTIFY | BluetoothGattCharacteristic.PROPERTY_INDICATE)) == 0) {
                reject(context.getString(R.string.omi_incompatible)); return;
            }
            operations.clear();
            BluetoothGattService feedback = current.getService(HAPTIC_SERVICE);
            haptic = feedback == null ? null : feedback.getCharacteristic(HAPTIC);
            if (haptic != null && (haptic.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE) == 0) haptic = null;
            read(codec);
            BluetoothGattService button = current.getService(BUTTON_SERVICE);
            subscribe(button == null ? null : button.getCharacteristic(BUTTON));
            clearCaptureCharacteristics(); clearLight();
            storage.discover(current);
            setupCharging(current);
            OmiConnection.this.audio = audio;
            reconcileAudioSubscription();
            BluetoothGattService settings = current.getService(SETTINGS);
            light = settings == null ? null : settings.getCharacteristic(LIGHT);
            if (light != null && (light.getProperties() & (BluetoothGattCharacteristic.PROPERTY_READ | BluetoothGattCharacteristic.PROPERTY_WRITE))
                    != (BluetoothGattCharacteristic.PROPERTY_READ | BluetoothGattCharacteristic.PROPERTY_WRITE)) light = null;
            if (light != null) readLight(); else publishLight(context.getString(R.string.omi_light_unsupported));
            startOperation();
        }
        @Override public void onCharacteristicRead(BluetoothGatt current, BluetoothGattCharacteristic characteristic, int status) {
            if (current == gatt && !closed && awaiting(Kind.STORAGE, characteristic.getUuid())) {
                storage.readResult(characteristic, status); return;
            }
            if (current == gatt && !closed && charging != null && awaiting(Kind.CHARGING_READ, characteristic.getUuid())) {
                Boolean value = status == BluetoothGatt.GATT_SUCCESS ? OmiChargingPolicy.decode(characteristic.getValue()) : null;
                if (value == null) { dropCharging(); return; }
                listener.charging(value); next(); return;
            }
            if (current == gatt && !closed && light != null && awaiting(Kind.LIGHT_READ, characteristic.getUuid())) {
                try {
                    if (status != BluetoothGatt.GATT_SUCCESS) { dropLight(); return; }
                    lightValue.accept(characteristic.getValue()); lightReading = false; publishLight(""); next();
                } catch (IllegalArgumentException e) { dropLight(); }
                return;
            }
            if (current != gatt || closed || !awaiting(Kind.READ, characteristic.getUuid())) return;
            try {
                byte[] value = characteristic.getValue();
                if (status != BluetoothGatt.GATT_SUCCESS || value == null || value.length == 0) throw new IllegalStateException();
                if (characteristic.getUuid().equals(CODEC)) listener.codec(value[0] & 255);
                next();
            } catch (Exception e) { reject(context.getString(R.string.omi_codec_failed)); }
        }
        @Override public void onDescriptorWrite(BluetoothGatt current, BluetoothGattDescriptor descriptor, int status) {
            if (current == gatt && !closed && awaiting(Kind.STORAGE, descriptor.getCharacteristic().getUuid())) {
                storage.subscribed(status); return;
            }
            if (current == gatt && !closed && awaiting(Kind.AUDIO_SUBSCRIBE, descriptor.getCharacteristic().getUuid())) {
                if (status != BluetoothGatt.GATT_SUCCESS) { retry(context.getString(R.string.bluetooth_subscription_failed)); return; }
                audioSubscribed = audioSubscriptionRequested; audioSubscriptionPending = false;
                retries.reset(); listener.status(context.getString(audioSubscriptionRequested ? R.string.receiving_omi : R.string.capture_paused_controls));
                next(); reconcileAudioSubscription(); return;
            }
            if (current == gatt && !closed && charging != null && awaiting(Kind.CHARGING_SUBSCRIBE, descriptor.getCharacteristic().getUuid())) {
                if (status == BluetoothGatt.GATT_SUCCESS) next(); else dropCharging();
                return;
            }
            if (current != gatt || closed || !awaiting(Kind.SUBSCRIBE, descriptor.getCharacteristic().getUuid())) return;
            if (status != BluetoothGatt.GATT_SUCCESS) { retry(context.getString(R.string.bluetooth_subscription_failed)); return; }
            if (descriptor.getCharacteristic().getUuid().equals(AUDIO)) { retries.reset(); listener.status(context.getString(R.string.receiving_omi)); }
            next();
        }
        @Override public void onCharacteristicWrite(BluetoothGatt current, BluetoothGattCharacteristic characteristic, int status) {
            if (current == gatt && !closed && awaiting(Kind.STORAGE, characteristic.getUuid())) {
                storage.written(status); return;
            }
            if (current == gatt && !closed && light != null && awaiting(Kind.LIGHT_WRITE, characteristic.getUuid())) {
                if (status == BluetoothGatt.GATT_SUCCESS) { readLight(); next(); } else dropLight();
                return;
            }
            if (current != gatt || closed || !awaiting(Kind.HAPTIC, characteristic.getUuid())) return;
            if (status == BluetoothGatt.GATT_SUCCESS) next(); else dropHaptics();
        }
        @Override public void onCharacteristicChanged(BluetoothGatt current, BluetoothGattCharacteristic characteristic) {
            if (current != gatt || closed) return;
            byte[] value = characteristic.getValue();
            if (value == null) return;
            if (characteristic.getUuid().equals(AUDIO)) listener.audio(value.clone());
            else if (characteristic.getUuid().equals(BUTTON)) listener.button(value.clone());
            else if (characteristic.getUuid().equals(OmiRingProtocol.CONTROL)) storage.receive(value.clone());
            else if (characteristic == charging) {
                Boolean state = OmiChargingPolicy.decode(value);
                if (state != null) listener.charging(state);
            }
        }
    };
}

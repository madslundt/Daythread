package com.daythread.app;

import android.annotation.SuppressLint;
import android.bluetooth.*;
import android.content.Context;
import android.os.Handler;
import java.util.*;

/** All GATT operations and callbacks run on the capture handler; Android permits one operation at a time. */
@SuppressLint("MissingPermission")
final class OmiConnection {
    static final UUID SERVICE = UUID.fromString("19b10000-e8f2-537e-4f6c-d104768a1214");
    static final UUID AUDIO = UUID.fromString("19b10001-e8f2-537e-4f6c-d104768a1214");
    static final UUID CODEC = UUID.fromString("19b10002-e8f2-537e-4f6c-d104768a1214");
    private static final UUID BUTTON_SERVICE = UUID.fromString("23ba7924-0000-1000-7450-346eac492e92");
    private static final UUID BUTTON = UUID.fromString("23ba7925-0000-1000-7450-346eac492e92");
    private static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    interface Listener {
        void codec(int codec) throws Exception;
        void audio(byte[] bytes);
        void button(byte[] bytes);
        void status(String text);
        void disconnected();
        void failure(Exception error);
    }
    private final Context context;
    private final Handler handler;
    private final Listener listener;
    private final BluetoothDevice device;
    private final ArrayDeque<Runnable> operations = new ArrayDeque<>();
    private BluetoothGatt gatt;
    private boolean closed;
    private Runnable timeout;

    OmiConnection(Context context, String address, Handler handler, Listener listener) {
        this.context = context; this.handler = handler; this.listener = listener;
        device = context.getSystemService(BluetoothManager.class).getAdapter().getRemoteDevice(address);
        connect();
    }
    private void connect() {
        if (closed) return;
        listener.status(context.getString(R.string.connecting_omi));
        try {
            gatt = device.connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE, BluetoothDevice.PHY_LE_1M_MASK, handler);
            deadline(30000);
        } catch (RuntimeException e) { reject(context.getString(R.string.scan_failed)); }
    }
    private void deadline(long millis) {
        if (timeout != null) handler.removeCallbacks(timeout);
        timeout = () -> retry(context.getString(R.string.bluetooth_timeout));
        handler.postDelayed(timeout, millis);
    }
    private void next() {
        if (timeout != null) handler.removeCallbacks(timeout);
        operations.poll();
        if (!operations.isEmpty()) { deadline(15000); operations.peek().run(); }
    }
    private void retry(String reason) {
        if (closed) return;
        listener.status(reason); listener.disconnected(); operations.clear();
        if (timeout != null) handler.removeCallbacks(timeout);
        if (gatt != null) { BluetoothGatt old = gatt; gatt = null; try { old.disconnect(); } catch (RuntimeException ignored) {}
            try { old.close(); } catch (RuntimeException ignored) {} }
        handler.postDelayed(this::connect, 10000);
    }
    private void read(BluetoothGattCharacteristic characteristic) {
        if (characteristic == null) return;
        operations.add(() -> { if (!gatt.readCharacteristic(characteristic)) retry(context.getString(R.string.bluetooth_read_failed)); });
    }
    private void subscribe(BluetoothGattCharacteristic characteristic) {
        if (characteristic == null) return;
        if (!characteristic.getUuid().equals(AUDIO) && (characteristic.getDescriptor(CCCD) == null
            || (characteristic.getProperties() & (BluetoothGattCharacteristic.PROPERTY_NOTIFY | BluetoothGattCharacteristic.PROPERTY_INDICATE)) == 0)) return;
        operations.add(() -> {
            BluetoothGattDescriptor descriptor = characteristic.getDescriptor(CCCD);
            if (descriptor == null || !gatt.setCharacteristicNotification(characteristic, true)) {
                retry(context.getString(R.string.bluetooth_subscription_failed)); return;
            }
            descriptor.setValue((characteristic.getProperties() & BluetoothGattCharacteristic.PROPERTY_NOTIFY) != 0
                ? BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE : BluetoothGattDescriptor.ENABLE_INDICATION_VALUE);
            if (!gatt.writeDescriptor(descriptor)) retry(context.getString(R.string.bluetooth_subscription_failed));
        });
    }
    private void reject(String message) { listener.status(message); close(); listener.failure(new java.io.IOException(message)); }
    void close() {
        closed = true;
        if (timeout != null) handler.removeCallbacks(timeout);
        if (gatt != null) { BluetoothGatt old = gatt; gatt = null; try { old.disconnect(); } catch (RuntimeException ignored) {}
            try { old.close(); } catch (RuntimeException ignored) {} }
        operations.clear();
    }
    private final BluetoothGattCallback callback = new BluetoothGattCallback() {
        @Override public void onConnectionStateChange(BluetoothGatt current, int status, int state) {
            if (closed || current != gatt) return;
            if (status == BluetoothGatt.GATT_SUCCESS && state == BluetoothProfile.STATE_CONNECTED) {
                deadline(15000);
                if (!current.requestMtu(185) && !current.discoverServices()) retry(context.getString(R.string.bluetooth_read_failed));
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
            operations.clear(); read(codec);
            BluetoothGattService button = current.getService(BUTTON_SERVICE);
            subscribe(button == null ? null : button.getCharacteristic(BUTTON));
            subscribe(audio);
            deadline(15000); operations.peek().run();
        }
        @Override public void onCharacteristicRead(BluetoothGatt current, BluetoothGattCharacteristic characteristic, int status) {
            if (current != gatt || closed) return;
            try {
                byte[] value = characteristic.getValue();
                if (status != BluetoothGatt.GATT_SUCCESS || value == null || value.length == 0) throw new IllegalStateException();
                if (characteristic.getUuid().equals(CODEC)) listener.codec(value[0] & 255);
                next();
            } catch (Exception e) { reject(context.getString(R.string.omi_codec_failed)); }
        }
        @Override public void onDescriptorWrite(BluetoothGatt current, BluetoothGattDescriptor descriptor, int status) {
            if (current != gatt || closed) return;
            if (status != BluetoothGatt.GATT_SUCCESS) { retry(context.getString(R.string.bluetooth_subscription_failed)); return; }
            if (descriptor.getCharacteristic().getUuid().equals(AUDIO)) listener.status(context.getString(R.string.receiving_omi));
            next();
        }
        @Override public void onCharacteristicChanged(BluetoothGatt current, BluetoothGattCharacteristic characteristic) {
            if (current != gatt || closed) return;
            byte[] value = characteristic.getValue();
            if (value == null) return;
            if (characteristic.getUuid().equals(AUDIO)) listener.audio(value.clone());
            else if (characteristic.getUuid().equals(BUTTON)) listener.button(value.clone());
        }
    };
}

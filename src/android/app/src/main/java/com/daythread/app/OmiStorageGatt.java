package com.daythread.app;

import android.annotation.SuppressLint;
import android.bluetooth.*;
import android.content.Context;
import android.os.Build;
import android.os.Handler;
import com.daythread.app.core.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import java.util.function.BooleanSupplier;

/** Optional custody-v1 copy session. All GATT work shares OmiConnection's serialized queue. */
@SuppressLint("MissingPermission")
final class OmiStorageGatt {
    interface Host {
        void enqueue(UUID characteristic, Runnable start);
        void next();
        void abort();
        boolean audioPaused();
        void status(String value);
    }
    private static final UUID DEVICE_INFO = UUID.fromString("0000180a-0000-1000-8000-00805f9b34fb");
    private static final UUID FIRMWARE = UUID.fromString("00002a26-0000-1000-8000-00805f9b34fb");
    private static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private enum Phase { IDLE, FIRMWARE, CLOCK, SUBSCRIBE, INFO, CUSTODY, CUSTODY_INFO, TRANSFER, VERIFY }
    private final Context context;
    private final Handler handler;
    private final Host host;
    private BluetoothGatt gatt;
    private BluetoothGattCharacteristic control, status, firmware;
    private Phase phase = Phase.IDLE;
    private Runnable deadline;
    private BooleanSupplier allowed;
    private Outbox outbox;
    private Destination destination;
    private String device;
    private int codec, copied;
    private boolean copy;
    private OmiRingProtocol.Info original;
    private OmiRingProtocol.Transfer transfer;
    private List<OmiRingProtocol.Record> received;
    private long cursor, frontier;

    OmiStorageGatt(Context context, Handler handler, Host host) { this.context = context; this.handler = handler; this.host = host; }
    boolean busy() { return phase != Phase.IDLE; }
    void discover(BluetoothGatt current) {
        disable(); gatt = current;
        BluetoothGattService service = current.getService(OmiRingProtocol.SERVICE), info = current.getService(DEVICE_INFO);
        control = service == null ? null : service.getCharacteristic(OmiRingProtocol.CONTROL);
        status = service == null ? null : service.getCharacteristic(OmiRingProtocol.STATUS);
        firmware = info == null ? null : info.getCharacteristic(FIRMWARE);
        if (control == null || (control.getProperties() & (BluetoothGattCharacteristic.PROPERTY_WRITE | BluetoothGattCharacteristic.PROPERTY_NOTIFY))
                != (BluetoothGattCharacteristic.PROPERTY_WRITE | BluetoothGattCharacteristic.PROPERTY_NOTIFY)
                || control.getDescriptor(CCCD) == null || status == null || firmware == null
                || (status.getProperties() & BluetoothGattCharacteristic.PROPERTY_READ) == 0
                || (firmware.getProperties() & BluetoothGattCharacteristic.PROPERTY_READ) == 0) disable();
    }
    void disable() {
        if (deadline != null) handler.removeCallbacks(deadline);
        phase = Phase.IDLE; gatt = null; control = status = firmware = null; transfer = null; received = null;
    }
    void fail(int resource) {
        host.status(context.getString(resource)); disable(); host.abort();
    }
    void begin(boolean copy, Outbox outbox, Destination destination, String device, int codec, BooleanSupplier allowed) {
        if (busy()) return;
        if (!host.audioPaused() || !allowed.getAsBoolean()) { host.status(context.getString(R.string.ring_pause)); return; }
        if (gatt == null || control == null || (codec != 20 && codec != 21)) { host.status(context.getString(R.string.ring_unsupported)); return; }
        this.copy = copy; this.outbox = outbox; this.destination = destination; this.device = device; this.codec = codec; this.allowed = allowed;
        copied = 0; phase = Phase.FIRMWARE;
        host.status(context.getString(copy ? R.string.ring_copying : R.string.ring_checking));
        read(firmware); timeout();
    }
    private void timeout() {
        if (deadline != null) handler.removeCallbacks(deadline);
        deadline = () -> fail(R.string.ring_failed); handler.postDelayed(deadline, 15000);
    }
    private boolean safe() {
        if (allowed == null || !allowed.getAsBoolean() || !host.audioPaused()) { fail(R.string.ring_pause); return false; }
        return true;
    }
    private void read(BluetoothGattCharacteristic characteristic) {
        BluetoothGatt current = gatt;
        host.enqueue(characteristic.getUuid(), () -> {
            if (current != gatt || !safe()) return;
            try { if (!current.readCharacteristic(characteristic)) fail(R.string.ring_failed); }
            catch (RuntimeException e) { fail(R.string.ring_failed); }
        });
    }
    private void write(byte[] value) {
        BluetoothGatt current = gatt; BluetoothGattCharacteristic characteristic = control;
        host.enqueue(OmiRingProtocol.CONTROL, () -> {
            if (current != gatt || !safe()) return;
            try {
                boolean accepted;
                if (Build.VERSION.SDK_INT >= 33) accepted = current.writeCharacteristic(characteristic, value, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothStatusCodes.SUCCESS;
                else { characteristic.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT); characteristic.setValue(value); accepted = current.writeCharacteristic(characteristic); }
                if (!accepted) fail(R.string.ring_failed);
            } catch (RuntimeException e) { fail(R.string.ring_failed); }
        });
    }
    boolean readResult(BluetoothGattCharacteristic characteristic, int code) {
        if (gatt == null || !busy() || !safe()) return false;
        try {
            byte[] value = characteristic.getValue();
            if (code != BluetoothGatt.GATT_SUCCESS || value == null) { fail(R.string.ring_failed); return true; }
            if (characteristic == firmware && phase == Phase.FIRMWARE) {
                if (!OmiRingProtocol.supportsFirmware(new String(value, StandardCharsets.UTF_8).trim())) { fail(R.string.ring_unsupported); return true; }
                phase = Phase.CLOCK; read(status);
            } else if (characteristic == status && phase == Phase.CLOCK) {
                if (!OmiRingProtocol.rtcValid(value)) { fail(R.string.ring_clock); return true; }
                phase = Phase.SUBSCRIBE;
                BluetoothGatt current = gatt;
                host.enqueue(OmiRingProtocol.CONTROL, () -> {
                    try {
                        BluetoothGattDescriptor descriptor = control.getDescriptor(CCCD);
                        if (!safe() || current != gatt) return;
                        if (!current.setCharacteristicNotification(control, true)) { fail(R.string.ring_failed); return; }
                        descriptor.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
                        if (!current.writeDescriptor(descriptor)) fail(R.string.ring_failed);
                    } catch (RuntimeException e) { fail(R.string.ring_failed); }
                });
            } else { fail(R.string.ring_changed); return true; }
            host.next(); timeout();
        } catch (RuntimeException e) { fail(R.string.ring_failed); }
        return true;
    }
    void subscribed(int code) {
        if (gatt == null || phase != Phase.SUBSCRIBE) return;
        if (code != BluetoothGatt.GATT_SUCCESS || !safe()) { fail(R.string.ring_failed); return; }
        phase = Phase.INFO; write(OmiRingProtocol.INFO); host.next(); timeout();
    }
    void written(int code) {
        if (gatt == null) return;
        if (code != BluetoothGatt.GATT_SUCCESS) fail(R.string.ring_failed); else host.next();
    }
    void receive(byte[] value) {
        if (!busy() || value.length == 0 || !safe()) return;
        try {
            if (phase == Phase.CUSTODY && value[0] == 1) {
                if (!OmiRingProtocol.custodyAccepted(value)) { fail(R.string.ring_unsupported); return; }
                phase = Phase.CUSTODY_INFO; write(OmiRingProtocol.INFO); timeout(); return;
            }
            if (value[0] == 2 && Set.of(Phase.INFO, Phase.CUSTODY_INFO, Phase.VERIFY).contains(phase)) {
                OmiRingProtocol.Info info = OmiRingProtocol.Info.parse(value);
                if (phase == Phase.INFO) {
                    original = info; frontier = info.write(); cursor = Math.max(info.read(), outbox.ringCursor(destination, device, info.ring()));
                    if (cursor > frontier) throw new OmiRingProtocol.Invalid(OmiRingProtocol.Failure.CHANGED);
                    if (!copy) { finish(context.getString(R.string.ring_pending, frontier - cursor) + (info.dropped() != 0 ? "\n" + context.getString(R.string.ring_overwritten) : "")); return; }
                    phase = Phase.CUSTODY; write(OmiRingProtocol.CUSTODY); timeout(); return;
                }
                if (info.ring() != original.ring() || info.read() > cursor || info.write() < frontier)
                    throw new OmiRingProtocol.Invalid(OmiRingProtocol.Failure.CHANGED);
                if (phase == Phase.VERIFY) {
                    if (!safe()) return;
                    copied += outbox.copyRingRecords(destination, device, original.ring(), received, codec);
                    cursor = received.get(received.size() - 1).sequence() + 1; received = null;
                }
                if (cursor >= frontier) { finish(context.getString(copied == 0 ? R.string.ring_empty : R.string.ring_saved)); return; }
                int count = (int) Math.min(OmiRingProtocol.MAX_BATCH, frontier - cursor);
                transfer = new OmiRingProtocol.Transfer(cursor, count); phase = Phase.TRANSFER;
                write(OmiRingProtocol.read(cursor, count)); timeout(); return;
            }
            if (phase == Phase.TRANSFER) {
                transfer.receive(value); timeout();
                if (transfer.complete()) { received = transfer.records(Instant.now()); phase = Phase.VERIFY; write(OmiRingProtocol.INFO); }
            } else if (value[0] == 6) fail(R.string.ring_changed);
        } catch (OmiRingProtocol.Invalid e) {
            fail(e.failure == OmiRingProtocol.Failure.CLOCK ? R.string.ring_clock : e.failure == OmiRingProtocol.Failure.OVERLAP ? R.string.ring_overlap : R.string.ring_changed);
        } catch (Exception e) { fail(R.string.ring_failed); }
    }
    private void finish(String message) {
        if (deadline != null) handler.removeCallbacks(deadline);
        phase = Phase.IDLE; transfer = null; received = null; host.status(message);
    }
}

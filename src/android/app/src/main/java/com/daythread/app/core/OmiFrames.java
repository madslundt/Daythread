package com.daythread.app.core;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;
import io.github.jaredmdobson.concentus.OpusDecoder;
import io.github.jaredmdobson.concentus.OpusException;

public final class OmiFrames {
    private Integer previous, fragment, frameSequence;
    private final ByteArrayOutputStream payload = new ByteArrayOutputStream();
    public record Result(byte[] frame, boolean lost, boolean started, Integer completedSequence) {
        public Result(byte[] frame, boolean lost, boolean started) { this(frame, lost, started, null); }
    }
    public Integer pendingSequence() { return payload.size() == 0 ? null : frameSequence; }

    public Result consume(byte[] packet) {
        if (packet.length <= 3 || packet.length > 8195) { reset(); return new Result(null, true, false); }
        int sequence = (packet[0] & 255) | (packet[1] & 255) << 8, index = packet[2] & 255;
        if (previous != null && sequence == previous) return new Result(null, false, false);
        boolean lost = previous != null && sequence != ((previous + 1) & 65535);
        previous = sequence;
        if (lost) { payload.reset(); fragment = null; frameSequence = null; }
        if (index == 0) {
            byte[] completed = payload.size() == 0 ? null : payload.toByteArray();
            Integer completedSequence = frameSequence;
            payload.reset(); payload.write(packet, 3, packet.length - 3); fragment = 0; frameSequence = sequence;
            return new Result(completed, lost, true, completedSequence);
        }
        if (fragment == null || fragment == 255 || index != fragment + 1 || payload.size() + packet.length - 3 > 8192) {
            payload.reset(); fragment = null; frameSequence = null; return new Result(null, true, false);
        }
        payload.write(packet, 3, packet.length - 3); fragment = index;
        return new Result(null, lost, false);
    }
    public byte[] flush() { byte[] frame = payload.size() == 0 ? null : payload.toByteArray(); reset(); return frame; }
    public void reset() { previous = null; fragment = null; frameSequence = null; payload.reset(); }

    public static final class Decoder {
        private final int codec;
        private final OpusDecoder opus;
        public Decoder(int codec) throws OpusException {
            if (codec != 0 && codec != 20 && codec != 21) throw new IllegalArgumentException("Unsupported Omi codec: " + codec);
            this.codec = codec;
            opus = codec == 0 ? null : new OpusDecoder(16000, 1);
        }
        public byte[] decode(byte[] frame) throws OpusException {
            if (frame.length == 0) throw new IllegalArgumentException("Empty audio frame");
            if (codec == 0) {
                if (frame.length % 2 != 0) throw new IllegalArgumentException("Incomplete PCM16 sample");
                return Arrays.copyOf(frame, frame.length);
            }
            short[] samples = new short[1920];
            int count = opus.decode(frame, 0, frame.length, samples, 0, samples.length, false);
            if (count != (codec == 20 ? 160 : 320)) throw new IllegalArgumentException("Unexpected Opus frame duration");
            ByteBuffer bytes = ByteBuffer.allocate(count * 2).order(ByteOrder.LITTLE_ENDIAN);
            for (int i = 0; i < count; i++) bytes.putShort(samples[i]);
            return bytes.array();
        }
    }
}

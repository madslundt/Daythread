import Foundation
import Copus

public enum CaptureError: LocalizedError {
    case unsupportedCodec(Int), invalidAudio(String), invalidReceipt, storageFull, corruptedStore
    public var errorDescription: String? {
        switch self {
        case .unsupportedCodec(let id): AppLocalization.text("Unsupported Omi codec \(id). Capture stopped; select supported firmware.")
        case .invalidAudio(let detail): AppLocalization.text("Audio decoding failed: \(detail)")
        case .invalidReceipt: AppLocalization.text("Home-core acknowledgement did not match the queued recording or event. Original data was retained.")
        case .storageFull: AppLocalization.text("Phone capture storage is full. Unsent recordings were preserved. Sync to home-core before resuming.")
        case .corruptedStore: AppLocalization.text("The capture index cannot be read. Recordings were preserved. Restore the index before continuing.")
        }
    }
}

public final class FrameDecoder {
    public let codec: Int
    private var opus: OpaquePointer?
    public init(codec: Int) throws {
        // Codec 1 has conflicting upstream bit-depth/sample-rate contracts; require hardware verification.
        guard [0, 20, 21].contains(codec) else { throw CaptureError.unsupportedCodec(codec) }
        self.codec = codec
        if codec >= 20 {
            var error: Int32 = 0
            opus = opus_decoder_create(16000, 1, &error)
            guard error == 0, opus != nil else { throw CaptureError.invalidAudio(AppLocalization.text("Opus initialization \(error)")) }
        }
    }
    deinit { if let opus { opus_decoder_destroy(opus) } }
    public func decode(_ frame: Data) throws -> Data {
        guard !frame.isEmpty else { throw CaptureError.invalidAudio(AppLocalization.text("Empty frame")) }
        if codec == 0 {
            guard frame.count.isMultiple(of: 2) else { throw CaptureError.invalidAudio(AppLocalization.text("Incomplete PCM16 sample")) }
            return frame
        }
        guard let opus else { throw CaptureError.invalidAudio(AppLocalization.text("Opus decoder unavailable")) }
        var samples = [Int16](repeating: 0, count: 1920)
        let count = frame.withUnsafeBytes { input in
            opus_decode(opus, input.bindMemory(to: UInt8.self).baseAddress, Int32(frame.count), &samples, 1920, 0)
        }
        guard count > 0 else { throw CaptureError.invalidAudio(AppLocalization.text("Opus error \(count)")) }
        // A different duration indicates a firmware/protocol mismatch; never silently mis-time it.
        let expected = codec == 20 ? 160 : 320
        guard count == expected else { throw CaptureError.invalidAudio(AppLocalization.text("Codec \(codec) returned \(count) samples; expected \(expected)")) }
        return samples.prefix(Int(count)).reduce(into: Data()) { data, value in
            var little = value.littleEndian
            withUnsafeBytes(of: &little) { data.append(contentsOf: $0) }
        }
    }
}

public enum WAV {
    public static func encode(_ pcm: Data) -> Data {
        var result = Data()
        func text(_ value: String) { result.append(contentsOf: value.utf8) }
        func number<T: FixedWidthInteger>(_ value: T) {
            var little = value.littleEndian; withUnsafeBytes(of: &little) { result.append(contentsOf: $0) }
        }
        text("RIFF"); number(UInt32(36 + pcm.count)); text("WAVEfmt "); number(UInt32(16))
        number(UInt16(1)); number(UInt16(1)); number(UInt32(16000)); number(UInt32(32000))
        number(UInt16(2)); number(UInt16(16)); text("data"); number(UInt32(pcm.count)); result.append(pcm)
        return result
    }
}

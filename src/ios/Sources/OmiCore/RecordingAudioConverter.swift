import AVFoundation
import CryptoKit
import Foundation

public struct ConvertedRecording: Sendable {
    public struct Part: Sendable {
        public let url: URL
        public let digest: String
        public let samples: Int
        public var bytes: Int { samples * 2 + 44 }
    }
    public let parts: [Part]
    public var sampleCount: Int { parts.reduce(0) { $0 + $1.samples } }
    public var bytes: Int { parts.reduce(0) { $0 + $1.bytes } }
}

/// Streams decoding and hashing off the main actor. At most one two-minute PCM
/// part and the reader's current sample buffer are retained in memory.
public enum RecordingAudioConverter {
    public static func fileDigest(_ url: URL) throws -> String {
        let input = try FileHandle(forReadingFrom: url)
        defer { try? input.close() }
        var hash = SHA256()
        while let data = try input.read(upToCount: 1_048_576), !data.isEmpty {
            try Task.checkCancellation()
            hash.update(data: data)
        }
        return hex(hash.finalize())
    }

    public static func convert(_ url: URL, into directory: URL) async throws -> ConvertedRecording {
        try Task.checkCancellation()
        let file: AVAudioFile
        do { file = try AVAudioFile(forReading: url, commonFormat: .pcmFormatFloat32, interleaved: false) }
        catch { throw RecordingImportError(AppLocalization.source("This recording cannot be decoded on this iPhone. Try a WAV, M4A, or MP3 file.")) }
        let duration = Double(file.length) / file.processingFormat.sampleRate
        guard duration.isFinite, duration > 0 else { throw RecordingImportError(AppLocalization.source("The recording contains no audio.")) }
        guard duration <= 7200 else { throw RecordingImportError(AppLocalization.source("Split recordings longer than two hours before uploading.")) }
        let expectedSamples = Int(ceil(duration * Double(RecordingImport.sampleRate)))
        try RecordingImport.validateSampleCount(expectedSamples)
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(RecordingImport.sampleRate), channels: 1, interleaved: false),
              let converter = AVAudioConverter(from: file.processingFormat, to: format),
              let input = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 4096),
              let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4096) else {
            throw RecordingImportError(AppLocalization.source("This audio format cannot be converted on this iPhone."))
        }
        // A single converter with fixed input/output buffers avoids timeline mix
        // rendering, whose internal boundary choices can vary between readings.
        // Float output plus explicit quantization keeps retry fingerprints stable.
        converter.downmix = true
        converter.dither = false
        converter.primeMethod = .normal
        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var pending = Data()
        pending.reserveCapacity(RecordingImport.samplesPerPart * 2)
        var parts: [ConvertedRecording.Part] = []
        var totalSamples = 0
        var decodedSamples = 0
        var finished = false
        do {
            while !finished {
                try Task.checkCancellation()
                var conversionError: NSError?
                var readError: Error?
                output.frameLength = 0
                let status = converter.convert(to: output, error: &conversionError) { requested, inputStatus in
                    do {
                        try Task.checkCancellation()
                        guard file.framePosition < file.length, requested > 0 else {
                            inputStatus.pointee = .endOfStream
                            return nil
                        }
                        let frames = min(requested, input.frameCapacity, AVAudioFrameCount(min(Int64(input.frameCapacity), file.length - file.framePosition)))
                        try file.read(into: input, frameCount: frames)
                        inputStatus.pointee = input.frameLength == 0 ? .endOfStream : .haveData
                        return input.frameLength == 0 ? nil : input
                    } catch {
                        readError = error
                        inputStatus.pointee = .endOfStream
                        return nil
                    }
                }
                if let readError { throw readError }
                if let conversionError { throw conversionError }
                guard status != .error else { throw RecordingImportError(AppLocalization.source("The audio conversion could not be completed.")) }
                finished = status == .endOfStream
                guard output.frameLength > 0 || finished else { throw RecordingImportError(AppLocalization.source("The audio decoder stopped before the recording ended.")) }
                decodedSamples += Int(output.frameLength)
                guard decodedSamples <= expectedSamples + 1024 else {
                    throw RecordingImportError(AppLocalization.source("The decoded audio does not match the recording duration."))
                }
                let count = min(Int(output.frameLength), expectedSamples - totalSamples)
                totalSamples += count
                var offset = 0
                while offset < count {
                    let length = min(count - offset, RecordingImport.samplesPerPart - pending.count / 2, 4096)
                    guard let samples = output.floatChannelData?[0] else { throw RecordingImportError(AppLocalization.source("The audio samples could not be read.")) }
                    var pcm = [Int16](repeating: 0, count: length)
                    for index in pcm.indices {
                        guard samples[offset + index].isFinite else { throw RecordingImportError(AppLocalization.source("The decoder returned an invalid audio sample.")) }
                        let value = Double(max(-1, min(1, samples[offset + index])))
                        pcm[index] = Int16(floor(value * (value < 0 ? 32768 : 32767) + 0.5)).littleEndian
                    }
                    pcm.withUnsafeBytes { pending.append(contentsOf: $0) }
                    offset += length
                    if pending.count == RecordingImport.samplesPerPart * 2 {
                        parts.append(try save(pending, index: parts.count, directory: directory))
                        pending.removeAll(keepingCapacity: true)
                    }
                }
            }
            try Task.checkCancellation()
            // AVFoundation's sample-rate converter can omit a few trailing frames
            // while draining. Like the web's fixed-length OfflineAudioContext,
            // retain the audio track's duration, with at most one codec block of
            // trailing silence. Larger discrepancies indicate a damaged file.
            guard decodedSamples > 0, abs(decodedSamples - expectedSamples) <= 1024 else {
                throw RecordingImportError(AppLocalization.source("The decoded audio does not match the recording duration."))
            }
            if totalSamples < expectedSamples {
                var remaining = (expectedSamples - totalSamples) * 2
                while remaining > 0 {
                    let length = min(remaining, RecordingImport.samplesPerPart * 2 - pending.count)
                    pending.append(Data(count: length))
                    remaining -= length
                    if pending.count == RecordingImport.samplesPerPart * 2 {
                        parts.append(try save(pending, index: parts.count, directory: directory))
                        pending.removeAll(keepingCapacity: true)
                    }
                }
            }
            if !pending.isEmpty { parts.append(try save(pending, index: parts.count, directory: directory)) }
            return ConvertedRecording(parts: parts)
        } catch {
            for part in parts { try? FileManager.default.removeItem(at: part.url) }
            throw error
        }
    }

    private static func save(_ pcm: Data, index: Int, directory: URL) throws -> ConvertedRecording.Part {
        let data = WAV.encode(pcm)
        let url = directory.appendingPathComponent("part-\(index).wav")
        var options: Data.WritingOptions = .atomic
        #if os(iOS)
        options.insert(.completeFileProtectionUntilFirstUserAuthentication)
        #endif
        try data.write(to: url, options: options)
        return ConvertedRecording.Part(url: url, digest: hex(SHA256.hash(data: data)), samples: pcm.count / 2)
    }

    private static func hex(_ digest: SHA256.Digest) -> String {
        digest.map { String(format: "%02x", $0) }.joined()
    }
}

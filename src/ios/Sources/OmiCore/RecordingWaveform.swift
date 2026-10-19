import Accelerate
import AVFoundation
import Foundation

/// A peak envelope of the recording's decoded samples, shared by all channels.
public struct RecordingWaveform: Sendable {
    public enum ReadError: Error {
        case invalidBinCount
        case unsupportedFormat
    }

    public let duration: TimeInterval
    public let peaks: [Float]

    /// Reads a fixed-size buffer at a time. Call off the main actor for long recordings.
    public static func read(url: URL, binCount: Int = 160) throws -> RecordingWaveform {
        guard (1...2048).contains(binCount) else { throw ReadError.invalidBinCount }
        try Task.checkCancellation()
        let file = try AVAudioFile(forReading: url, commonFormat: .pcmFormatFloat32, interleaved: false)
        let totalFrames = file.length
        let format = file.processingFormat
        guard totalFrames >= 0, format.sampleRate.isFinite, format.sampleRate > 0,
              format.channelCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 8192),
              let channels = buffer.floatChannelData else { throw ReadError.unsupportedFormat }
        var peaks = [Float](repeating: 0, count: binCount)
        guard totalFrames > 0 else { return RecordingWaveform(duration: 0, peaks: peaks) }

        // Each sample belongs to floor(frame * binCount / totalFrames). Integer
        // boundaries retain the first and final sample even for very short files.
        func boundary(after bin: Int) -> AVAudioFramePosition {
            let divisor = AVAudioFramePosition(binCount)
            let multiplier = AVAudioFramePosition(bin + 1)
            return (totalFrames / divisor) * multiplier
                + ((totalFrames % divisor) * multiplier + divisor - 1) / divisor
        }
        var bin = 0
        while file.framePosition < totalFrames {
            try Task.checkCancellation()
            let start = file.framePosition
            let count = AVAudioFrameCount(min(AVAudioFramePosition(buffer.frameCapacity), totalFrames - start))
            try file.read(into: buffer, frameCount: count)
            guard buffer.frameLength > 0 else { throw ReadError.unsupportedFormat }
            let frameCount = Int(buffer.frameLength)
            // Malformed floating-point audio must not contaminate the envelope.
            for channel in 0..<Int(format.channelCount) {
                for frame in 0..<frameCount where !channels[channel][frame].isFinite {
                    channels[channel][frame] = 0
                }
            }
            var offset = 0
            while offset < frameCount {
                let frame = start + AVAudioFramePosition(offset)
                while bin < binCount - 1, boundary(after: bin) <= frame { bin += 1 }
                let length = min(frameCount - offset, Int(boundary(after: bin) - frame))
                for channel in 0..<Int(format.channelCount) {
                    var peak: Float = 0
                    vDSP_maxmgv(channels[channel].advanced(by: offset), 1, &peak, vDSP_Length(length))
                    peaks[bin] = max(peaks[bin], peak)
                }
                offset += length
            }
        }
        try Task.checkCancellation()
        if let maximum = peaks.max(), maximum > 0 {
            peaks = peaks.map { min(1, max(0, $0 / maximum)) }
        }
        return RecordingWaveform(duration: Double(totalFrames) / format.sampleRate, peaks: peaks)
    }
}

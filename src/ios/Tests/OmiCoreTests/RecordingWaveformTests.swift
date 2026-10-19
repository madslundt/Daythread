import AVFoundation
import XCTest
@testable import OmiCore

final class RecordingWaveformTests: XCTestCase {
    func testPeaksRetainImpulsePositionsAcrossReadChunksAndFinalBucket() throws {
        var samples = [Float](repeating: 0, count: 32_003)
        samples[0] = -0.25
        samples[8001] = 0.5
        samples[16_002] = -1
        samples[32_002] = 0.75
        let url = try recording(channels: [samples])
        defer { try? FileManager.default.removeItem(at: url) }

        let waveform = try RecordingWaveform.read(url: url, binCount: 4)

        XCTAssertEqual(waveform.duration, Double(samples.count) / 16_000, accuracy: 0.000001)
        XCTAssertEqual(waveform.peaks, [0.25, 0.5, 1, 0.75])
    }

    func testStereoUsesBothChannelsWithoutCancellationFromOppositePolarity() throws {
        let url = try recording(channels: [
            [0.1, -0.2, 0.1, -0.4],
            [-0.1, 0.2, -0.8, 0.4]
        ])
        defer { try? FileManager.default.removeItem(at: url) }

        XCTAssertEqual(try RecordingWaveform.read(url: url, binCount: 4).peaks, [0.125, 0.25, 1, 0.5])
    }

    func testSilenceRemainsZeroAndDefaultResolutionIsStable() throws {
        let url = try recording(channels: [[Float](repeating: 0, count: 20_000)])
        defer { try? FileManager.default.removeItem(at: url) }

        let waveform = try RecordingWaveform.read(url: url)

        XCTAssertEqual(waveform.peaks, [Float](repeating: 0, count: 160))
        XCTAssertEqual(waveform.duration, 1.25)
    }

    func testShortRecordingPreservesFirstAndLastSamplesWithEmptyBins() throws {
        let url = try recording(channels: [[0.5, -1]])
        defer { try? FileManager.default.removeItem(at: url) }

        XCTAssertEqual(try RecordingWaveform.read(url: url, binCount: 5).peaks, [0.5, 0, 1, 0, 0])
        XCTAssertEqual(try RecordingWaveform.read(url: url, binCount: 1).peaks, [1])
        XCTAssertEqual(try RecordingWaveform.read(url: url, binCount: 2048).peaks.count, 2048)
    }

    func testEmptyRecordingHasZeroDurationAndSilentBins() throws {
        let url = try recording(channels: [[]])
        defer { try? FileManager.default.removeItem(at: url) }

        let waveform = try RecordingWaveform.read(url: url, binCount: 3)

        XCTAssertEqual(waveform.duration, 0)
        XCTAssertEqual(waveform.peaks, [0, 0, 0])
    }

    func testInvalidBinCountsAreRejectedBeforeOpeningFile() {
        for count in [-1, 0, 2049] {
            XCTAssertThrowsError(try RecordingWaveform.read(url: URL(fileURLWithPath: "/missing.wav"), binCount: count)) { error in
                guard case RecordingWaveform.ReadError.invalidBinCount = error else {
                    return XCTFail("Unexpected error: \(error)")
                }
            }
        }
    }

    func testNonfiniteSamplesCannotContaminatePeaks() throws {
        let url = try recording(channels: [[.nan, .infinity, -.infinity, -0.25, 0.5]])
        defer { try? FileManager.default.removeItem(at: url) }

        XCTAssertEqual(try RecordingWaveform.read(url: url, binCount: 5).peaks, [0, 0, 0, 0.5, 1])
    }

    func testCancelledReadThrowsCancellationBeforeOpeningFile() async {
        let task = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            return try RecordingWaveform.read(url: URL(fileURLWithPath: "/missing.wav"))
        }
        do {
            _ = try await task.value
            XCTFail("Expected cancellation")
        } catch {
            XCTAssertTrue(error is CancellationError)
        }
    }

    private func recording(channels: [[Float]]) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathExtension("wav")
        let format = try XCTUnwrap(AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: AVAudioChannelCount(channels.count), interleaved: false))
        let file = try AVAudioFile(forWriting: url, settings: format.settings, commonFormat: .pcmFormatFloat32, interleaved: false)
        let count = channels[0].count
        if count > 0 {
            let buffer = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)))
            buffer.frameLength = AVAudioFrameCount(count)
            let data = try XCTUnwrap(buffer.floatChannelData)
            for channel in channels.indices {
                for frame in 0..<count { data[channel][frame] = channels[channel][frame] }
            }
            try file.write(from: buffer)
        }
        return url
    }
}

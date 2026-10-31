import XCTest
import Copus
@testable import OmiCore

final class CaptureTests: XCTestCase {
    func testPendantVibrationUsesFixedDistinctPulsesForEachClick() {
        XCTAssertEqual(WearableClick.single.hapticDuration, .short)
        XCTAssertEqual(WearableClick.double.hapticDuration, .long)
        XCTAssertEqual(WearableClick.single.hapticDuration.milliseconds, 100)
        XCTAssertEqual(WearableClick.double.hapticDuration.milliseconds, 500)
    }

    func testPendantHapticCommandsNeverSendOffOrMediumCommands() {
        XCTAssertNil(OmiHapticDuration(rawValue: 0))
        XCTAssertNil(OmiHapticDuration(rawValue: 2))
        XCTAssertEqual(OmiHapticDuration.short.command, Data([1]))
        XCTAssertEqual(OmiHapticDuration.long.command, Data([3]))
        XCTAssertEqual(OmiHapticDuration.allCases.map(\.milliseconds), [100, 500])
    }

    func testTransferEstimateRequiresMeasuredProgressAndExpiresWhenStalled() {
        var rate = TransferRate()
        rate.observe(sentBytes: 0, at: 100)
        XCTAssertNil(rate.remainingSeconds(for: 1000, at: 100))
        rate.observe(sentBytes: 500, at: 102)
        XCTAssertEqual(rate.remainingSeconds(for: 1000, at: 102), 4)
        XCTAssertNil(rate.remainingSeconds(for: 1000, at: 112))
        XCTAssertNil(rate.remainingSeconds(for: 0, at: 102))
    }

    func testTransferEstimateResetsAfterRetryOrRestoringAnExistingTask() {
        var rate = TransferRate()
        rate.observe(sentBytes: 500, at: 100)
        XCTAssertNil(rate.remainingSeconds(for: 1000, at: 102))
        rate.observe(sentBytes: 1000, at: 102)
        XCTAssertEqual(rate.remainingSeconds(for: 1000, at: 102), 4)
        rate.observe(sentBytes: 100, at: 103)
        XCTAssertNil(rate.remainingSeconds(for: 1000, at: 104))
        rate.observe(sentBytes: 300, at: 105)
        XCTAssertEqual(rate.remainingSeconds(for: 1000, at: 105), 10)
    }

    func testDiscoveryAcceptsServiceAndNamedOmiWithoutAdvertisingService() {
        XCTAssertTrue(OmiDeviceDiscovery.matches(localName: nil, peripheralName: nil, serviceUUIDs: [OmiDeviceDiscovery.audioService.uppercased()]))
        XCTAssertTrue(OmiDeviceDiscovery.matches(localName: " OMI ", peripheralName: nil, serviceUUIDs: []))
        XCTAssertTrue(OmiDeviceDiscovery.matches(localName: "Omi DevKit", peripheralName: nil, serviceUUIDs: []))
        XCTAssertTrue(OmiDeviceDiscovery.matches(localName: nil, peripheralName: "omi-123", serviceUUIDs: []))
        XCTAssertFalse(OmiDeviceDiscovery.matches(localName: "Omicron", peripheralName: nil, serviceUUIDs: []))
        XCTAssertFalse(OmiDeviceDiscovery.matches(localName: nil, peripheralName: nil, serviceUUIDs: ["180F"]))
    }

    private func packet(_ sequence: UInt16, _ fragment: UInt8, _ bytes: [UInt8]) -> Data {
        Data([UInt8(truncatingIfNeeded: sequence), UInt8(truncatingIfNeeded: sequence >> 8), fragment] + bytes)
    }
    func testFragmentsRolloverAndDuplicateNotifications() {
        var assembler = OmiReassembler()
        XCTAssertNil(assembler.consume(packet(65534, 0, [1, 2])).frame)
        XCTAssertNil(assembler.consume(packet(65535, 1, [3])).frame)
        XCTAssertNil(assembler.consume(packet(65535, 1, [3])).frame)
        let result = assembler.consume(packet(0, 0, [4]))
        XCTAssertEqual(result.frame, Data([1, 2, 3])); XCTAssertFalse(result.lost)
        XCTAssertEqual(assembler.flush(), Data([4])); XCTAssertNil(assembler.flush())
    }
    func testLossDiscardsPartialFrameAndWaitsForFrameStart() {
        var assembler = OmiReassembler()
        _ = assembler.consume(packet(1, 0, [1]))
        XCTAssertTrue(assembler.consume(packet(3, 1, [2])).lost)
        XCTAssertNil(assembler.consume(packet(4, 0, [3])).frame)
        XCTAssertEqual(assembler.consume(packet(5, 0, [4])).frame, Data([3]))
    }
    func testOutOfOrderFragmentCannotBecomeValidAudio() {
        var assembler = OmiReassembler()
        _ = assembler.consume(packet(1, 0, [1]))
        XCTAssertTrue(assembler.consume(packet(2, 2, [2])).lost)
        XCTAssertNil(assembler.flush())
        XCTAssertTrue(assembler.consume(Data([1])).lost)
    }
    func testDuplicateFrameStartDoesNotResetCaptureTimestamp() {
        var assembler = OmiReassembler()
        XCTAssertTrue(assembler.consume(packet(10, 0, [1, 0])).startedFrame)
        XCTAssertFalse(assembler.consume(packet(10, 0, [1, 0])).startedFrame)
        XCTAssertTrue(assembler.consume(packet(11, 0, [2, 0])).startedFrame)
        XCTAssertFalse(assembler.consume(packet(12, 1, [3, 0])).startedFrame)
    }

    func testInterruptedFinalFrameIsDiscardedAndReconnectionCanRecord() throws {
        var assembler = OmiReassembler()
        let decoder = try FrameDecoder(codec: 0)
        _ = assembler.consume(packet(1, 0, [1]))
        // Disconnect before the second PCM byte arrives. This is not a fatal codec mismatch.
        XCTAssertNil(assembler.flush(using: decoder))
        _ = assembler.consume(packet(100, 0, [2, 0]))
        let resumed = try XCTUnwrap(assembler.flush(using: decoder))
        XCTAssertEqual(resumed.frame, Data([2, 0]))
        XCTAssertEqual(resumed.pcm, Data([2, 0]))
        XCTAssertNil(assembler.flush(using: decoder))
    }

    func testTruncatedOpusCandidateIsDroppedAndNextStreamCanDecode() throws {
        for (codec, count) in [(20, 160), (21, 320)] {
            var assembler = OmiReassembler()
            let decoder = try FrameDecoder(codec: codec)
            _ = assembler.consume(packet(1, 0, [255])) // Opus frame-count byte never arrived.
            XCTAssertNil(assembler.flush(using: decoder))
            var error: Int32 = 0
            let encoder = try XCTUnwrap(opus_encoder_create(16000, 1, OPUS_APPLICATION_AUDIO, &error))
            defer { opus_encoder_destroy(encoder) }
            var encoded = [UInt8](repeating: 0, count: 1276)
            let samples = [Int16](repeating: 0, count: count)
            let size = opus_encode(encoder, samples, Int32(count), &encoded, 1276)
            XCTAssertGreaterThan(size, 0)
            _ = assembler.consume(packet(100, 0, Array(encoded.prefix(Int(size)))))
            XCTAssertEqual(assembler.flush(using: decoder)?.pcm.count, count * 2)
        }
    }

    func testResolvedClicksPreserveDefaultsAndIgnoreAllHoldEvents() {
        var policy = GesturePolicy()
        let start = Date(timeIntervalSince1970: 1000)
        func data(_ code: UInt8) -> Data { Data([code, 0, 0, 0]) }
        XCTAssertEqual(policy.resolve(data(1), at: start), .mark)
        XCTAssertNil(policy.resolve(data(1), at: start.addingTimeInterval(20)))
        XCTAssertNil(policy.resolve(data(1), at: start.addingTimeInterval(59)))
        XCTAssertEqual(policy.resolve(data(2), at: start.addingTimeInterval(59)), .meetingToggle)
        XCTAssertEqual(policy.resolve(data(1), at: start.addingTimeInterval(60)), .mark)
        XCTAssertNil(policy.resolve(data(3), at: start))
        XCTAssertNil(policy.resolve(data(4), at: start)); XCTAssertNil(policy.resolve(data(5), at: start))
        XCTAssertNil(policy.resolve(Data([2]), at: start))
        for action in WearableAction.allCases {
            policy.singleClick = action; policy.doubleClick = action
            for code: UInt8 in [3, 4, 5] { XCTAssertNil(policy.resolve(data(code), at: start)) }
        }
    }
    func testConfigurableActionsUseOnlyResolvedClicksAndMarkCooldownFollowsAction() {
        let start = Date(timeIntervalSince1970: 1000)
        let single = Data([1, 0, 0, 0]), double = Data([2, 0, 0, 0])
        for action in WearableAction.allCases {
            var policy = GesturePolicy(singleClick: action, doubleClick: action)
            if action == .none {
                XCTAssertNil(policy.resolve(single, at: start)); XCTAssertNil(policy.resolve(double, at: start))
            } else {
                XCTAssertEqual(policy.resolve(single, at: start), action)
                XCTAssertEqual(policy.resolve(double, at: start.addingTimeInterval(60)), action)
            }
        }
        var policy = GesturePolicy(singleClick: .answer, doubleClick: .mark)
        XCTAssertEqual(policy.resolve(double, at: start), .mark)
        XCTAssertEqual(policy.resolve(single, at: start.addingTimeInterval(1)), .answer)
        XCTAssertEqual(policy.resolve(single, at: start.addingTimeInterval(2)), .answer)
        XCTAssertNil(policy.resolve(double, at: start.addingTimeInterval(59)))
        XCTAssertEqual(policy.resolve(double, at: start.addingTimeInterval(60)), .mark)
    }
    func testClickControlDefaultsIgnoreLegacyHoldAndRejectInvalidPreferences() {
        let defaults = ClickControls()
        XCTAssertEqual(defaults.singleClick, .mark); XCTAssertEqual(defaults.doubleClick, .meetingToggle)
        XCTAssertEqual(defaults.durationSeconds, 30)
        let invalid = ClickControls(singleClick: "bad", doubleClick: "verifiedHold", durationSeconds: -1)
        XCTAssertEqual(invalid, defaults)
        let customized = ClickControls(singleClick: "translate", doubleClick: "none", durationSeconds: 120)
        XCTAssertEqual(customized.singleClick, .translate); XCTAssertEqual(customized.doubleClick, .none)
        XCTAssertEqual(customized.durationSeconds, 120)
    }
    func testClockUsesUptimeAnchor() {
        let clock = CaptureClock(wall: Date(timeIntervalSince1970: 1000), uptime: 500)
        XCTAssertEqual(clock.date(at: 510), Date(timeIntervalSince1970: 1010))
        XCTAssertEqual(clock.date(at: 499), Date(timeIntervalSince1970: 1000))
    }
    func testRealOpusEncodeDecodeBothFrameDurations() throws {
        for (codec, count) in [(20, 160), (21, 320)] {
            var error: Int32 = 0
            let encoder = try XCTUnwrap(opus_encoder_create(16000, 1, OPUS_APPLICATION_AUDIO, &error))
            defer { opus_encoder_destroy(encoder) }
            XCTAssertEqual(error, 0)
            let samples = (0..<count).map { Int16(sin(Double($0) * 2 * .pi * 440 / 16000) * 12000) }
            var encoded = [UInt8](repeating: 0, count: 1276)
            let size = opus_encode(encoder, samples, Int32(count), &encoded, 1276)
            XCTAssertGreaterThan(size, 0)
            let decoder = try FrameDecoder(codec: codec)
            let decoded = try decoder.decode(Data(encoded.prefix(Int(size))))
            XCTAssertEqual(decoded.count, count * 2)
        }
        XCTAssertThrowsError(try FrameDecoder(codec: 99))
        XCTAssertThrowsError(try FrameDecoder(codec: 20).decode(Data()))
        XCTAssertThrowsError(try FrameDecoder(codec: 0).decode(Data([0])))
    }
    func testPCM16AndWAVContractRejectsAmbiguousLegacyPCM8() throws {
        let decoder = try FrameDecoder(codec: 0)
        XCTAssertEqual(try decoder.decode(Data([0, 0, 255, 127, 0, 128, 255, 255])), Data([0, 0, 255, 127, 0, 128, 255, 255]))
        XCTAssertThrowsError(try FrameDecoder(codec: 1))
        let wav = WAV.encode(Data(count: 32000))
        XCTAssertEqual(wav.count, 32044)
        XCTAssertEqual(String(data: wav.prefix(4), encoding: .utf8), "RIFF")
        XCTAssertEqual([UInt8](wav[24..<28]), [128, 62, 0, 0])
    }
}

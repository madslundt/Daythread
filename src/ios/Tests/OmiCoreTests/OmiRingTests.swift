import XCTest
import Copus
@testable import OmiCore

final class OmiRingTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_793_534_400)
    private func number(_ value: UInt64, bytes: Int) -> Data {
        Data((0..<bytes).reversed().map { UInt8(truncatingIfNeeded: value >> ($0 * 8)) })
    }
    private func info(read: UInt64 = 10, write: UInt64 = 12, ring: UInt64 = 99, caps: UInt8 = 0x0d) -> Data {
        Data([2]) + number(read, bytes: 8) + number(write, bytes: 8) + number(100, bytes: 4) +
            number(0, bytes: 8) + number(444, bytes: 2) + Data([caps, 1]) + number(ring, bytes: 8)
    }
    private func frame(codec: Int = 20) throws -> Data {
        var error: Int32 = 0
        let encoder = try XCTUnwrap(opus_encoder_create(16000, 1, OPUS_APPLICATION_AUDIO, &error))
        defer { opus_encoder_destroy(encoder) }
        var encoded = [UInt8](repeating: 0, count: 1276)
        let count = codec == 20 ? 160 : 320
        let samples = [Int16](repeating: 200, count: count)
        let size = opus_encode(encoder, samples, Int32(count), &encoded, 1276)
        XCTAssertGreaterThan(size, 0)
        return Data(encoded.prefix(Int(size)))
    }
    private func raw(frame: Data, at: Date? = nil) -> Data {
        var record = number(UInt64((at ?? now).timeIntervalSince1970), bytes: 4)
        record.append(UInt8(frame.count)); record.append(frame)
        record.append(Data(repeating: 0, count: 444 - record.count))
        return record
    }
    private func directory() throws -> URL {
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: path) }
        return path
    }
    func testOnlyVerifiedCustodyProtocolIsSupported() throws {
        XCTAssertEqual(OmiRingProtocol.stableID(device: "pendant", ring: 99, sequence: 10)
            .uuidString.lowercased(), "fb58c342-6eba-52aa-a447-4807587c8606")
        XCTAssertTrue(OmiRingProtocol.firmwareSupportsRing("3.0.20"))
        XCTAssertTrue(OmiRingProtocol.firmwareSupportsRing("3.1.0"))
        for version in ["Unknown", "3.0.19", "2.9.90", "3.0.20-beta", "garbage", "3.20"] {
            XCTAssertFalse(OmiRingProtocol.firmwareSupportsRing(version))
        }
        XCTAssertEqual(try OmiRingInfo(info()).ringID, 99)
        for data in [info(caps: 0), info(caps: 1), info(ring: 0), info(read: 20, write: 10), Data(info().prefix(31))] {
            XCTAssertThrowsError(try OmiRingInfo(data))
        }
        XCTAssertTrue(OmiRingProtocol.custodyAccepted(Data([1, 0, 0x0d])))
        XCTAssertFalse(OmiRingProtocol.custodyAccepted(Data([1, 0])))
        XCTAssertFalse(OmiRingProtocol.custodyAccepted(Data([1, 1, 0x0d])))
        var status = Data(repeating: 0, count: 16); status[12] = 1
        XCTAssertTrue(OmiRingProtocol.rtcValid(status))
        status[12] = 0
        XCTAssertFalse(OmiRingProtocol.rtcValid(status))
        XCTAssertEqual(OmiRingProtocol.readCommand(start: 10, count: 2), Data([0x11]) + number(10, bytes: 8) + number(2, bytes: 4))
    }
    func testTransferReassemblesArbitraryFragmentsAndValidatesFrontiers() throws {
        let first = raw(frame: try frame(), at: now.addingTimeInterval(-1))
        let second = raw(frame: try frame())
        var transfer = try OmiRingTransfer(start: 10, count: 2)
        try transfer.receive(Data([5]) + number(10, bytes: 8) + number(2, bytes: 4))
        let stream = first + second
        for offset in stride(from: 0, to: stream.count, by: 73) {
            try transfer.receive(Data([3]) + stream.subdata(in: offset..<min(offset + 73, stream.count)))
        }
        XCTAssertThrowsError(try transfer.records(now: now))
        try transfer.receive(Data([4, 0]) + number(12, bytes: 8))
        let records = try transfer.records(now: now)
        XCTAssertEqual(records.map(\.sequence), [10, 11])
        XCTAssertEqual(records.map(\.raw), [first, second])
        XCTAssertEqual(records[1].capturedAt, now)
        XCTAssertThrowsError(try transfer.receive(Data([3, 0])))
    }
    func testTruncatedExtraOutOfOrderOrMismatchedDataNeverCompletes() throws {
        for malformed in [Data([3, 1]), Data([4, 0]) + number(11, bytes: 8), Data([5]) + number(11, bytes: 8) + number(1, bytes: 4)] {
            var transfer = try OmiRingTransfer(start: 10, count: 1)
            XCTAssertThrowsError(try transfer.receive(malformed))
        }
        var partial = try OmiRingTransfer(start: 10, count: 1)
        try partial.receive(Data([5]) + number(10, bytes: 8) + number(1, bytes: 4))
        try partial.receive(Data([3, 0, 1]))
        XCTAssertThrowsError(try partial.receive(Data([4, 0]) + number(11, bytes: 8)))
        var extra = try OmiRingTransfer(start: 10, count: 1)
        try extra.receive(Data([5]) + number(10, bytes: 8) + number(1, bytes: 4))
        XCTAssertThrowsError(try extra.receive(Data([3]) + Data(repeating: 0, count: 445)))
        XCTAssertThrowsError(try OmiRingTransfer(start: UInt64.max, count: 1))
    }
    func testUnknownUptimeFutureAndRegressingClockAreRejected() throws {
        let frame = try frame()
        var uptime = raw(frame: frame); uptime[0] |= 0x80
        XCTAssertThrowsError(try OmiRingRecord(sequence: 10, raw: uptime, now: now))
        XCTAssertThrowsError(try OmiRingRecord(sequence: 10, raw: raw(frame: frame, at: now.addingTimeInterval(60)), now: now))
        var transfer = try OmiRingTransfer(start: 10, count: 2)
        try transfer.receive(Data([5]) + number(10, bytes: 8) + number(2, bytes: 4))
        try transfer.receive(Data([3]) + raw(frame: frame) + raw(frame: frame, at: now.addingTimeInterval(-1)))
        try transfer.receive(Data([4, 0]) + number(12, bytes: 8))
        XCTAssertThrowsError(try transfer.records(now: now))
    }
    func testRealOpusCopiesAreDurableIdempotentAndHaveExactSourceIdentity() throws {
        let directory = try directory(), frame = try frame()
        let store = try DurableStore(directory: directory)
        store.destinationID = "home"; store.profileID = "default"
        let record = try OmiRingRecord(sequence: 10, raw: raw(frame: frame), now: now)
        XCTAssertEqual(try store.copyRingRecords(deviceID: "pendant", ringID: 99, records: [record], codec: 20), 1)
        let item = try XCTUnwrap(store.pending.first)
        XCTAssertEqual(item.id, OmiRingProtocol.stableID(device: "pendant", ring: 99, sequence: 10))
        XCTAssertEqual(item.capturedAt, now)
        XCTAssertEqual(item.sourceKind, "manual_upload")
        XCTAssertEqual(item.sourceID, "omi-ring:pendant:99:10")
        XCTAssertEqual(try Data(contentsOf: store.file(item.id, extension: "ring")), record.raw)
        XCTAssertEqual(try Data(contentsOf: store.uploadFile(for: item)), WAV.encode(try FrameDecoder(codec: 20).decode(frame)))
        XCTAssertEqual(try store.copyRingRecords(deviceID: "pendant", ringID: 99, records: [record], codec: 20), 0)
        let reopened = try DurableStore(directory: directory)
        reopened.destinationID = "home"
        XCTAssertEqual(reopened.pending.count, 1)
        XCTAssertEqual(try reopened.ringRecoveryCursor(deviceID: "pendant", ringID: 99), 11)
        reopened.profileID = "other"
        XCTAssertNil(try reopened.ringRecoveryCursor(deviceID: "pendant", ringID: 99))
        XCTAssertThrowsError(try reopened.copyRingRecords(deviceID: "pendant", ringID: 99, records: [record], codec: 20))
    }
    func testFailedDurabilityNeverWritesLocalFrontierAndReplayKeepsSameID() throws {
        let directory = try directory(), frame = try frame()
        var syncs = 0
        let store = try DurableStore(directory: directory, synchronizeSource: { handle in
            syncs += 1
            if syncs == 4 { throw OmiRingError.changed } // Atomic index exists, fsync failed.
            try handle.synchronize()
        })
        store.destinationID = "home"
        let record = try OmiRingRecord(sequence: 10, raw: raw(frame: frame), now: now)
        XCTAssertThrowsError(try store.copyRingRecords(deviceID: "pendant", ringID: 99, records: [record], codec: 20))
        XCTAssertNil(try store.ringRecoveryCursor(deviceID: "pendant", ringID: 99))
        XCTAssertEqual(try store.copyRingRecords(deviceID: "pendant", ringID: 99, records: [record], codec: 20), 0)
        XCTAssertEqual(store.pending.count, 1)
        XCTAssertEqual(try store.ringRecoveryCursor(deviceID: "pendant", ringID: 99), 11)
    }
    func testFullStorageAndAmbiguousLocalLiveOverlapPreserveDeviceWork() throws {
        let directory = try directory(), frame = try frame()
        let store = try DurableStore(directory: directory)
        store.destinationID = "home"
        let record = try OmiRingRecord(sequence: 10, raw: raw(frame: frame), now: now)
        store.maximumBytes = 10
        XCTAssertThrowsError(try store.copyRingRecords(deviceID: "pendant", ringID: 99, records: [record], codec: 20))
        XCTAssertTrue(store.pending.isEmpty)
        XCTAssertNil(try store.ringRecoveryCursor(deviceID: "pendant", ringID: 99))
        store.maximumBytes = 10_000_000
        try store.begin(deviceID: "pendant", codec: 20, clock: CaptureClock(wall: now, uptime: 100))
        try store.append(frame: frame, decoded: FrameDecoder(codec: 20).decode(frame)); try store.finish()
        XCTAssertThrowsError(try store.copyRingRecords(deviceID: "pendant", ringID: 99, records: [record], codec: 20))
        XCTAssertEqual(store.pending.count, 1)
        XCTAssertNil(try store.ringRecoveryCursor(deviceID: "pendant", ringID: 99))
    }
    func testLocalFrontierSurvivesUploadPruning() throws {
        let store = try DurableStore(directory: directory())
        store.destinationID = "home"
        let record = try OmiRingRecord(sequence: 10, raw: raw(frame: frame()), now: now)
        try store.copyRingRecords(deviceID: "pendant", ringID: 99, records: [record], codec: 20)
        try store.acknowledge(XCTUnwrap(store.pending.first).id)
        try store.pruneAcknowledged(before: now.addingTimeInterval(3600))
        XCTAssertTrue(store.items.filter { $0.kind == .audio }.isEmpty)
        XCTAssertEqual(try store.ringRecoveryCursor(deviceID: "pendant", ringID: 99), 11)
    }
    func testReplayRepairsMissingDerivedAudioBeforeFrontierAdmission() throws {
        let store = try DurableStore(directory: directory())
        store.destinationID = "home"
        let frame = try frame()
        let record = try OmiRingRecord(sequence: 10, raw: raw(frame: frame), now: now)
        try store.copyRingRecords(deviceID: "pendant", ringID: 99, records: [record], codec: 20)
        let item = try XCTUnwrap(store.pending.first)
        try FileManager.default.removeItem(at: store.file(item.id, extension: "wav"))
        XCTAssertEqual(try store.copyRingRecords(deviceID: "pendant", ringID: 99, records: [record], codec: 20), 0)
        XCTAssertEqual(try Data(contentsOf: store.uploadFile(for: item)), WAV.encode(try FrameDecoder(codec: 20).decode(frame)))
    }
    func testNoncontiguousRecordsCannotSkipLocalFrontier() throws {
        let store = try DurableStore(directory: directory())
        store.destinationID = "home"
        let frame = try frame()
        let a = try OmiRingRecord(sequence: 10, raw: raw(frame: frame), now: now)
        let b = try OmiRingRecord(sequence: 12, raw: raw(frame: frame), now: now)
        XCTAssertThrowsError(try store.copyRingRecords(deviceID: "pendant", ringID: 99, records: [a, b], codec: 20))
        XCTAssertTrue(store.pending.isEmpty)
        XCTAssertNil(try store.ringRecoveryCursor(deviceID: "pendant", ringID: 99))
    }

}

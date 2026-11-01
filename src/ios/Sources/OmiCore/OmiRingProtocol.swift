import Foundation
import CryptoKit

/// Verified Omi ring custody v1, revision fbc7973. No destructive command is exposed.
/// Legacy reads may reclaim data during BLE delivery and are deliberately unsupported.
public enum OmiRingProtocol {
    public static let service = "30295780-4301-eabd-2904-2849adfeae43"
    public static let control = "30295781-4301-eabd-2904-2849adfeae43"
    public static let status = "30295782-4301-eabd-2904-2849adfeae43"
    public static let recordSize = 444
    public static let requiredCapabilities: UInt8 = 0x0d
    public static let maximumBatch = 32
    public static let infoCommand = Data([0x10])
    public static let custodyCommand = Data([0x14, 0x01, requiredCapabilities])

    public static func firmwareSupportsRing(_ version: String) -> Bool {
        let parts = version.split(separator: ".").compactMap { Int($0) }
        guard parts.count == 3, parts.allSatisfy({ $0 >= 0 }) else { return false }
        return parts[0] > 3 || (parts[0] == 3 && (parts[1] > 0 || parts[2] >= 20))
    }
    public static func uint(_ data: Data, _ offset: Int, _ size: Int) -> UInt64 {
        data.dropFirst(offset).prefix(size).reduce(0) { ($0 << 8) | UInt64($1) }
    }
    public static func readCommand(start: UInt64, count: Int) -> Data {
        var start = start.bigEndian, count = UInt32(count).bigEndian
        var data = Data([0x11])
        withUnsafeBytes(of: &start) { data.append(contentsOf: $0) }
        withUnsafeBytes(of: &count) { data.append(contentsOf: $0) }
        return data
    }
    public static func rtcValid(_ data: Data) -> Bool {
        guard data.count == 16 else { return false }
        return data[12] == 1 && data[13...15].allSatisfy { $0 == 0 }
    }
    public static func custodyAccepted(_ data: Data) -> Bool {
        data.count >= 3 && data[0] == 1 && data[1] == 0 &&
            data[2] & requiredCapabilities == requiredCapabilities
    }
    public static func stableID(device: String, ring: UInt64, sequence: UInt64) -> UUID {
        let digest = Array(SHA256.hash(data: Data("omi-ring:\(device):\(ring):\(sequence)".utf8)))
        var bytes = Array(digest.prefix(16))
        bytes[6] = (bytes[6] & 0x0f) | 0x50; bytes[8] = (bytes[8] & 0x3f) | 0x80
        return UUID(uuid: (bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
                           bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]))
    }
}

public enum OmiRingError: Error { case unsupported, invalidTransfer, invalidClock, changed, overlap }

public struct OmiRingInfo: Equatable, Sendable {
    public let readSequence: UInt64
    public let writeSequence: UInt64
    public let capacity: UInt64
    public let dropped: UInt64
    public let ringID: UInt64
    public var unread: UInt64 { writeSequence - readSequence }
    public init(_ data: Data) throws {
        guard data.count >= 41, data[0] == 2, data[32] == 1,
              data[31] & OmiRingProtocol.requiredCapabilities == OmiRingProtocol.requiredCapabilities,
              OmiRingProtocol.uint(data, 29, 2) == 444 else { throw OmiRingError.unsupported }
        readSequence = OmiRingProtocol.uint(data, 1, 8)
        writeSequence = OmiRingProtocol.uint(data, 9, 8)
        capacity = OmiRingProtocol.uint(data, 17, 4)
        dropped = OmiRingProtocol.uint(data, 21, 8)
        ringID = OmiRingProtocol.uint(data, 33, 8)
        guard ringID != 0, capacity > 0, writeSequence >= readSequence,
              writeSequence - readSequence <= capacity else { throw OmiRingError.invalidTransfer }
    }
}

public struct OmiRingRecord: Sendable {
    public let sequence: UInt64
    public let raw: Data
    public let capturedAt: Date
    public let frames: [Data]
    public init(sequence: UInt64, raw: Data, now: Date = Date()) throws {
        guard raw.count == 444 else { throw OmiRingError.invalidTransfer }
        let timestamp = OmiRingProtocol.uint(raw, 0, 4)
        // The high bit identifies uptime, not epoch UTC. Never convert it into a date.
        guard timestamp & 0x80000000 == 0, timestamp >= 1_577_836_800,
              Double(timestamp) <= now.timeIntervalSince1970 + 30 else { throw OmiRingError.invalidClock }
        self.sequence = sequence; self.raw = raw
        capturedAt = Date(timeIntervalSince1970: Double(timestamp))
        var frames: [Data] = [], offset = 4
        while offset < raw.count - 1 {
            let size = Int(raw[offset])
            if size == 0 { offset += 1; continue }
            // Firmware leaves a trailing length without its frame at this boundary.
            if offset + 1 + size >= raw.count { break }
            frames.append(Data(raw[(offset + 1)..<(offset + 1 + size)]))
            offset += size + 1
        }
        guard !frames.isEmpty else { throw OmiRingError.invalidTransfer }
        self.frames = frames
    }
}

/// A bounded complete transfer is validated before any record is admitted locally.
public struct OmiRingTransfer {
    public let start: UInt64
    public let count: Int
    public private(set) var complete = false
    private var began = false
    private var bytes = Data()
    public init(start: UInt64, count: Int) throws {
        guard count > 0, count <= OmiRingProtocol.maximumBatch,
              start <= UInt64.max - UInt64(count) else { throw OmiRingError.invalidTransfer }
        self.start = start; self.count = count
    }
    public mutating func receive(_ data: Data) throws {
        guard !data.isEmpty, !complete else { throw OmiRingError.invalidTransfer }
        switch data[0] {
        case 0x05:
            guard !began, data.count == 13, OmiRingProtocol.uint(data, 1, 8) == start,
                  OmiRingProtocol.uint(data, 9, 4) == UInt64(count) else { throw OmiRingError.invalidTransfer }
            began = true
        case 0x03:
            guard began, bytes.count + data.count - 1 <= count * 444 else { throw OmiRingError.invalidTransfer }
            bytes.append(data.dropFirst())
        case 0x04:
            guard began, data.count == 10, data[1] == 0,
                  OmiRingProtocol.uint(data, 2, 8) == start + UInt64(count),
                  bytes.count == count * 444 else { throw OmiRingError.invalidTransfer }
            complete = true
        case 0x01, 0x02, 0x06: break // Other control-plane notifications contain no recording bytes.
        default: throw OmiRingError.invalidTransfer
        }
    }
    public func records(now: Date = Date()) throws -> [OmiRingRecord] {
        guard complete else { throw OmiRingError.invalidTransfer }
        let result = try (0..<count).map { index in
            try OmiRingRecord(sequence: start + UInt64(index),
                              raw: Data(bytes[index * 444..<(index + 1) * 444]), now: now)
        }
        for pair in zip(result, result.dropFirst()) {
            let step = pair.1.capturedAt.timeIntervalSince(pair.0.capturedAt)
            guard step >= 0, step <= 60 else { throw OmiRingError.invalidClock }
        }
        return result
    }
}

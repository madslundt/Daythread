import Foundation

/// Discovery accepts the advertised service or an Omi name, then GATT discovery verifies the device.
public enum OmiDeviceDiscovery {
    public static let audioService = "19b10000-e8f2-537e-4f6c-d104768a1214"

    public static func matches(localName: String?, peripheralName: String?, serviceUUIDs: [String]) -> Bool {
        if serviceUUIDs.contains(where: { $0.lowercased() == audioService }) { return true }
        return [localName, peripheralName].compactMap { $0?.lowercased().trimmingCharacters(in: .whitespacesAndNewlines) }
            .contains { name in
                name == "omi" || ["omi ", "omi-", "omi_"].contains(where: name.hasPrefix)
            }
    }
}

/// Omi notifications: UInt16 sequence (little endian), UInt8 fragment index, payload.
public struct OmiReassembler {
    private var previous: UInt16?
    private var fragment: UInt8?
    private var payload = Data()
    public init() {}

    public struct Result {
        public let frame: Data?
        public let lost: Bool
        public let startedFrame: Bool
        init(frame: Data?, lost: Bool, startedFrame: Bool = false) {
            self.frame = frame; self.lost = lost; self.startedFrame = startedFrame
        }
    }

    public mutating func consume(_ notification: Data) -> Result {
        guard notification.count > 3 else { reset(); return Result(frame: nil, lost: true) }
        let bytes = [UInt8](notification)
        let sequence = UInt16(bytes[0]) | UInt16(bytes[1]) << 8
        let index = bytes[2]
        if previous == sequence { return Result(frame: nil, lost: false) }
        let lost = previous.map { sequence != $0 &+ 1 } ?? false
        previous = sequence
        if lost { payload.removeAll(keepingCapacity: true); fragment = nil }
        if index == 0 {
            let completed = payload.isEmpty ? nil : payload
            payload = Data(bytes.dropFirst(3)); fragment = 0
            return Result(frame: completed, lost: lost, startedFrame: true)
        }
        guard let last = fragment, last < 255, index == last + 1 else {
            payload.removeAll(keepingCapacity: true); fragment = nil
            return Result(frame: nil, lost: true)
        }
        guard payload.count + notification.count - 3 <= 8192 else {
            reset(); return Result(frame: nil, lost: true)
        }
        payload.append(contentsOf: bytes.dropFirst(3)); fragment = index
        return Result(frame: nil, lost: lost)
    }

    /// The protocol has no final-fragment flag. Validate this candidate with the codec.
    public mutating func flush() -> Data? {
        let candidate = payload.isEmpty ? nil : payload
        reset()
        return candidate
    }

    /// A disconnect may leave a truncated candidate. Invalid final audio must not stop reconnection.
    public mutating func flush(using decoder: FrameDecoder) -> (frame: Data, pcm: Data)? {
        guard let frame = flush(), let pcm = try? decoder.decode(frame) else { return nil }
        return (frame, pcm)
    }

    public mutating func reset() { previous = nil; fragment = nil; payload.removeAll() }
}

public enum SemanticAction: String, Codable { case mark, meetingToggle = "meeting_toggle" }
public enum MeetingGesture: String, Codable, CaseIterable { case doubleClick, verifiedHold }

public struct GesturePolicy {
    public var meetingGesture: MeetingGesture
    public var lastAcceptedMark: Date?
    public var cooldown: TimeInterval
    public init(meetingGesture: MeetingGesture = .doubleClick, lastAcceptedMark: Date? = nil, cooldown: TimeInterval = 60) {
        self.meetingGesture = meetingGesture; self.lastAcceptedMark = lastAcceptedMark; self.cooldown = cooldown
    }
    public mutating func resolve(_ data: Data, at date: Date) -> SemanticAction? {
        guard data.count >= 4 else { return nil }
        let b = [UInt8](data.prefix(4))
        let code = UInt32(b[0]) | UInt32(b[1]) << 8 | UInt32(b[2]) << 16 | UInt32(b[3]) << 24
        if (code == 2 && meetingGesture == .doubleClick) || (code == 3 && meetingGesture == .verifiedHold) {
            return .meetingToggle
        }
        guard code == 1 else { return nil }
        guard lastAcceptedMark.map({ date.timeIntervalSince($0) >= cooldown }) ?? true else { return nil }
        lastAcceptedMark = date
        return .mark
    }
}

/// Dates advance from an uptime anchor; wall-clock adjustments cannot compress a recording.
public struct CaptureClock: Codable {
    public let wall: Date
    public let uptime: TimeInterval
    public init(wall: Date = Date(), uptime: TimeInterval = ProcessInfo.processInfo.systemUptime) {
        self.wall = wall; self.uptime = uptime
    }
    public func date(at uptime: TimeInterval) -> Date { wall.addingTimeInterval(max(0, uptime - self.uptime)) }
}

/// Uses monotonic transfer observations; drops estimates when a transfer stalls or restarts.
public struct TransferRate {
    private var startedAt: TimeInterval?
    private var initialBytes: Int64 = 0
    private var sentBytes: Int64 = 0
    private var lastProgressAt: TimeInterval = 0
    public init() {}
    public mutating func observe(sentBytes: Int64, at uptime: TimeInterval) {
        let bytes = max(0, sentBytes)
        if startedAt == nil || bytes < self.sentBytes {
            startedAt = uptime; initialBytes = bytes; lastProgressAt = uptime
        }
        if bytes > self.sentBytes { lastProgressAt = uptime }
        self.sentBytes = bytes
    }
    public func remainingSeconds(for bytes: Int64, at uptime: TimeInterval) -> TimeInterval? {
        guard let startedAt, uptime - startedAt >= 1, uptime - lastProgressAt < 10,
              uptime >= lastProgressAt, sentBytes > initialBytes, bytes > 0 else { return nil }
        let rate = Double(sentBytes - initialBytes) / (uptime - startedAt)
        let seconds = Double(bytes) / rate
        return seconds.isFinite ? seconds : nil
    }
}

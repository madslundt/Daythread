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
    public var hasPendingFrame: Bool { !payload.isEmpty }

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
/// These actions apply only to resolved single/double clicks. Holds remain device power controls.
public enum WearableAction: String, Codable, CaseIterable {
    case mark, meetingToggle = "meeting_toggle", summarize, answer, translate
    case captureToggle = "capture_toggle", none

    public var usesRecentAudio: Bool { self == .summarize || self == .answer || self == .translate }
}

public struct ClickControls: Equatable {
    public static let durations = [15, 30, 60, 120, 300]
    public var singleClick: WearableAction
    public var doubleClick: WearableAction
    public var durationSeconds: Int
    public init(singleClick: String? = nil, doubleClick: String? = nil, durationSeconds: Int = 30) {
        self.singleClick = singleClick.flatMap(WearableAction.init(rawValue:)) ?? .mark
        self.doubleClick = doubleClick.flatMap(WearableAction.init(rawValue:)) ?? .meetingToggle
        self.durationSeconds = Self.durations.contains(durationSeconds) ? durationSeconds : 30
    }
}

/// Physical click feedback uses two distinct, fixed CV1 vibration commands.
public enum OmiHapticDuration: Int, Codable, CaseIterable, Sendable {
    case short = 1, long = 3
    public var command: Data? { Data([UInt8(rawValue)]) }
    public var milliseconds: Int {
        switch self { case .short: 100; case .long: 500 }
    }
}

public enum WearableClick: UInt32 {
    case single = 1, double = 2
    public var hapticDuration: OmiHapticDuration { self == .single ? .short : .long }
    public init?(_ data: Data) {
        guard data.count >= 4 else { return nil }
        let b = [UInt8](data.prefix(4))
        self.init(rawValue: UInt32(b[0]) | UInt32(b[1]) << 8 | UInt32(b[2]) << 16 | UInt32(b[3]) << 24)
    }
    public func action(single: WearableAction, double: WearableAction) -> WearableAction {
        self == .single ? single : double
    }
}

public struct GesturePolicy {
    public var singleClick: WearableAction
    public var doubleClick: WearableAction
    public var lastAcceptedMark: Date?
    public var cooldown: TimeInterval
    public init(singleClick: WearableAction = .mark, doubleClick: WearableAction = .meetingToggle,
                lastAcceptedMark: Date? = nil, cooldown: TimeInterval = 60) {
        self.singleClick = singleClick; self.doubleClick = doubleClick
        self.lastAcceptedMark = lastAcceptedMark; self.cooldown = cooldown
    }
    public mutating func resolve(_ data: Data, at date: Date) -> WearableAction? {
        guard let click = WearableClick(data) else { return nil }
        let action = click.action(single: singleClick, double: doubleClick)
        guard action != .none else { return nil }
        if action == .mark {
            guard lastAcceptedMark.map({ date.timeIntervalSince($0) >= cooldown }) ?? true else { return nil }
            lastAcceptedMark = date
        }
        return action
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

/// Initial GATT setup gets bounded retries; a connected link alone is not readiness.
public struct OmiDiscoveryRecovery {
    public enum Step: Equatable { case wait, retry, exhausted }
    public private(set) var attempts = 0
    public private(set) var retryAt: TimeInterval?
    private let interval: TimeInterval
    private let maximumAttempts: Int
    public init(interval: TimeInterval = 3, maximumAttempts: Int = 3) {
        self.interval = max(0.01, interval); self.maximumAttempts = max(1, maximumAttempts)
    }
    public mutating func start(at uptime: TimeInterval) {
        attempts = 1; retryAt = uptime + interval
    }
    public mutating func cancel() { attempts = 0; retryAt = nil }
    public mutating func advance(at uptime: TimeInterval, ready: Bool) -> Step {
        if ready { cancel(); return .wait }
        guard let retryAt, uptime >= retryAt else { return .wait }
        guard attempts < maximumAttempts else { self.retryAt = nil; return .exhausted }
        attempts += 1; self.retryAt = uptime + interval
        return .retry
    }
}

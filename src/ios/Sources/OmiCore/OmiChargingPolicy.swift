import Foundation

/// Charging holds only pendant capture. Manual pauses and schedules remain separate gates.
public struct OmiChargingPolicy: Equatable, Sendable {
    public private(set) var paused: Bool
    public init(paused: Bool = false) { self.paused = paused }

    public static func decode(_ data: Data) -> Bool? {
        guard data.count == 1, let byte = data.first, byte <= 1 else { return nil }
        return byte == 1
    }

    public mutating func update(charging: Bool?, autoPause: Bool, autoResume: Bool) {
        if !autoPause { paused = false }
        else if charging == true { paused = true }
        else if charging == false && autoResume { paused = false }
        // Unknown status (including a disconnect) never releases a charging hold.
    }

    public mutating func resumeManually(charging: Bool?, autoPause: Bool) {
        paused = autoPause && charging == true
    }
}

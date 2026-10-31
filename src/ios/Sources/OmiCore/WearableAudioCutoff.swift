import Foundation

/// Wait only for the frame already being assembled at the physical click, never later speech.
public struct WearableAudioCutoff {
    private var pending: [UUID: CaptureClock] = [:]
    public var pendingIDs: Set<UUID> { Set(pending.keys) }
    public init() {}

    public mutating func enqueue(store: DurableStore, deviceID: String, end: Date, action: WearableAction,
                                 durationSeconds: Int, pendingFrame: CaptureClock?) throws -> UUID {
        if pendingFrame == nil { try store.finish() }
        let id = try store.wearableAction(deviceID: deviceID, at: end, action: action,
                                          durationSeconds: durationSeconds, awaitingAudioTailAt: pendingFrame?.wall)
        if let pendingFrame { pending[id] = pendingFrame }
        return id
    }
    @discardableResult
    public mutating func completedFrame(_ clock: CaptureClock, store: DurableStore) throws -> Set<UUID> {
        let ids = Set(pending.filter { $0.value.uptime == clock.uptime }.keys)
        guard !ids.isEmpty else { return [] }
        try store.finish()
        try store.finishWearableAudioCutoffs(ids, missingTail: false)
        for id in ids { pending.removeValue(forKey: id) }
        return ids
    }
    @discardableResult
    public mutating func expire(_ ids: Set<UUID>? = nil, store: DurableStore) throws -> Set<UUID> {
        let selected = pendingIDs.intersection(ids ?? pendingIDs)
        guard !selected.isEmpty else { return [] }
        try store.finish()
        try store.finishWearableAudioCutoffs(selected, missingTail: true)
        for id in selected { pending.removeValue(forKey: id) }
        return selected
    }
}

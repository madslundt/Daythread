import Foundation

public struct OutboxItem: Codable, Identifiable {
    public enum Kind: String, Codable { case audio, event, gap, captureState }
    public let id: UUID
    public let kind: Kind
    public let deviceID: String
    public let capturedAt: Date
    public var endedAt: Date?
    public var codec: Int?
    public var clock: CaptureClock?
    public var action: SemanticAction?
    public var meetingMaxMinutes: Int?
    public var meetingMinSeconds: Int?
    public var markerCooldownSeconds: Int?
    public var reason: String?
    public var cause: String?
    public var captureState: String?
    public var ready = false
    public var acknowledged = false
    public var attempts = 0
    public var retryAt = Date.distantPast
    public var error: String?
}

/// Atomic index and append-only original frames. Only acknowledged data can expire.
public final class DurableStore {
    public let directory: URL
    public private(set) var items: [OutboxItem]
    public var maximumBytes: Int64 = 1_000_000_000
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    private var usedBytes: Int64 = 0
    private var source: FileHandle?
    private var currentID: UUID?
    private var pcm = Data()
    public init(directory: URL) throws {
        self.directory = directory
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        #if os(iOS)
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: directory.path)
        #endif
        encoder.dateEncodingStrategy = .millisecondsSince1970; decoder.dateDecodingStrategy = .millisecondsSince1970
        let index = directory.appendingPathComponent("index.json")
        if FileManager.default.fileExists(atPath: index.path) {
            do { items = try decoder.decode([OutboxItem].self, from: Data(contentsOf: index)) }
            catch { throw CaptureError.corruptedStore }
        } else {
            let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            guard files.isEmpty else { throw CaptureError.corruptedStore }
            items = []
        }
        for file in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.fileSizeKey]) {
            usedBytes += Int64(try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0)
            #if os(iOS)
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: file.path)
            #endif
        }
        try recover()
    }
    deinit { try? source?.close() }
    public var pending: [OutboxItem] { items.filter { !$0.acknowledged && $0.ready }.sorted { $0.capturedAt < $1.capturedAt } }
    public var unsyncedSeconds: TimeInterval {
        items.filter { $0.kind == .audio && !$0.acknowledged }.reduce(0) { $0 + max(0, ($1.endedAt ?? $1.capturedAt).timeIntervalSince($1.capturedAt)) }
    }
    public var storageBytes: Int64 { usedBytes }
    public var segmentSeconds: Double { Double(pcm.count) / 32000 }
    public var isRecording: Bool { currentID != nil }
    public func file(_ id: UUID, extension ext: String) -> URL { directory.appendingPathComponent(id.uuidString).appendingPathExtension(ext) }
    public func uploadFile(for item: OutboxItem) throws -> URL {
        let url = file(item.id, extension: item.kind == .audio ? "wav" : "json")
        if item.kind != .audio && !FileManager.default.fileExists(atPath: url.path) {
            try write(metadata(item), to: url)
        }
        return url
    }
    private func size(of url: URL) throws -> Int64 {
        guard FileManager.default.fileExists(atPath: url.path) else { return 0 }
        return Int64(try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0)
    }
    private func write(_ data: Data, to url: URL) throws {
        let previous = try size(of: url)
        var options: Data.WritingOptions = .atomic
        #if os(iOS)
        options.insert(.completeFileProtectionUntilFirstUserAuthentication)
        #endif
        try data.write(to: url, options: options)
        usedBytes += Int64(data.count) - previous
    }
    private func commit(_ updated: [OutboxItem]) throws {
        try write(encoder.encode(updated), to: directory.appendingPathComponent("index.json"))
        items = updated
    }
    private func save() throws { try commit(items) }

    public func begin(deviceID: String, codec: Int, clock: CaptureClock) throws {
        guard currentID == nil else { return }
        try pruneAcknowledged(before: Date().addingTimeInterval(-3600))
        // Reserve a full segment's decoded WAV before accepting more source audio.
        guard usedBytes + 2_000_000 < maximumBytes else { throw CaptureError.storageFull }
        let id = UUID()
        var item = OutboxItem(id: id, kind: .audio, deviceID: deviceID, capturedAt: clock.wall)
        item.codec = codec; item.clock = clock
        try commit(items + [item])
        let url = file(id, extension: "frames")
        try write(Data(), to: url)
        source = try FileHandle(forWritingTo: url); currentID = id; pcm.removeAll()
    }

    public func append(frame: Data, decoded: Data) throws {
        guard currentID != nil else { return }
        guard let source else { throw CaptureError.invalidAudio("The previous segment must finish saving before capture resumes") }
        guard usedBytes + Int64(frame.count + 4 + pcm.count + decoded.count + 44) < maximumBytes else { throw CaptureError.storageFull }
        var size = UInt32(frame.count).littleEndian
        let header = withUnsafeBytes(of: &size) { Data($0) }
        try source.write(contentsOf: header + frame)
        try source.synchronize()
        pcm.append(decoded); usedBytes += Int64(frame.count + 4)
    }

    public func finish() throws {
        guard let id = currentID, let index = items.firstIndex(where: { $0.id == id }) else { return }
        try source?.synchronize(); try source?.close(); source = nil
        guard !pcm.isEmpty else {
            var updated = items; updated.remove(at: index); try commit(updated)
            currentID = nil
            let url = file(id, extension: "frames"); let bytes = try size(of: url)
            try FileManager.default.removeItem(at: url); usedBytes -= bytes
            return
        }
        let wav = WAV.encode(pcm)
        try write(wav, to: file(id, extension: "wav"))
        var updated = items
        updated[index].endedAt = updated[index].capturedAt.addingTimeInterval(Double(pcm.count) / 32000)
        updated[index].ready = true
        try commit(updated)
        currentID = nil; pcm.removeAll()
    }

    @discardableResult
    public func event(deviceID: String, at date: Date, action: SemanticAction, meetingMaxMinutes: Int = 120, meetingMinSeconds: Int = 60, markerCooldownSeconds: Int = 60) throws -> UUID {
        var item = OutboxItem(id: UUID(), kind: .event, deviceID: deviceID, capturedAt: date)
        item.action = action; item.meetingMaxMinutes = meetingMaxMinutes; item.ready = true
        item.meetingMinSeconds = meetingMinSeconds; item.markerCooldownSeconds = markerCooldownSeconds
        return try appendMetadata(item)
    }
    @discardableResult
    public func gap(deviceID: String, start: Date, end: Date, reason: String, cause: String? = nil) throws -> UUID {
        var item = OutboxItem(id: UUID(), kind: .gap, deviceID: deviceID, capturedAt: start)
        item.endedAt = max(start.addingTimeInterval(0.001), end); item.reason = reason; item.cause = cause; item.ready = true
        return try appendMetadata(item)
    }
    @discardableResult
    public func captureState(phoneID: UUID, at date: Date, state: String, cause: String) throws -> UUID {
        var item = OutboxItem(id: UUID(), kind: .captureState, deviceID: phoneID.uuidString, capturedAt: date)
        item.captureState = state; item.cause = cause; item.ready = true
        return try appendMetadata(item)
    }
    private func metadata(_ item: OutboxItem) throws -> Data {
        var body: [String: Any]
        if item.kind == .captureState {
            guard let state = item.captureState, let cause = item.cause else { throw CaptureError.corruptedStore }
            body = ["phone_id": item.deviceID, "occurred_at": Self.stamp(item.capturedAt), "state": state, "cause": cause]
        } else if item.kind == .event {
            guard let action = item.action else { throw CaptureError.corruptedStore }
            body = ["event_id": item.id.uuidString, "device_id": item.deviceID, "occurred_at": Self.stamp(item.capturedAt), "action": action.rawValue,
                    "meeting_max_minutes": item.meetingMaxMinutes ?? 120,
                    "meeting_min_seconds": item.meetingMinSeconds ?? 60,
                    "marker_cooldown_seconds": item.markerCooldownSeconds ?? 60]
        } else {
            guard let endedAt = item.endedAt, let reason = item.reason else { throw CaptureError.corruptedStore }
            body = ["gap_id": item.id.uuidString, "device_id": item.deviceID, "started_at": Self.stamp(item.capturedAt), "ended_at": Self.stamp(endedAt), "reason": reason]
        }
        if item.kind == .gap, let cause = item.cause { body["cause"] = cause }
        return try JSONSerialization.data(withJSONObject: body)
    }
    private func appendMetadata(_ item: OutboxItem) throws -> UUID {
        let body = try metadata(item)
        let updated = items + [item]
        let indexBytes = try encoder.encode(updated).count
        let previousIndexBytes = try size(of: directory.appendingPathComponent("index.json"))
        let reservedWAVBytes = currentID == nil ? 0 : Int64(pcm.count + 44)
        // Keep a small bounded emergency allowance for stop/pause observations when audio
        // has filled its budget. Original unsent recordings are never evicted for metadata.
        let metadataBudget = maximumBytes + (item.kind == .captureState ? 65_536 : 0)
        guard usedBytes + reservedWAVBytes + Int64(body.count + indexBytes) - previousIndexBytes < metadataBudget else { throw CaptureError.storageFull }
        // The index holds the complete event. If writing its upload file is interrupted,
        // recovery rebuilds it with the same ID and capture-time policies.
        try commit(updated)
        try write(body, to: file(item.id, extension: "json"))
        return item.id
    }
    public func acknowledge(_ id: UUID) throws {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        var updated = items; updated[index].acknowledged = true; updated[index].error = nil; try commit(updated)
    }
    public func failed(_ id: UUID, message: String) throws {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        var updated = items; updated[index].attempts += 1; updated[index].error = message
        updated[index].retryAt = Date().addingTimeInterval(min(300, pow(2, Double(min(8, updated[index].attempts)))))
        try commit(updated)
    }
    public func retryNow() throws {
        var updated = items
        for index in updated.indices where !updated[index].acknowledged { updated[index].retryAt = .distantPast }
        try commit(updated)
    }
    public func pruneAcknowledged(before date: Date) throws {
        let expired = items.filter { $0.acknowledged && ($0.endedAt ?? $0.capturedAt) < date }
        for item in expired {
            for ext in ["wav", "frames", "json"] {
                let url = file(item.id, extension: ext)
                if FileManager.default.fileExists(atPath: url.path) {
                    let size = (try url.resourceValues(forKeys: [.fileSizeKey])).fileSize ?? 0
                    try FileManager.default.removeItem(at: url); usedBytes -= Int64(size)
                }
            }
        }
        let ids = Set(expired.filter { $0.kind == .audio }.map(\.id))
        // Keep tiny event/gap records for local replay and cooldown across restarts.
        items.removeAll { ids.contains($0.id) }; try save()
    }
    private func recover() throws {
        for item in items where item.kind != .audio && !item.acknowledged {
            _ = try uploadFile(for: item)
        }
        for index in items.indices where items[index].kind == .audio && !items[index].ready {
            let item = items[index]
            do {
                let decoder = try FrameDecoder(codec: item.codec ?? -1)
                let original = try Data(contentsOf: file(item.id, extension: "frames"))
                var offset = 0; var decoded = Data()
                while offset + 4 <= original.count {
                    let bytes = [UInt8](original[offset..<offset + 4])
                    let size = Int(UInt32(bytes[0]) | UInt32(bytes[1]) << 8 | UInt32(bytes[2]) << 16 | UInt32(bytes[3]) << 24)
                    guard size > 0, size <= 8192, offset + 4 + size <= original.count else { break }
                    do { decoded.append(try decoder.decode(Data(original[offset + 4..<offset + 4 + size]))) }
                    catch { break } // Preserve the valid prefix while retaining all original bytes.
                    offset += 4 + size
                }
                guard !decoded.isEmpty else { items[index].error = "No complete frames survived capture interruption"; continue }
                let wav = WAV.encode(decoded)
                try write(wav, to: file(item.id, extension: "wav"))
                items[index].endedAt = item.capturedAt.addingTimeInterval(Double(decoded.count) / 32000)
                items[index].ready = true
                items[index].error = offset < original.count ? "Recovered; trailing incomplete frame retained in source" : nil
            } catch { items[index].error = "Recovery requires attention: \(error.localizedDescription)" }
        }
        try save()
    }
    public func rewind(minutes: Int, end: Date) throws -> (url: URL, gaps: Int) {
        let duration = max(1, min(60, minutes)) * 60
        let start = end.addingTimeInterval(-Double(duration))
        let audio = items.filter { $0.kind == .audio && $0.ready && $0.capturedAt < end && ($0.endedAt ?? $0.capturedAt) > start }
        guard !audio.isEmpty else { throw CaptureError.invalidAudio("No local audio in this window") }
        var output = Data(count: duration * 32000); var spans: [(Date, Date)] = []
        for item in audio {
            let pcm = try Data(contentsOf: file(item.id, extension: "wav")).dropFirst(44)
            let lower = max(start, item.capturedAt); let upper = min(end, item.endedAt!)
            let sourceOffset = Int(lower.timeIntervalSince(item.capturedAt) * 16000) * 2
            let targetOffset = Int(lower.timeIntervalSince(start) * 16000) * 2
            let count = min(Int(upper.timeIntervalSince(lower) * 16000) * 2, pcm.count - sourceOffset, output.count - targetOffset)
            if count > 0 { output.replaceSubrange(targetOffset..<targetOffset + count, with: pcm.dropFirst(sourceOffset).prefix(count)); spans.append((lower, upper)) }
        }
        var cursor = start; var gaps = 0
        for span in spans.sorted(by: { $0.0 < $1.0 }) {
            if span.0.timeIntervalSince(cursor) > 0.05 { gaps += 1 }
            cursor = max(cursor, span.1)
        }
        if end.timeIntervalSince(cursor) > 0.05 { gaps += 1 }
        let url = directory.deletingLastPathComponent().appendingPathComponent("local-rewind.wav")
        try WAV.encode(output).write(to: url, options: .atomic)
        return (url, gaps)
    }
    public static func stamp(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }
}

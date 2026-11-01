import Foundation

/// Atomic index and append-only original frames. Only acknowledged data can expire.
public final class DurableStore {
    public let directory: URL
    public private(set) var items: [OutboxItem]
    public var profileID = "default"
    public var destinationID: String?
    public var maximumBytes: Int64 = 1_000_000_000
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    private var usedBytes: Int64 = 0
    private var source: FileHandle?
    private var currentID: UUID?
    private var pcm = Data()
    private var synchronizedPCMBytes = 0
    private let synchronizeSource: (FileHandle) throws -> Void
    public convenience init(directory: URL) throws {
        try self.init(directory: directory, synchronizeSource: { try $0.synchronize() })
    }
    init(directory: URL, synchronizeSource: @escaping (FileHandle) throws -> Void) throws {
        self.directory = directory
        self.synchronizeSource = synchronizeSource
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
    public func pending(destinationID: String) -> [OutboxItem] { pending.filter { $0.destinationID == destinationID } }
    /// Physical actions and only their original audio dependencies precede bulk capture uploads.
    public func nextUpload(destinationID: String, at date: Date = Date(), excluding excluded: Set<UUID> = []) -> OutboxItem? {
        let queued = pending(destinationID: destinationID)
        for request in queued where request.prioritizesUpload && !excluded.contains(request.id) {
            if let next = nextSubmissionUpload(request.id, destinationID: destinationID, at: date, excluding: excluded) { return next }
        }
        return queued.first { !$0.prioritizesUpload && $0.retryAt <= date && !excluded.contains($0.id) }
    }
    public func nextSubmissionUpload(_ id: UUID, destinationID: String, at date: Date = Date(), excluding excluded: Set<UUID> = []) -> OutboxItem? {
        guard let request = items.first(where: { $0.id == id }), request.destinationID == destinationID,
              request.prioritizesUpload, !request.acknowledged, request.ready, request.retryAt <= date,
              !excluded.contains(id) else { return nil }
        if let gapID = request.cutoffGapID, let gap = items.first(where: { $0.id == gapID && !$0.acknowledged }) {
            return gap.ready && gap.retryAt <= date && !excluded.contains(gapID) ? gap : nil
        }
        let audio = audioDependencies(for: request)
        if !audio.isEmpty {
            return audio.first { $0.ready && $0.retryAt <= date && !excluded.contains($0.id) }
        }
        return request
    }
    public func audioDependencies(for request: OutboxItem) -> [OutboxItem] {
        guard request.kind == .generation, let duration = request.durationSeconds else { return [] }
        let frozen = request.audioDependencyIDs.map(Set.init)
        let start = request.capturedAt.addingTimeInterval(-Double(duration))
        return items.filter { audio in
            audio.kind == .audio && !audio.acknowledged && audio.destinationID == request.destinationID &&
            audio.resolvedProfileID == request.resolvedProfileID && audio.deviceID == request.deviceID &&
            (frozen?.contains(audio.id) ?? (audio.capturedAt < request.capturedAt && (audio.endedAt ?? request.capturedAt) > start))
        }.sorted { $0.capturedAt < $1.capturedAt }
    }
    public func prioritize(_ id: UUID) throws {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        var updated = items; updated[index].userInitiated = true; try commit(updated)
    }
    /// Upgrade only unscoped legacy records, before accepting another account or backend.
    public func bindLegacyDestination(_ identity: String) throws {
        guard items.contains(where: { $0.destinationID == nil }) else { return }
        try commit(items.map { original in
            var item = original
            if item.destinationID == nil { item.destinationID = identity }
            return item
        })
    }
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

    /// Non-destructive ring copies use exact incarnation/sequence IDs, not timestamps for deduplication.
    /// The local frontier survives upload pruning; it never advances the pendant's own read frontier.
    public func ringRecoveryCursor(deviceID: String, ringID: UInt64) throws -> UInt64? {
        let url = directory.appendingPathComponent("ring-receipts.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let receipts = try decoder.decode([String: UInt64].self, from: Data(contentsOf: url))
        return receipts[ringReceiptKey(deviceID: deviceID, ringID: ringID)]
    }
    private func ringReceiptKey(deviceID: String, ringID: UInt64) -> String {
        "\(destinationID ?? ""):\(profileID):\(deviceID):\(ringID)"
    }
    @discardableResult
    public func copyRingRecords(deviceID: String, ringID: UInt64, records: [OmiRingRecord], codec: Int) throws -> Int {
        guard !isRecording, destinationID != nil, [20, 21].contains(codec), !records.isEmpty,
              records.count <= OmiRingProtocol.maximumBatch else { throw OmiRingError.unsupported }
        for pair in zip(records, records.dropFirst()) {
            guard pair.0.sequence < UInt64.max, pair.1.sequence == pair.0.sequence + 1,
                  pair.1.capturedAt >= pair.0.capturedAt,
                  pair.1.capturedAt.timeIntervalSince(pair.0.capturedAt) <= 60 else {
                throw OmiRingError.invalidTransfer
            }
        }
        var updated = items, added = 0
        func sync(_ url: URL) throws {
            let handle = try FileHandle(forWritingTo: url)
            defer { try? handle.close() }
            try synchronizeSource(handle)
        }
        guard records.last!.sequence < UInt64.max else { throw OmiRingError.invalidTransfer }
        for record in records {
            let id = OmiRingProtocol.stableID(device: deviceID, ring: ringID, sequence: record.sequence)
            let decoder = try FrameDecoder(codec: codec)
            var pcm = Data(), original = Data()
            for frame in record.frames {
                pcm.append(try decoder.decode(frame))
                var length = UInt32(frame.count).littleEndian
                withUnsafeBytes(of: &length) { original.append(contentsOf: $0) }
                original.append(frame)
            }
            if let item = updated.first(where: { $0.id == id }) {
                guard item.destinationID == destinationID, item.resolvedProfileID == profileID,
                      item.sourceID == "omi-ring:\(deviceID):\(ringID):\(record.sequence)", item.ready,
                      item.codec == codec, item.capturedAt == record.capturedAt,
                      try Data(contentsOf: file(id, extension: "ring")) == record.raw else { throw OmiRingError.changed }
                // A replay repairs derived files from the exact original record;
                // it never grants a local frontier merely because an index row exists.
                for (ext, data) in [("frames", original), ("wav", WAV.encode(pcm))] {
                    let url = file(id, extension: ext)
                    if (try? Data(contentsOf: url)) != data {
                        let additional = max(0, Int64(data.count) - (try size(of: url)))
                        guard usedBytes + additional + 8192 < maximumBytes else { throw CaptureError.storageFull }
                        try write(data, to: url); try sync(url)
                    }
                }
                continue
            }
            let end = record.capturedAt.addingTimeInterval(Double(pcm.count) / 32000)
            // Timing alone cannot prove identity with existing live capture. Leave
            // the device's original untouched and require explicit source inspection.
            if items.contains(where: { $0.kind == .audio && $0.deviceID == deviceID &&
                $0.sourceKind != "manual_upload" && $0.destinationID == destinationID &&
                $0.resolvedProfileID == profileID && $0.capturedAt < end.addingTimeInterval(1) &&
                ($0.endedAt ?? $0.capturedAt) > record.capturedAt.addingTimeInterval(-1) }) {
                throw OmiRingError.overlap
            }
            guard usedBytes + Int64(record.raw.count + original.count + pcm.count + 8192) < maximumBytes else { throw CaptureError.storageFull }
            try write(record.raw, to: file(id, extension: "ring")); try sync(file(id, extension: "ring"))
            try write(original, to: file(id, extension: "frames")); try sync(file(id, extension: "frames"))
            try write(WAV.encode(pcm), to: file(id, extension: "wav")); try sync(file(id, extension: "wav"))
            var item = OutboxItem(id: id, kind: .audio, deviceID: deviceID, capturedAt: record.capturedAt)
            item.destinationID = destinationID; item.profileID = profileID; item.codec = codec
            item.sourceKind = "manual_upload"; item.sourceID = "omi-ring:\(deviceID):\(ringID):\(record.sequence)"
            item.endedAt = end; item.ready = true
            updated.append(item); added += 1
        }
        try commit(updated); try sync(directory.appendingPathComponent("index.json"))
        let receiptURL = directory.appendingPathComponent("ring-receipts.json")
        var receipts = FileManager.default.fileExists(atPath: receiptURL.path)
            ? try decoder.decode([String: UInt64].self, from: Data(contentsOf: receiptURL)) : [:]
        let key = ringReceiptKey(deviceID: deviceID, ringID: ringID)
        receipts[key] = max(receipts[key] ?? 0, records.last!.sequence + 1)
        try write(encoder.encode(receipts), to: receiptURL); try sync(receiptURL)
        return added
    }

    public func begin(deviceID: String, codec: Int, clock: CaptureClock, location: CaptureLocation? = nil) throws {
        guard currentID == nil else { return }
        try pruneAcknowledged(before: Date().addingTimeInterval(-3600))
        // Reserve a full segment's decoded WAV before accepting more source audio.
        guard usedBytes + 2_000_000 < maximumBytes else { throw CaptureError.storageFull }
        let id = UUID()
        var item = OutboxItem(id: id, kind: .audio, deviceID: deviceID, capturedAt: clock.wall)
        item.profileID = profileID
        item.destinationID = destinationID
        item.location = location.flatMap { $0.isUsable(at: clock.wall) ? $0 : nil }
        item.codec = codec; item.clock = clock
        try commit(items + [item])
        let url = file(id, extension: "frames")
        try write(Data(), to: url)
        source = try FileHandle(forWritingTo: url); currentID = id; pcm.removeAll(); synchronizedPCMBytes = 0
    }

    public func append(frame: Data, decoded: Data) throws {
        guard currentID != nil else { return }
        guard let source else { throw CaptureError.invalidAudio(AppLocalization.source("The previous segment must finish saving before capture resumes")) }
        guard usedBytes + Int64(frame.count + 4 + pcm.count + decoded.count + 44) < maximumBytes else { throw CaptureError.storageFull }
        var size = UInt32(frame.count).littleEndian
        let header = withUnsafeBytes(of: &size) { Data($0) }
        try source.write(contentsOf: header + frame)
        pcm.append(decoded); usedBytes += Int64(frame.count + 4)
        // Write every frame for process-crash recovery, but force storage sync only
        // once per second of 16 kHz PCM. Finish always syncs the final partial tail.
        // A sudden device power loss can therefore lose up to one second of audio.
        if pcm.count - synchronizedPCMBytes >= 32_000 {
            try synchronizeSource(source)
            synchronizedPCMBytes = pcm.count
        }
    }

    public func finish() throws {
        guard let id = currentID, let index = items.firstIndex(where: { $0.id == id }) else { return }
        if let source { try synchronizeSource(source); try source.close() }; source = nil
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
    public func event(deviceID: String, at date: Date, action: SemanticAction, meetingMaxMinutes: Int = 120, meetingMinSeconds: Int = 60, markerCooldownSeconds: Int = 60, meetingIntent: String? = nil, meetingID: UUID? = nil) throws -> UUID {
        var item = OutboxItem(id: UUID(), kind: .event, deviceID: deviceID, capturedAt: date)
        // Flags never carry meeting targets, even while a meeting is active.
        item.meetingIntent = action == .meetingToggle ? meetingIntent : nil
        item.meetingID = item.meetingIntent == "end" ? meetingID : nil
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
    @discardableResult
    public func wearableAction(deviceID: String, at date: Date, action: WearableAction,
                               durationSeconds: Int, language: String? = nil, awaitingAudioTailAt: Date? = nil) throws -> UUID {
        guard action.usesRecentAudio, ClickControls.durations.contains(durationSeconds) else { throw CaptureError.corruptedStore }
        var item = OutboxItem(id: UUID(), kind: .generation, deviceID: deviceID, capturedAt: date)
        item.wearableAction = action; item.durationSeconds = durationSeconds; item.language = language
        item.awaitingAudioTailAt = awaitingAudioTailAt; item.ready = awaitingAudioTailAt == nil
        item.cutoffGapID = awaitingAudioTailAt == nil ? nil : UUID()
        let start = date.addingTimeInterval(-Double(durationSeconds))
        item.audioDependencyIDs = items.filter {
            $0.kind == .audio && $0.destinationID == destinationID && $0.resolvedProfileID == profileID &&
            $0.deviceID == deviceID && $0.capturedAt < date && ($0.endedAt ?? date) > start
        }.map(\.id)
        return try appendMetadata(item)
    }
    public func finishWearableAudioCutoffs(_ ids: Set<UUID>, missingTail: Bool) throws {
        for id in ids {
            guard let original = items.first(where: { $0.id == id }), original.kind == .generation,
                  let tail = original.awaitingAudioTailAt, let duration = original.durationSeconds else { continue }
            var gapID: UUID?
            if missingTail {
                let stableID = original.cutoffGapID ?? UUID()
                if original.cutoffGapID == nil, let index = items.firstIndex(where: { $0.id == id }) {
                    var prepared = items; prepared[index].cutoffGapID = stableID; try commit(prepared)
                }
                if let existing = items.first(where: { $0.id == stableID }) {
                    guard existing.kind == .gap, existing.destinationID == original.destinationID,
                          existing.resolvedProfileID == original.resolvedProfileID,
                          existing.deviceID == original.deviceID, existing.endedAt == original.capturedAt else { throw CaptureError.corruptedStore }
                    gapID = stableID
                } else {
                    var gap = OutboxItem(id: stableID, kind: .gap, deviceID: original.deviceID,
                                        capturedAt: min(tail, original.capturedAt.addingTimeInterval(-0.001)))
                    gap.profileID = original.profileID; gap.destinationID = original.destinationID
                    gap.endedAt = original.capturedAt; gap.reason = AppLocalization.source("Incomplete final BLE frame discarded")
                    gap.cause = "incomplete_frame"; gap.ready = true
                    gapID = try appendMetadata(gap, preserveOwnership: true)
                }
            }
            guard let index = items.firstIndex(where: { $0.id == id }) else { continue }
            let start = original.capturedAt.addingTimeInterval(-Double(duration))
            var updated = items
            updated[index].audioDependencyIDs = items.filter {
                $0.kind == .audio && $0.ready && $0.destinationID == original.destinationID &&
                $0.resolvedProfileID == original.resolvedProfileID && $0.deviceID == original.deviceID &&
                $0.capturedAt < original.capturedAt && ($0.endedAt ?? original.capturedAt) > start
            }.map(\.id)
            updated[index].awaitingAudioTailAt = nil; updated[index].cutoffGapID = gapID; updated[index].ready = true
            try commit(updated)
        }
    }
    private func metadata(_ item: OutboxItem) throws -> Data {
        var body: [String: Any]
        if item.kind == .generation {
            guard let action = item.wearableAction, action.usesRecentAudio, let duration = item.durationSeconds else { throw CaptureError.corruptedStore }
            var parameters: [String: Any] = ["action": action.rawValue, "duration_seconds": duration, "end": Self.stamp(item.capturedAt), "device_id": item.deviceID]
            if action == .translate, let language = item.language { parameters["language"] = language }
            body = ["id": item.id.uuidString.lowercased(), "kind": "wearable_action", "parameters": parameters]
        } else if item.kind == .captureState {
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
        if item.kind == .event {
            if let intent = item.meetingIntent { body["meeting_intent"] = intent }
            if let id = item.meetingID { body["meeting_id"] = id.uuidString }
        }
        if item.kind == .gap, let cause = item.cause { body["cause"] = cause }
        return try JSONSerialization.data(withJSONObject: body, options: .sortedKeys)
    }
    private func appendMetadata(_ original: OutboxItem, preserveOwnership: Bool = false) throws -> UUID {
        var item = original
        if !preserveOwnership { item.profileID = profileID; item.destinationID = destinationID }
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
    public func retryNow(_ id: UUID) throws {
        guard let index = items.firstIndex(where: { $0.id == id }), !items[index].acknowledged else { return }
        var updated = items; updated[index].retryAt = .distantPast; try commit(updated)
    }
    public func pruneAcknowledged(before date: Date) throws {
        let expired = items.filter { $0.acknowledged && ($0.endedAt ?? $0.capturedAt) < date }
        for item in expired {
            for ext in ["wav", "frames", "json", "ring"] {
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
                guard !decoded.isEmpty else { items[index].error = AppLocalization.source("No complete frames survived capture interruption"); continue }
                let wav = WAV.encode(decoded)
                try write(wav, to: file(item.id, extension: "wav"))
                items[index].endedAt = item.capturedAt.addingTimeInterval(Double(decoded.count) / 32000)
                items[index].ready = true
                items[index].error = offset < original.count ? AppLocalization.source("Recovered; trailing incomplete frame retained in source") : nil
            } catch { items[index].error = AppLocalization.source("Recovery requires attention: \(error.localizedDescription)") }
        }
        try save()
        let interrupted = Set(items.filter { $0.kind == .generation && !$0.ready && $0.awaitingAudioTailAt != nil }.map(\.id))
        try finishWearableAudioCutoffs(interrupted, missingTail: true)
    }
    /// Copy only current decoded samples; this never closes capture or mutates outbox custody.
    public func transcriptionPreview(seconds: Int = 15) -> Data? {
        guard let currentID, !pcm.isEmpty,
              let item = items.first(where: { $0.id == currentID }),
              item.destinationID == destinationID, item.resolvedProfileID == profileID else { return nil }
        let count = min(pcm.count, max(1, min(15, seconds)) * 32000)
        return WAV.encode(Data(pcm.suffix(count - count % 2)))
    }

    /// Uses the same archive and time window as playback, including acknowledged buffer audio.
    public func canRewind(minutes: Int, end: Date) -> Bool {
        rewindAudio(minutes: minutes, end: end).contains {
            FileManager.default.fileExists(atPath: file($0.id, extension: "wav").path)
        }
    }

    private func rewindAudio(minutes: Int, end: Date) -> [OutboxItem] {
        let start = end.addingTimeInterval(-Double(max(1, min(60, minutes)) * 60))
        return items.filter {
            $0.destinationID == destinationID && $0.resolvedProfileID == profileID && $0.kind == .audio
                && $0.ready && $0.capturedAt < end && ($0.endedAt ?? $0.capturedAt) > start
        }
    }

    public func rewind(minutes: Int, end: Date) throws -> (url: URL, gaps: Int) {
        let duration = max(1, min(60, minutes)) * 60
        let start = end.addingTimeInterval(-Double(duration))
        let audio = rewindAudio(minutes: minutes, end: end)
        guard !audio.isEmpty else { throw CaptureError.invalidAudio(AppLocalization.source("No local audio in this window")) }
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

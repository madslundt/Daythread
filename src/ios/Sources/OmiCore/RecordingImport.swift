import Foundation

public enum RecordingTimeReference: String, CaseIterable, Identifiable, Sendable {
    case end, start
    public var id: Self { self }
    public var title: String { self == .end ? "Recording ended" : "Recording started" }
}

public struct RecordingImportError: LocalizedError, Sendable {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var errorDescription: String? { message }
}

public struct RecordingBounds: Equatable, Sendable {
    public let startedAt: Date
    public let endedAt: Date

    public init(recordedAt: Date, sampleCount: Int, reference: RecordingTimeReference, now: Date = Date()) throws {
        guard recordedAt.timeIntervalSince1970.isFinite else {
            throw RecordingImportError("Choose a valid recording date and time.")
        }
        try RecordingImport.validateSampleCount(sampleCount)
        let duration = Double(sampleCount) / Double(RecordingImport.sampleRate)
        let end = reference == .end ? recordedAt : recordedAt.addingTimeInterval(duration)
        guard end <= now.addingTimeInterval(30) else {
            throw RecordingImportError(reference == .end
                ? "Choose when the recording stopped, at or before now."
                : "This recording would end in the future. Choose when it began, or enter when it stopped.")
        }
        startedAt = end.addingTimeInterval(-duration)
        endedAt = end
    }
}

public enum RecordingImport {
    public static let sampleRate = 16_000
    public static let samplesPerPart = sampleRate * 120
    public static let maximumSamples = sampleRate * 7200
    // This is the shared manual-upload protocol identifier required by the backend.
    public static let deviceID = "manual-web"

    public static func validateFile(name: String, bytes: Int) throws {
        guard !name.isEmpty, name.unicodeScalars.count <= 255 else {
            throw RecordingImportError("Rename this file to a name of 255 characters or fewer before uploading.")
        }
        guard bytes > 0 else { throw RecordingImportError("The recording is empty. Choose another file.") }
        guard bytes <= 100_000_000 else { throw RecordingImportError("Choose a recording no larger than 100 MB.") }
    }

    public static func validateSampleCount(_ count: Int) throws {
        guard count > 0 else { throw RecordingImportError("The recording contains no audio.") }
        guard count <= maximumSamples else { throw RecordingImportError("Split recordings longer than two hours before uploading.") }
    }

    public static func sampleRanges(count: Int) throws -> [Range<Int>] {
        try validateSampleCount(count)
        return stride(from: 0, to: count, by: samplesPerPart).map { $0..<min($0 + samplesPerPart, count) }
    }

    public static func date(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }

    public static func suggestedEnd(modifiedAt: Date?, now: Date = Date()) -> Date? {
        guard let modifiedAt, modifiedAt.timeIntervalSince1970 > 0, modifiedAt <= now else { return nil }
        return modifiedAt
    }

    public static func uploadURL(base: URL, id: UUID, query: [String: String]) -> URL {
        let url = base.appendingPathComponent("v1/audio/\(id.uuidString.lowercased())")
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        components.queryItems = query.keys.sorted().map { URLQueryItem(name: $0, value: query[$0]) }
        // URLComponents permits literal '+', but the server parses query strings
        // as form values. Escape it to preserve UTC offsets and filenames.
        components.percentEncodedQuery = components.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        return components.url!
    }
}

public struct RecordingUploadPlan: Decodable, Sendable {
    public struct Part: Decodable, Sendable {
        public let id: UUID
        public let digest: String
        public let uploaded: Bool
    }
    public let recordingID: UUID
    public let sourceID: String
    public let startedAt: String
    public let endedAt: String
    public let conversationSourceID: UUID?
    public let parts: [Part]

    enum CodingKeys: String, CodingKey {
        case recordingID = "recording_id", sourceID = "source_id", startedAt = "started_at"
        case endedAt = "ended_at", conversationSourceID = "conversation_source_id", parts
    }

    public func validate(digests: [String], sampleCount: Int) throws {
        let ranges = try RecordingImport.sampleRanges(count: sampleCount)
        guard parts.count == ranges.count, parts.map(\.digest) == digests,
              Set(parts.map(\.id)).count == parts.count, !sourceID.isEmpty, sourceID.unicodeScalars.count <= 255,
              let start = RecordingImport.date(startedAt), let end = RecordingImport.date(endedAt),
              abs(end.timeIntervalSince(start) - Double(sampleCount) / Double(RecordingImport.sampleRate)) < 0.002 else {
            throw RecordingImportError("The server returned a mismatched recording upload plan. Retry to confirm it.")
        }
    }

    public func query(for index: Int) throws -> [String: String] {
        guard parts.indices.contains(index), let start = RecordingImport.date(startedAt) else {
            throw RecordingImportError("The recording upload plan is invalid.")
        }
        var query = [
            "device_id": RecordingImport.deviceID,
            "started_at": DurableStore.stamp(start.addingTimeInterval(Double(index * 120))),
            "source_kind": "manual_upload", "source_id": sourceID,
            "recording_id": recordingID.uuidString.lowercased(), "part_index": String(index),
            "part_count": String(parts.count), "recording_started_at": startedAt, "recording_ended_at": endedAt,
        ]
        if let conversationSourceID { query["conversation_source_id"] = conversationSourceID.uuidString.lowercased() }
        return query
    }

    public func validateReceipt(_ data: Data, part index: Int, sampleCount: Int) throws {
        guard parts.indices.contains(index), sampleCount > 0, sampleCount <= RecordingImport.samplesPerPart,
              let start = RecordingImport.date(startedAt) else { throw CaptureError.invalidReceipt }
        let capturedAt = start.addingTimeInterval(Double(index * 120))
        var item = OutboxItem(id: parts[index].id, kind: .audio, deviceID: RecordingImport.deviceID, capturedAt: capturedAt)
        item.endedAt = capturedAt.addingTimeInterval(Double(sampleCount) / Double(RecordingImport.sampleRate))
        try UploadReceipt.validate(data, for: item)
    }
}

public enum RecordingPreparation: Decodable, Sendable {
    case duplicate(sourceID: String)
    case upload(RecordingUploadPlan)

    private enum CodingKeys: String, CodingKey { case status, sourceID = "source_id" }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        switch try values.decode(String.self, forKey: .status) {
        case "duplicate": self = .duplicate(sourceID: try values.decode(String.self, forKey: .sourceID))
        case "upload": self = .upload(try RecordingUploadPlan(from: decoder))
        default: throw RecordingImportError("The server returned an invalid recording upload plan.")
        }
    }
}

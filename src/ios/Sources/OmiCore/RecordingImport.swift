import Foundation
import AVFoundation

public enum RecordingTimeReference: String, CaseIterable, Identifiable, Sendable {
    case end, start
    public var id: Self { self }
    public var title: String { self == .end ? AppLocalization.text("Recording ended") : AppLocalization.text("Recording started") }
}

public struct RecordingImportError: LocalizedError, Sendable {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var errorDescription: String? { AppLocalization.key(message) }
}

public struct RecordingBounds: Equatable, Sendable {
    public let startedAt: Date
    public let endedAt: Date

    public init(recordedAt: Date, sampleCount: Int, reference: RecordingTimeReference, now: Date = Date()) throws {
        guard recordedAt.timeIntervalSince1970.isFinite else {
            throw RecordingImportError(AppLocalization.source("Choose a valid recording date and time."))
        }
        try RecordingImport.validateSampleCount(sampleCount)
        let duration = Double(sampleCount) / Double(RecordingImport.sampleRate)
        let end = reference == .end ? recordedAt : recordedAt.addingTimeInterval(duration)
        guard end <= now.addingTimeInterval(30) else {
            throw RecordingImportError(reference == .end
                ? AppLocalization.source("Choose when the recording stopped, at or before now.")
                : AppLocalization.source("This recording would end in the future. Choose when it began, or enter when it stopped."))
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

    public static func vendorParameters(vendorID: String) -> [String: String] {
        vendorID == "manual" ? [:] : ["vendor_id": vendorID]
    }

    public static func validateFile(name: String, bytes: Int) throws {
        guard !name.isEmpty, name.unicodeScalars.count <= 255 else {
            throw RecordingImportError(AppLocalization.source("Rename this file to a name of 255 characters or fewer before uploading."))
        }
        guard bytes > 0 else { throw RecordingImportError(AppLocalization.source("The recording is empty. Choose another file.")) }
        guard bytes <= 100_000_000 else { throw RecordingImportError(AppLocalization.source("Choose a recording no larger than 100 MB.")) }
    }

    public static func validateSampleCount(_ count: Int) throws {
        guard count > 0 else { throw RecordingImportError(AppLocalization.source("The recording contains no audio.")) }
        guard count <= maximumSamples else { throw RecordingImportError(AppLocalization.source("Split recordings longer than two hours before uploading.")) }
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

    /// Returns a recording timestamp embedded in the audio container, when present.
    /// Audio exporters often preserve this even when the exported file's filesystem
    /// dates are reset to the time of export or sharing.
    public static func embeddedRecordingDate(in url: URL) async -> Date? {
        let asset = AVURLAsset(url: url)
        guard let metadata = try? await asset.load(.commonMetadata) else { return nil }
        for item in metadata {
            let commonKey = item.commonKey?.rawValue.lowercased() ?? ""
            let identifier = item.identifier?.rawValue.lowercased() ?? ""
            guard commonKey.contains("creationdate") || identifier.contains("creationdate")
                    || identifier.contains("recordingtime") || identifier.hasSuffix("/tdrc")
                    || identifier.hasSuffix("/tyer") else { continue }
            if let date = try? await item.load(.dateValue), suggestedEnd(modifiedAt: date) != nil { return date }
            if let value = try? await item.load(.stringValue), let date = parseMetadataDate(value),
               suggestedEnd(modifiedAt: date) != nil { return date }
        }
        return nil
    }

    private static func parseMetadataDate(_ value: String) -> Date? {
        let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: text) { return date }
        iso.formatOptions = [.withInternetDateTime]
        if let date = iso.date(from: text) { return date }

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = .current
        for format in ["yyyy-MM-dd'T'HH:mm:ss.SSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd"] {
            formatter.dateFormat = format
            if let date = formatter.date(from: text) { return date }
        }
        return nil
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

public struct VendorDefinition: Decodable, Identifiable, Sendable {
    public let id: String
    public let label: String
    public let capabilities: [String]
    public let setupURL: String
    public let importInstructions: String
    public let configured: Bool
    public let keySetting: String?

    public var supportsFileUpload: Bool { capabilities.contains("file_upload") }
    public static let manual = VendorDefinition(
        id: "manual", label: AppLocalization.source("Other device"), capabilities: ["file_upload"], setupURL: "",
        importInstructions: AppLocalization.source("Choose an exported audio file and its original recording time."),
        configured: false, keySetting: nil
    )

    private enum CodingKeys: String, CodingKey {
        case id, label, capabilities, configured
        case setupURL = "setup_url", importInstructions = "import_instructions", keySetting = "key_setting"
    }
}

public struct VendorCatalog: Decodable, Sendable {
    public let items: [VendorDefinition]
    public var fileUploadVendors: [VendorDefinition] { items.filter(\.supportsFileUpload) }
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
    public let vendorID: String?
    public let parts: [Part]

    public var resolvedVendorID: String { vendorID ?? "manual" }

    enum CodingKeys: String, CodingKey {
        case recordingID = "recording_id", sourceID = "source_id", startedAt = "started_at"
        case endedAt = "ended_at", conversationSourceID = "conversation_source_id", parts
        case vendorID = "vendor_id"
    }

    public func validate(digests: [String], sampleCount: Int) throws {
        let ranges = try RecordingImport.sampleRanges(count: sampleCount)
        guard parts.count == ranges.count, parts.map(\.digest) == digests,
              Set(parts.map(\.id)).count == parts.count, !sourceID.isEmpty, sourceID.unicodeScalars.count <= 255,
              !resolvedVendorID.isEmpty, resolvedVendorID.count <= 80,
              let start = RecordingImport.date(startedAt), let end = RecordingImport.date(endedAt),
              abs(end.timeIntervalSince(start) - Double(sampleCount) / Double(RecordingImport.sampleRate)) < 0.002 else {
            throw RecordingImportError(AppLocalization.source("The server returned a mismatched recording upload plan. Retry to confirm it."))
        }
    }

    public func query(for index: Int) throws -> [String: String] {
        guard parts.indices.contains(index), let start = RecordingImport.date(startedAt) else {
            throw RecordingImportError(AppLocalization.source("The recording upload plan is invalid."))
        }
        var query = [
            "device_id": RecordingImport.deviceID,
            "started_at": DurableStore.stamp(start.addingTimeInterval(Double(index * 120))),
            "source_kind": "manual_upload", "source_id": sourceID,
            "recording_id": recordingID.uuidString.lowercased(), "part_index": String(index),
            "part_count": String(parts.count), "recording_started_at": startedAt, "recording_ended_at": endedAt,
        ]
        if let conversationSourceID { query["conversation_source_id"] = conversationSourceID.uuidString.lowercased() }
        query.merge(RecordingImport.vendorParameters(vendorID: resolvedVendorID)) { _, server in server }
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
        default: throw RecordingImportError(AppLocalization.source("The server returned an invalid recording upload plan."))
        }
    }
}

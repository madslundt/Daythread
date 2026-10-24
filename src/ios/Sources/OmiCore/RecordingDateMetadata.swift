import Foundation
import AVFoundation

public struct RecordingDateSuggestion: Equatable, Sendable {
    public let date: Date
    public let reference: RecordingTimeReference
    /// A localization source key describing the metadata actually used.
    public let source: String
    public let needsConfirmation: Bool
    fileprivate let priority: Int

    public init(date: Date, reference: RecordingTimeReference, source: String, needsConfirmation: Bool) {
        self.init(date: date, reference: reference, source: source, needsConfirmation: needsConfirmation, priority: 1)
    }

    fileprivate init(date: Date, reference: RecordingTimeReference, source: String, needsConfirmation: Bool, priority: Int) {
        self.date = date
        self.reference = reference
        self.source = source
        self.needsConfirmation = needsConfirmation
        self.priority = priority
    }

    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.date == rhs.date && lhs.reference == rhs.reference && lhs.source == rhs.source
            && lhs.needsConfirmation == rhs.needsConfirmation
    }
}

extension RecordingImport {
    /// Recording timestamps take priority over container creation and filesystem
    /// modification. Filesystem creation is deliberately never a capture clock.
    public static func dateSuggestion(in url: URL, modifiedAt: Date? = nil, now: Date = Date(),
                                      timeZone: TimeZone = .current) async -> RecordingDateSuggestion? {
        let asset = AVURLAsset(url: url)
        var metadata = (try? await asset.load(.commonMetadata)) ?? []
        for format in (try? await asset.load(.availableMetadataFormats)) ?? [] {
            metadata += (try? await asset.loadMetadata(for: format)) ?? []
        }
        // BWF origination fields are not exposed consistently by AVFoundation.
        let wav = wavDateSuggestion(in: url, now: now, timeZone: timeZone)
        let embedded = await metadataDateSuggestion(from: metadata, now: now, timeZone: timeZone)
        if let best = [wav, embedded].compactMap({ $0 }).min(by: { $0.priority < $1.priority }) { return best }
        guard let date = suggestedEnd(modifiedAt: modifiedAt, now: now) else { return nil }
        return RecordingDateSuggestion(date: date, reference: .end,
                                       source: AppLocalization.source("File modification date"), needsConfirmation: true)
    }

    static func metadataDateSuggestion(from metadata: [AVMetadataItem], now: Date,
                                       timeZone: TimeZone) async -> RecordingDateSuggestion? {
        var creation: RecordingDateSuggestion?
        for item in metadata {
            let identifier = item.identifier?.rawValue.lowercased() ?? ""
            let key = (item.key as? String)?.lowercased() ?? ""
            let common = item.commonKey?.rawValue.lowercased() ?? ""
            let fields = [identifier, key, common]
            // ID3 release, encoding, and tagging clocks can share a common date
            // key but describe a different event from recording.
            let nonRecordingID3 = ["tdor", "tdrl", "tden", "tdtg", "tyer", "tory"]
            guard !nonRecordingID3.contains(where: { identifier.hasSuffix("/" + $0) || key == $0 }) else { continue }
            let recording = fields.contains { value in
                value == "tdrc" || value.hasSuffix("/tdrc") || value.contains("recordingtime")
                    || value.contains("recordingdate") || value.contains("recorded_date")
                    || value.contains("recorded_at")
            }
            let container = fields.contains { $0.contains("creationdate") || $0 == "©day" || $0 == "icrd" }
            guard recording || container else { continue }
            // Parsing the string first preserves precision: a year-only ID3 date
            // can otherwise be coerced by AVFoundation into January 1 at midnight.
            let value: String
            if let string = try? await item.load(.stringValue) { value = string }
            else if let date = try? await item.load(.dateValue) { value = DurableStore.stamp(date) }
            else { continue }
            guard let parsed = parseRecordingTimestamp(value, now: now, timeZone: timeZone) else { continue }
            let suggestion = RecordingDateSuggestion(date: parsed.date, reference: .start,
                source: recording ? AppLocalization.source("Embedded recording date") : AppLocalization.source("Embedded creation date"),
                needsConfirmation: !recording || parsed.assumedTimeZone, priority: recording ? 0 : 1)
            if recording { return suggestion }
            if creation == nil { creation = suggestion }
        }
        return creation
    }

    /// Accept a complete date and clock time only, never a year or date alone.
    static func parseRecordingTimestamp(_ value: String, now: Date, timeZone: TimeZone) -> (date: Date, assumedTimeZone: Bool)? {
        let text = value.trimmingCharacters(in: .whitespacesAndNewlines.union(.controlCharacters))
        let pattern = #"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(:\d{2})?(\.\d{1,9})?(Z|[+-]\d{2}:?\d{2})?$"#
        guard let match = text.range(of: pattern, options: .regularExpression), match == text.startIndex..<text.endIndex else { return nil }
        let hasZone = text.range(of: #"(Z|[+-]\d{2}:?\d{2})$"#, options: .regularExpression) != nil
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = timeZone
        formatter.isLenient = false
        for clock in ["HH:mm:ss.SSSSSSSSS", "HH:mm:ss", "HH:mm"] {
            for separator in ["'T'", "' '"] {
                formatter.dateFormat = "yyyy-MM-dd" + separator + clock + (hasZone ? "XXXXX" : "")
                if let date = formatter.date(from: text), suggestedEnd(modifiedAt: date, now: now) != nil {
                    return (date, !hasZone)
                }
            }
        }
        return nil
    }

    private static func wavDateSuggestion(in url: URL, now: Date, timeZone: TimeZone) -> RecordingDateSuggestion? {
        guard let file = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? file.close() }
        guard let header = try? file.read(upToCount: 12), header.count == 12,
              String(data: header.prefix(4), encoding: .ascii) == "RIFF",
              String(data: header.suffix(4), encoding: .ascii) == "WAVE" else { return nil }
        let fileEnd = (try? file.seekToEnd()) ?? 0
        var offset: UInt64 = 12
        var creation: RecordingDateSuggestion?
        // Seek over audio, keeping reads bounded even for twelve-hour recordings.
        for _ in 0..<1024 {
            guard offset + 8 <= fileEnd else { break }
            do {
                try file.seek(toOffset: offset)
                guard let chunk = try file.read(upToCount: 8), chunk.count == 8 else { break }
                let name = String(data: chunk.prefix(4), encoding: .ascii)
                let size = chunk.dropFirst(4).enumerated().reduce(UInt64(0)) { $0 | UInt64($1.element) << ($1.offset * 8) }
                guard offset + 8 + size <= fileEnd else { break }
                if name == "bext", size >= 338, let body = try file.read(upToCount: 338), body.count == 338 {
                    let date = String(data: body[320..<330], encoding: .ascii) ?? ""
                    let time = String(data: body[330..<338], encoding: .ascii) ?? ""
                    if let parsed = parseRecordingTimestamp(date + "T" + time, now: now, timeZone: timeZone) {
                        return RecordingDateSuggestion(date: parsed.date, reference: .start,
                            source: AppLocalization.source("WAV recording date"), needsConfirmation: parsed.assumedTimeZone, priority: 0)
                    }
                } else if name == "LIST", size <= 65_536,
                          let body = try file.read(upToCount: Int(size)), body.count >= 4,
                          String(data: body.prefix(4), encoding: .ascii) == "INFO" {
                    creation = wavInfoDate(body, now: now, timeZone: timeZone) ?? creation
                }
                offset += 8 + size + size % 2
            } catch { break }
        }
        return creation
    }

    private static func wavInfoDate(_ body: Data, now: Date, timeZone: TimeZone) -> RecordingDateSuggestion? {
        var offset = 4
        while offset + 8 <= body.count {
            let name = String(data: body[offset..<(offset + 4)], encoding: .ascii)
            let size = body[(offset + 4)..<(offset + 8)].enumerated().reduce(0) { $0 | Int($1.element) << ($1.offset * 8) }
            guard size <= body.count - offset - 8 else { break }
            if name == "ICRD", let text = String(data: body[(offset + 8)..<(offset + 8 + size)], encoding: .utf8),
               let parsed = parseRecordingTimestamp(text, now: now, timeZone: timeZone) {
                return RecordingDateSuggestion(date: parsed.date, reference: .start,
                    source: AppLocalization.source("WAV creation date"), needsConfirmation: true)
            }
            offset += 8 + size + size % 2
        }
        return nil
    }
}

import XCTest
import AVFoundation
@testable import OmiCore

final class RecordingDateMetadataTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)
    private let utc = TimeZone(secondsFromGMT: 0)!

    func testRecordingTimestampWinsOverExportCreationAndFileModification() async throws {
        let metadata = [item("mdta/com.apple.quicktime.creationdate", "2026-10-23T10:00:00Z"),
                        item("id3/TDRC", "2026-10-20T09:12:34+02:00")]
        let result = await RecordingImport.metadataDateSuggestion(from: metadata, now: now, timeZone: utc)
        let suggestion = try XCTUnwrap(result)
        XCTAssertEqual(suggestion.date, RecordingImport.date("2026-10-20T07:12:34Z"))
        XCTAssertEqual(suggestion.reference, .start)
        XCTAssertFalse(suggestion.needsConfirmation)
    }

    func testImpreciseAndExportRelatedDatesAreNotRecordingTimes() async {
        for (key, value) in [("id3/TDRC", "2026"), ("id3/TDRC", "2026-10-20"),
                             ("id3/TYER", "2026"), ("id3/TDOR", "2026-10-20T09:12:34Z"),
                             ("id3/TDEN", "2026-10-20T09:12:34Z"),
                             ("mdta/com.apple.quicktime.creationdate", "2026-02-30T09:12:34Z"),
                             ("id3/TDRC", "2030-01-01T10:00:00Z")] {
            let suggestion = await RecordingImport.metadataDateSuggestion(from: [item(key, value)], now: now, timeZone: utc)
            XCTAssertNil(suggestion, "\(key): \(value)")
        }
    }

    func testUnzonedTimestampUsesProvidedTimezoneAndRequiresConfirmation() async throws {
        let timeZone = TimeZone(identifier: "Europe/Copenhagen")!
        let metadata = [item("id3/TDRC", "2026-10-20 09:12:34")]
        let result = await RecordingImport.metadataDateSuggestion(from: metadata, now: now, timeZone: timeZone)
        let suggestion = try XCTUnwrap(result)
        XCTAssertEqual(suggestion.date, RecordingImport.date("2026-10-20T07:12:34Z"))
        XCTAssertTrue(suggestion.needsConfirmation)
        let creation = await RecordingImport.metadataDateSuggestion(
            from: [item("mdta/com.apple.quicktime.creationdate", "2026-10-20T09:12:34Z")], now: now, timeZone: utc)
        let container = try XCTUnwrap(creation)
        XCTAssertEqual(container.reference, .start)
        XCTAssertTrue(container.needsConfirmation, "Container creation can be export time")
    }

    func testFullTimestampPrecisionAndOffsetsArePreserved() throws {
        for value in ["2026-10-20T09:12:34.125+02:00", "2026-10-20T09:12:34.125+0200",
                      "2026-10-20T07:12:34.125Z"] {
            let parsed = try XCTUnwrap(RecordingImport.parseRecordingTimestamp(value, now: now, timeZone: utc))
            XCTAssertEqual(parsed.date.timeIntervalSince1970,
                           RecordingImport.date("2026-10-20T07:12:34.125Z")!.timeIntervalSince1970, accuracy: 0.001)
            XCTAssertFalse(parsed.assumedTimeZone)
        }
    }

    func testBroadcastWAVOriginationTimeSurvivesFilesystemDateChanges() async throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".wav")
        defer { try? FileManager.default.removeItem(at: url) }
        var broadcast = Data(repeating: 0, count: 602)
        broadcast.replaceSubrange(320..<330, with: Data("2026-10-20".utf8))
        broadcast.replaceSubrange(330..<338, with: Data("09:12:34".utf8))
        try wav(chunks: [("bext", broadcast)]).write(to: url)
        let modified = RecordingImport.date("2026-10-23T10:00:00Z")!
        let result = await RecordingImport.dateSuggestion(in: url, modifiedAt: modified, now: now, timeZone: utc)
        let suggestion = try XCTUnwrap(result)
        XCTAssertEqual(suggestion.date, RecordingImport.date("2026-10-20T09:12:34Z"))
        XCTAssertEqual(suggestion.reference, .start)
        XCTAssertTrue(suggestion.needsConfirmation)
    }

    func testWAVInfoTimestampAndMissingMetadataFallback() async throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".wav")
        defer { try? FileManager.default.removeItem(at: url) }
        let info = Data("INFO".utf8) + chunk("ICRD", Data("2026-10-20T09:12:34+02:00\0".utf8))
        try wav(chunks: [("LIST", info)]).write(to: url)
        let embedded = await RecordingImport.dateSuggestion(in: url, now: now, timeZone: utc)
        XCTAssertEqual(embedded?.date, RecordingImport.date("2026-10-20T07:12:34Z"))
        try wav(chunks: []).write(to: url)
        let absent = await RecordingImport.dateSuggestion(in: url, now: now, timeZone: utc)
        XCTAssertNil(absent)
        let modified = now.addingTimeInterval(-100)
        let fallback = await RecordingImport.dateSuggestion(in: url, modifiedAt: modified, now: now, timeZone: utc)
        XCTAssertEqual(fallback?.date, modified)
        XCTAssertEqual(fallback?.reference, .end)
        XCTAssertEqual(fallback?.needsConfirmation, true)
    }

    func testM4ACreationMetadataIsReadAfterFileIsCopiedAndRenamed() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let source = folder.appendingPathComponent("source.wav")
        try wav(chunks: []).write(to: source)
        let target = folder.appendingPathComponent("export.m4a")
        let export = try XCTUnwrap(AVAssetExportSession(asset: AVURLAsset(url: source), presetName: AVAssetExportPresetAppleM4A))
        export.outputURL = target
        export.outputFileType = .m4a
        export.metadata = [item(AVMetadataIdentifier.commonIdentifierCreationDate.rawValue, "2026-10-20T09:12:34+02:00")]
        await withCheckedContinuation { continuation in
            export.exportAsynchronously { continuation.resume() }
        }
        XCTAssertEqual(export.status, .completed, export.error?.localizedDescription ?? "Export failed")
        let renamed = folder.appendingPathComponent("shared-recording.m4a")
        try FileManager.default.copyItem(at: target, to: renamed)
        let result = await RecordingImport.dateSuggestion(in: renamed, modifiedAt: now, now: now, timeZone: utc)
        XCTAssertEqual(result?.date, RecordingImport.date("2026-10-20T07:12:34Z"))
        XCTAssertEqual(result?.reference, .start)
        XCTAssertEqual(result?.source, "Embedded creation date")
    }

    func testFilenameTimestampDefaultsBeforeFileModificationWhenMetadataIsMissing() async throws {
        let modified = RecordingImport.date("2026-10-23T10:00:00Z")!
        for name in ["Meeting_2026-10-20_09-12-34.wav", "20261020_091234.wav",
                     "Recording 2026-10-20 09:12:34.wav", "2026-10-20T09:12:34+02:00.wav"] {
            let suggestion = try await filenameSuggestion(name, modifiedAt: modified)
            let expected = name.contains("+02:00") ? "2026-10-20T07:12:34Z" : "2026-10-20T09:12:34Z"
            XCTAssertEqual(suggestion?.date, RecordingImport.date(expected), name)
            XCTAssertEqual(suggestion?.reference, .end, name)
            XCTAssertEqual(suggestion?.source, "Filename date and time", name)
            XCTAssertEqual(suggestion?.needsConfirmation, true, name)
        }
    }

    func testFilenameClockWithoutTimezoneUsesLocalTimezone() async throws {
        let suggestion = try await filenameSuggestion("2026-10-20_09-12.wav",
            timeZone: TimeZone(identifier: "Europe/Copenhagen")!)
        XCTAssertEqual(suggestion?.date, RecordingImport.date("2026-10-20T07:12:00Z"))
        XCTAssertEqual(suggestion?.needsConfirmation, true)
    }

    func testStagedFilenameKeepsYearlessDateAndMissingFilesystemClockStaysReviewable() async throws {
        let prefix = UUID().uuidString + "__"
        let modified = RecordingImport.date("2026-10-30T12:32:11Z")!
        let staged = try await filenameSuggestion(prefix + "10-29_Weekly_Meeting.wav", modifiedAt: modified)
        XCTAssertEqual(staged?.date, RecordingImport.date("2026-10-29T12:32:11Z"))
        let withoutModified = try await filenameSuggestion("2026-10-29_Weekly_Meeting.wav")
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        let guessed = try XCTUnwrap(withoutModified)
        XCTAssertEqual(calendar.dateComponents([.hour, .minute, .second], from: guessed.date),
                       calendar.dateComponents([.hour, .minute, .second], from: now))
        XCTAssertEqual(guessed.source, "Filename date (time needs review)")
        XCTAssertTrue(guessed.needsConfirmation)
    }

    func testDateOnlyFilenameRetainsFallbackClockAndDisclosesMissingTime() async throws {
        let modified = RecordingImport.date("2026-10-30T12:32:11Z")!
        for name in ["2026-10-29_Weekly_Meeting.wav", "10-29_Weekly_Meeting.wav"] {
            let suggestion = try await filenameSuggestion(name, modifiedAt: modified)
            XCTAssertEqual(suggestion?.date, RecordingImport.date("2026-10-29T12:32:11Z"), name)
            XCTAssertEqual(suggestion?.source, "Filename date (time needs review)", name)
            XCTAssertEqual(suggestion?.needsConfirmation, true, name)
        }
        let newYear = RecordingImport.date("2026-01-02T12:32:11Z")!
        let previousYear = try await filenameSuggestion("12-31_Meeting.wav", modifiedAt: newYear)
        XCTAssertEqual(previousYear?.date, RecordingImport.date("2025-12-31T12:32:11Z"))
    }

    func testInvalidAmbiguousAndUnrelatedFilenameNumbersKeepFilesystemFallback() async throws {
        let modified = RecordingImport.date("2026-10-30T12:32:11Z")!
        for name in ["2026-02-30_10-30-00.wav", "2030-10-20_10-30-00.wav", "2026-10-20_25-12-34.wav",
                     "2026-10-20_to_2026-10-21.wav", "Meeting_03-04-26.wav", "Meeting_123456789012345.wav"] {
            let suggestion = try await filenameSuggestion(name, modifiedAt: modified)
            XCTAssertEqual(suggestion?.date, modified, name)
            XCTAssertEqual(suggestion?.source, "File modification date", name)
        }
    }

    func testEmbeddedTimestampStillWinsOverFilenameTimestamp() async throws {
        let info = Data("INFO".utf8) + chunk("ICRD", Data("2026-10-20T09:12:34Z\0".utf8))
        let suggestion = try await filenameSuggestion("2026-10-21_10-30-00.wav", chunks: [("LIST", info)])
        XCTAssertEqual(suggestion?.date, RecordingImport.date("2026-10-20T09:12:34Z"))
        XCTAssertEqual(suggestion?.source, "WAV creation date")
    }

    private func filenameSuggestion(_ name: String, modifiedAt: Date? = nil, timeZone: TimeZone? = nil,
                                    chunks: [(String, Data)] = []) async throws -> RecordingDateSuggestion? {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let url = folder.appendingPathComponent(name)
        try wav(chunks: chunks).write(to: url)
        return await RecordingImport.dateSuggestion(in: url, modifiedAt: modifiedAt, now: now, timeZone: timeZone ?? utc)
    }

    private func item(_ identifier: String, _ value: String) -> AVMetadataItem {
        let item = AVMutableMetadataItem()
        item.identifier = AVMetadataIdentifier(rawValue: identifier)
        item.dataType = "com.apple.metadata.datatype.UTF-8"
        item.value = value as NSString
        return item
    }

    private func wav(chunks: [(String, Data)]) -> Data {
        let format: [UInt8] = [1, 0, 1, 0, 0x80, 0x3e, 0, 0, 0, 0x7d, 0, 0, 2, 0, 16, 0]
        let body = Data("WAVE".utf8) + chunk("fmt ", Data(format))
            + chunks.reduce(Data()) { $0 + chunk($1.0, $1.1) } + chunk("data", Data(repeating: 0, count: 320))
        return Data("RIFF".utf8) + littleEndian(body.count) + body
    }
    private func chunk(_ name: String, _ body: Data) -> Data {
        Data(name.utf8) + littleEndian(body.count) + body + Data(repeating: 0, count: body.count % 2)
    }
    private func littleEndian(_ value: Int) -> Data {
        withUnsafeBytes(of: UInt32(value).littleEndian) { Data($0) }
    }
}

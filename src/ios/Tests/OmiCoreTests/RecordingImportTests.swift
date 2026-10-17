import XCTest
import AVFoundation
@testable import OmiCore

final class RecordingImportTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testEndAndStartReferencesPreserveOriginalCaptureTime() throws {
        let selected = now.addingTimeInterval(-1000)
        let ended = try RecordingBounds(recordedAt: selected, sampleCount: 16_000 * 180 + 1, reference: .end, now: now)
        XCTAssertEqual(ended.endedAt, selected)
        XCTAssertEqual(ended.endedAt.timeIntervalSince(ended.startedAt), 180.0000625, accuracy: 0.000001)
        let started = try RecordingBounds(recordedAt: ended.startedAt, sampleCount: 16_000 * 180 + 1, reference: .start, now: now)
        XCTAssertEqual(started.endedAt.timeIntervalSince(ended.endedAt), 0, accuracy: 0.000001)
    }

    func testFutureEndIsRejectedRegardlessOfTimeReference() throws {
        XCTAssertThrowsError(try RecordingBounds(recordedAt: now.addingTimeInterval(31), sampleCount: 16_000, reference: .end, now: now))
        XCTAssertThrowsError(try RecordingBounds(recordedAt: now, sampleCount: 16_000 * 60, reference: .start, now: now))
        XCTAssertNoThrow(try RecordingBounds(recordedAt: now, sampleCount: 16_000 * 30, reference: .start, now: now))
        XCTAssertThrowsError(try RecordingBounds(recordedAt: now, sampleCount: 0, reference: .end, now: now))
    }

    func testChunkBoundariesKeepEverySampleWithinBackendLimits() throws {
        let limit = RecordingImport.samplesPerPart
        XCTAssertEqual(try RecordingImport.sampleRanges(count: limit), [0..<limit])
        XCTAssertEqual(try RecordingImport.sampleRanges(count: limit + 1), [0..<limit, limit..<(limit + 1)])
        let ranges = try RecordingImport.sampleRanges(count: RecordingImport.maximumSamples)
        XCTAssertEqual(ranges.count, 60)
        XCTAssertEqual(ranges.reduce(0) { $0 + $1.count }, RecordingImport.maximumSamples)
        for range in ranges {
            XCTAssertLessThanOrEqual(range.count * 2 + 44, 4_000_000)
            XCTAssertLessThanOrEqual(Double(range.count) / 16_000, 120)
        }
        XCTAssertThrowsError(try RecordingImport.sampleRanges(count: RecordingImport.maximumSamples + 1))
    }

    func testFileLimitsAndSuggestedTimesDoNotInventCaptureTimes() throws {
        XCTAssertNoThrow(try RecordingImport.validateFile(name: "Voice Memo.m4a", bytes: 100_000_000))
        XCTAssertThrowsError(try RecordingImport.validateFile(name: "a.wav", bytes: 100_000_001))
        XCTAssertThrowsError(try RecordingImport.validateFile(name: "a.wav", bytes: 0))
        XCTAssertThrowsError(try RecordingImport.validateFile(name: String(repeating: "a", count: 256), bytes: 1))
        XCTAssertNil(RecordingImport.suggestedEnd(modifiedAt: nil, now: now))
        XCTAssertNil(RecordingImport.suggestedEnd(modifiedAt: now.addingTimeInterval(1), now: now))
        XCTAssertNil(RecordingImport.suggestedEnd(modifiedAt: Date(timeIntervalSince1970: 0), now: now))
        XCTAssertEqual(RecordingImport.suggestedEnd(modifiedAt: now.addingTimeInterval(-60), now: now), now.addingTimeInterval(-60))
    }

    func testResumedPlanRetainsServerIDsFilenameTimesAndConfirmedParts() throws {
        let plan = try decode(planBody())
        try plan.validate(digests: ["first", "last"], sampleCount: 16_000 * 121)
        XCTAssertTrue(plan.parts[0].uploaded)
        XCTAssertFalse(plan.parts[1].uploaded)
        XCTAssertEqual(plan.parts[1].id.uuidString.lowercased(), "22222222-2222-4222-8222-222222222222")
        let query = try plan.query(for: 1)
        XCTAssertEqual(query["source_id"], "Original café & meeting.m4a")
        XCTAssertEqual(query["started_at"], "2026-01-02T10:02:00.125Z")
        XCTAssertEqual(query["recording_started_at"], "2026-01-02T10:00:00.125Z")
        XCTAssertEqual(query["recording_ended_at"], "2026-01-02T10:02:01.125Z")
        XCTAssertEqual(query["conversation_source_id"], "44444444-4444-4444-8444-444444444444")
        XCTAssertEqual(query["part_index"], "1")
        XCTAssertEqual(query["part_count"], "2")
        XCTAssertEqual(query["source_kind"], "manual_upload")
        XCTAssertThrowsError(try plan.query(for: 2))
    }

    func testMismatchedOrRepeatedPlanPartsCannotReleaseAudio() throws {
        let plan = try decode(planBody())
        XCTAssertThrowsError(try plan.validate(digests: ["first", "wrong"], sampleCount: 16_000 * 121))
        XCTAssertThrowsError(try plan.validate(digests: ["first", "last"], sampleCount: 16_000 * 122))
        var body = planBody()
        var parts = body["parts"] as! [[String: Any]]
        parts[1]["id"] = parts[0]["id"]
        body["parts"] = parts
        XCTAssertThrowsError(try decode(body).validate(digests: ["first", "last"], sampleCount: 16_000 * 121))
    }

    func testLegacyAndExplicitManualPlansKeepExistingUploadProtocol() throws {
        let legacy = try decode(planBody())
        XCTAssertEqual(legacy.resolvedVendorID, "manual")
        XCTAssertNil(try legacy.query(for: 0)["vendor_id"])
        var body = planBody()
        body["vendor_id"] = "manual"
        XCTAssertEqual(try decode(body).query(for: 0), try legacy.query(for: 0))
        body["vendor_id"] = NSNull()
        XCTAssertEqual(try decode(body).resolvedVendorID, "manual")
        XCTAssertTrue(RecordingImport.vendorParameters(vendorID: "manual").isEmpty)
    }

    func testBrandedServerPlanPreservesVendorAcrossAllRetriedParts() throws {
        var body = planBody()
        body["vendor_id"] = "plaud"
        let plan = try decode(body)
        try plan.validate(digests: ["first", "last"], sampleCount: 16_000 * 121)
        XCTAssertEqual(plan.resolvedVendorID, "plaud")
        for index in plan.parts.indices {
            let query = try plan.query(for: index)
            XCTAssertEqual(query["vendor_id"], "plaud")
            XCTAssertEqual(query["source_kind"], "manual_upload")
            XCTAssertEqual(query["device_id"], RecordingImport.deviceID)
        }
        XCTAssertEqual(RecordingImport.vendorParameters(vendorID: plan.resolvedVendorID), ["vendor_id": "plaud"])
    }

    func testFutureBrandsUseCatalogCapabilitiesWithoutAClientRelease() throws {
        let body: [String: Any] = ["items": [
            ["id": "new-recorder", "label": "New recorder", "capabilities": ["file_upload", "new_capability"],
             "setup_url": "https://example.com", "import_instructions": "Export your recording.", "configured": false],
            ["id": "cloud-only", "label": "Cloud", "capabilities": ["transcript_import"],
             "setup_url": "", "import_instructions": "", "configured": true, "key_setting": "cloud_key"],
        ]]
        let catalog = try JSONDecoder().decode(VendorCatalog.self, from: data(body))
        XCTAssertEqual(catalog.fileUploadVendors.map(\.id), ["new-recorder"])
        XCTAssertNil(catalog.items[0].keySetting)
        XCTAssertEqual(catalog.items[1].keySetting, "cloud_key")
        var plan = planBody()
        plan["vendor_id"] = catalog.fileUploadVendors[0].id
        XCTAssertEqual(try decode(plan).query(for: 1)["vendor_id"], "new-recorder")
    }

    func testInvalidBrandInServerPlanIsRejected() throws {
        for vendor in ["", String(repeating: "a", count: 81)] {
            var body = planBody()
            body["vendor_id"] = vendor
            XCTAssertThrowsError(try decode(body).validate(digests: ["first", "last"], sampleCount: 16_000 * 121))
        }
    }

    func testEachPartRequiresItsOwnReceiptBeforeContinuing() throws {
        let plan = try decode(planBody())
        var receipt: [String: Any] = [
            "id": plan.parts[1].id.uuidString.lowercased(), "device_id": RecordingImport.deviceID,
            "started_at": "2026-01-02T10:02:00.125Z", "ended_at": "2026-01-02T10:02:01.125Z", "state": "queued",
        ]
        XCTAssertNoThrow(try plan.validateReceipt(data(receipt), part: 1, sampleCount: 16_000))
        receipt["id"] = plan.parts[0].id.uuidString
        XCTAssertThrowsError(try plan.validateReceipt(data(receipt), part: 1, sampleCount: 16_000))
        receipt["id"] = plan.parts[1].id.uuidString
        receipt["ended_at"] = "2026-01-02T10:02:02.125Z"
        XCTAssertThrowsError(try plan.validateReceipt(data(receipt), part: 1, sampleCount: 16_000))
        XCTAssertThrowsError(try plan.validateReceipt(Data("{\"ok\":true}".utf8), part: 1, sampleCount: 16_000))
    }

    func testDuplicateAndUnknownPreparationResponses() throws {
        let duplicate = try JSONDecoder().decode(RecordingPreparation.self, from: data(["status": "duplicate", "source_id": "memo.m4a"]))
        guard case .duplicate(let name) = duplicate else { return XCTFail("Expected duplicate") }
        XCTAssertEqual(name, "memo.m4a")
        XCTAssertThrowsError(try JSONDecoder().decode(RecordingPreparation.self, from: data(["status": "ok"])))
    }

    func testUploadQueryPreservesServerUTCOffsetsAndLiteralFilenamePlusSigns() throws {
        let query = ["recording_started_at": "2026-10-17T10:17:17.926000+00:00", "source_id": "Møde + notes & ideas.m4a"]
        let url = RecordingImport.uploadURL(base: URL(string: "http://localhost:8799")!, id: UUID(), query: query)
        let encoded = URLComponents(url: url, resolvingAgainstBaseURL: false)!.percentEncodedQuery!
        XCTAssertFalse(encoded.contains("+"))
        XCTAssertTrue(encoded.contains("%2B00:00"))
        // Reproduce the server's form-query handling before decoding percent escapes.
        let decoded = Dictionary(uniqueKeysWithValues: encoded.split(separator: "&").map { field in
            let pair = field.split(separator: "=", maxSplits: 1).map(String.init)
            return (pair[0].removingPercentEncoding!, pair[1].replacingOccurrences(of: "+", with: " ").removingPercentEncoding!)
        })
        XCTAssertEqual(decoded, query)
    }

    func testStreamingConversionResamplesStereoAndKeepsFinalPartialChunk() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("stereo.wav")
        try writeStereo(to: source, seconds: 121)
        let recording = try await RecordingAudioConverter.convert(source, into: root.appendingPathComponent("converted"))
        XCTAssertEqual(recording.sampleCount, 121 * 16_000)
        XCTAssertEqual(recording.parts.map(\.samples), [120 * 16_000, 16_000])
        let copy = root.appendingPathComponent("same-audio.wav")
        try FileManager.default.copyItem(at: source, to: copy)
        let repeated = try await RecordingAudioConverter.convert(copy, into: root.appendingPathComponent("repeated"))
        XCTAssertEqual(repeated.parts.map(\.digest), recording.parts.map(\.digest), "Repeated conversions must retain upload plan identities")
        for part in recording.parts {
            let bytes = try Data(contentsOf: part.url)
            XCTAssertEqual(bytes.count, part.bytes)
            XCTAssertLessThan(bytes.count, 4_000_000)
            XCTAssertEqual(String(data: bytes.prefix(4), encoding: .utf8), "RIFF")
            let file = try AVAudioFile(forReading: part.url)
            XCTAssertEqual(file.fileFormat.sampleRate, 16_000)
            XCTAssertEqual(file.fileFormat.channelCount, 1)
            XCTAssertEqual(file.length, Int64(part.samples))
            XCTAssertEqual(part.digest, try RecordingAudioConverter.fileDigest(part.url))
            XCTAssertTrue(bytes.dropFirst(44).contains { $0 != 0 })
        }
    }

    func testHashMatchesSHA256AndConversionRejectsNonAudio() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("invalid.wav")
        try Data("abc".utf8).write(to: file)
        XCTAssertEqual(try RecordingAudioConverter.fileDigest(file), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        do {
            _ = try await RecordingAudioConverter.convert(file, into: root.appendingPathComponent("parts"))
            XCTFail("Invalid audio must fail before upload")
        } catch { }
    }

    func testVoiceMemoM4AConvertsDeterministicallyWithOriginalDuration() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("voice-memo.m4a")
        try writeStereo(to: source, seconds: 3)
        let original = try await RecordingAudioConverter.convert(source, into: root.appendingPathComponent("original"))
        let copy = root.appendingPathComponent("renamed.m4a")
        try FileManager.default.copyItem(at: source, to: copy)
        let repeated = try await RecordingAudioConverter.convert(copy, into: root.appendingPathComponent("repeated"))
        XCTAssertEqual(original.sampleCount, 3 * 16_000)
        XCTAssertEqual(original.parts.map(\.digest), repeated.parts.map(\.digest))
    }

    private func writeStereo(to url: URL, seconds: Int) throws {
        let format = AVAudioFormat(standardFormatWithSampleRate: 44_100, channels: 2)!
        var settings = format.settings
        settings[AVLinearPCMIsNonInterleaved] = false
        if url.pathExtension == "m4a" {
            settings = [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 44_100, AVNumberOfChannelsKey: 2, AVEncoderBitRateKey: 128_000]
        }
        let file = try AVAudioFile(forWriting: url, settings: settings)
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4096)!
        var remaining = seconds * 44_100
        var offset = 0
        while remaining > 0 {
            let count = min(4096, remaining)
            buffer.frameLength = AVAudioFrameCount(count)
            for sample in 0..<count {
                let signal = Float(sin(Double(offset + sample) * 440 * 2 * .pi / 44_100)) * 0.2
                buffer.floatChannelData![0][sample] = signal
                buffer.floatChannelData![1][sample] = signal * 0.5
            }
            try file.write(from: buffer)
            remaining -= count; offset += count
        }
    }

    private func planBody() -> [String: Any] {
        ["status": "upload", "recording_id": "33333333-3333-4333-8333-333333333333",
         "source_id": "Original café & meeting.m4a", "started_at": "2026-01-02T10:00:00.125Z",
         "ended_at": "2026-01-02T10:02:01.125Z", "conversation_source_id": "44444444-4444-4444-8444-444444444444",
         "parts": [["id": "11111111-1111-4111-8111-111111111111", "digest": "first", "uploaded": true],
                   ["id": "22222222-2222-4222-8222-222222222222", "digest": "last", "uploaded": false]]]
    }
    private func data(_ body: [String: Any]) throws -> Data { try JSONSerialization.data(withJSONObject: body) }
    private func decode(_ body: [String: Any]) throws -> RecordingUploadPlan { try JSONDecoder().decode(RecordingUploadPlan.self, from: data(body)) }
}

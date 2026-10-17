import XCTest
@testable import OmiCore

final class LocalRecordingStoreTests: XCTestCase {
    private func folder() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testUnfinishedAndCompletedRecordingsSurviveRestartWithOwnershipAndOriginalTimes() throws {
        let directory = try folder()
        let store = try LocalRecordingStore(directory: directory)
        let start = Date(timeIntervalSince1970: 1000.125)
        let interrupted = try store.create(archiveIdentity: "first", startedAt: start)
        let completed = try store.create(archiveIdentity: "first", startedAt: start.addingTimeInterval(60))
        let other = try store.create(archiveIdentity: "second", startedAt: start)
        let audio = Data([1, 2, 3, 4])
        try audio.write(to: store.fileURL(for: interrupted))
        try audio.write(to: store.fileURL(for: completed))
        let finished = try store.finish(id: completed.id, endedAt: start.addingTimeInterval(120))
        XCTAssertEqual(try store.finish(id: completed.id, endedAt: start.addingTimeInterval(180)), finished)

        let restored = try LocalRecordingStore(directory: directory)
        XCTAssertEqual(try restored.recordings(for: "first"), [finished, interrupted])
        XCTAssertNil(try restored.recordings(for: "first").last?.endedAt)
        XCTAssertEqual(try restored.recordings(for: "second"), [other])
        XCTAssertEqual(try Data(contentsOf: restored.fileURL(for: interrupted)), audio)
        XCTAssertEqual(restored.fileURL(for: completed).lastPathComponent, "recording.m4a")
    }

    func testRemovalDeletesOnlyChosenRecordingAndIsSafeToRetry() throws {
        let store = try LocalRecordingStore(directory: folder())
        let removed = try store.create(archiveIdentity: "first")
        let kept = try store.create(archiveIdentity: "first")
        try Data([1]).write(to: store.fileURL(for: removed))
        try Data([2]).write(to: store.fileURL(for: kept))
        try store.remove(id: removed.id)
        try store.remove(id: removed.id)
        XCTAssertEqual(try store.recordings(for: "first"), [kept])
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.fileURL(for: removed).deletingLastPathComponent().path))
        XCTAssertEqual(try Data(contentsOf: store.fileURL(for: kept)), Data([2]))
    }

    func testCorruptOrMissingMetadataCannotHideOrDeleteOtherRecoverableAudio() throws {
        let directory = try folder(), store = try LocalRecordingStore(directory: directory)
        let good = try store.create(archiveIdentity: "first")
        let corrupt = try store.create(archiveIdentity: "first")
        let missing = try store.create(archiveIdentity: "first")
        let corruptMetadata = store.fileURL(for: corrupt).deletingLastPathComponent().appendingPathComponent("metadata.json")
        let missingMetadata = store.fileURL(for: missing).deletingLastPathComponent().appendingPathComponent("metadata.json")
        try Data("broken".utf8).write(to: corruptMetadata)
        try Data([7, 8]).write(to: store.fileURL(for: corrupt))
        try FileManager.default.removeItem(at: missingMetadata)
        XCTAssertEqual(try store.recordings(for: "first"), [good])
        XCTAssertEqual(store.loadingIssues.count, 2)
        XCTAssertEqual(try Data(contentsOf: corruptMetadata), Data("broken".utf8))
        XCTAssertEqual(try Data(contentsOf: store.fileURL(for: corrupt)), Data([7, 8]))
        XCTAssertTrue(FileManager.default.fileExists(atPath: missingMetadata.deletingLastPathComponent().path))
    }

    func testMetadataCannotRedirectARecordingToAnotherIdentityOrFile() throws {
        let directory = try folder(), store = try LocalRecordingStore(directory: directory)
        let original = try store.create(archiveIdentity: "first")
        let metadata = store.fileURL(for: original).deletingLastPathComponent().appendingPathComponent("metadata.json")
        let mismatched = LocalRecordingDraft(id: UUID(), archiveIdentity: "first", startedAt: original.startedAt)
        let encoder = JSONEncoder()
        try encoder.encode(mismatched).write(to: metadata)
        XCTAssertTrue(try store.recordings(for: "first").isEmpty)
        XCTAssertEqual(store.loadingIssues.count, 1)
        XCTAssertThrowsError(try store.finish(id: original.id))
        XCTAssertTrue(FileManager.default.fileExists(atPath: metadata.path))
    }

    func testFailedFinalizationPreservesUnfinishedDraftAndAudioForRetry() throws {
        let directory = try folder(), store = try LocalRecordingStore(directory: directory)
        let start = Date(timeIntervalSince1970: 1000)
        let draft = try store.create(archiveIdentity: "first", startedAt: start)
        let metadata = store.fileURL(for: draft).deletingLastPathComponent().appendingPathComponent("metadata.json")
        let saved = try Data(contentsOf: metadata)
        try Data([1, 2]).write(to: store.fileURL(for: draft))
        try FileManager.default.removeItem(at: metadata)
        try FileManager.default.createDirectory(at: metadata, withIntermediateDirectories: false)
        XCTAssertThrowsError(try store.finish(id: draft.id, endedAt: start.addingTimeInterval(2)))
        try FileManager.default.removeItem(at: metadata)
        try saved.write(to: metadata)
        XCTAssertEqual(try store.recordings(for: "first"), [draft])
        let finished = try store.finish(id: draft.id, endedAt: start.addingTimeInterval(2))
        XCTAssertEqual(finished.id, draft.id)
        XCTAssertEqual(finished.endedAt, start.addingTimeInterval(2))
        XCTAssertEqual(try Data(contentsOf: store.fileURL(for: draft)), Data([1, 2]))
    }

    func testInvalidCompletionDoesNotRewriteSavedCaptureTime() throws {
        let store = try LocalRecordingStore(directory: folder())
        let start = Date(timeIntervalSince1970: 1000)
        let draft = try store.create(archiveIdentity: "first", startedAt: start)
        XCTAssertThrowsError(try store.finish(id: draft.id, endedAt: start.addingTimeInterval(-1)))
        XCTAssertEqual(try store.recordings(for: "first"), [draft])
        XCTAssertThrowsError(try store.create(archiveIdentity: "", startedAt: start))
    }

    func testSymbolicLinkCannotExposeOrRemoveOutsideRecording() throws {
        let directory = try folder(), outside = try folder()
        let store = try LocalRecordingStore(directory: directory)
        let id = UUID(), link = directory.appendingPathComponent(id.uuidString)
        let sentinel = outside.appendingPathComponent("recording.m4a")
        try Data([9]).write(to: sentinel)
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: outside)
        XCTAssertTrue(try store.recordings(for: "first").isEmpty)
        XCTAssertEqual(store.loadingIssues.count, 1)
        XCTAssertThrowsError(try store.remove(id: id))
        XCTAssertEqual(try Data(contentsOf: sentinel), Data([9]))
    }
}

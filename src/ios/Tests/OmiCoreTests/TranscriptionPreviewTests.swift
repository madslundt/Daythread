import XCTest
@testable import OmiCore

final class TranscriptionPreviewTests: XCTestCase {
    func testSnapshotCopiesBoundedCurrentAudioWithoutClosingOrConsumingCapture() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try DurableStore(directory: directory)
        store.profileID = "preview"
        store.destinationID = "test-destination"
        XCTAssertNil(store.transcriptionPreview())
        try store.begin(deviceID: "omi", codec: 0, clock: .init(wall: Date(), uptime: 0))
        let samples = Data(repeating: 37, count: 20 * 32000)
        try store.append(frame: Data([1, 2, 3]), decoded: samples)
        let items = store.items
        let bytes = store.storageBytes
        let preview = try XCTUnwrap(store.transcriptionPreview())
        XCTAssertEqual(preview.count, 15 * 32000 + 44)
        XCTAssertEqual(Data(preview.dropFirst(44)), samples.suffix(15 * 32000))
        XCTAssertTrue(store.isRecording)
        XCTAssertEqual(store.segmentSeconds, 20)
        XCTAssertEqual(store.storageBytes, bytes)
        XCTAssertEqual(store.items.map(\.id), items.map(\.id))
        XCTAssertEqual(store.pending.count, 0)
        store.profileID = "different"
        XCTAssertNil(store.transcriptionPreview(), "Never expose another profile's current audio")
        store.profileID = "preview"
        try store.finish()
        XCTAssertNil(store.transcriptionPreview())
        XCTAssertEqual(store.pending.count, 1)
        let saved = try Data(contentsOf: store.uploadFile(for: store.pending[0]))
        XCTAssertEqual(saved.count, samples.count + 44, "The complete original remains authoritative")
    }
}

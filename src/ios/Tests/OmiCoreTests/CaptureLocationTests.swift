import Foundation
import XCTest
@testable import OmiCore

final class CaptureLocationTests: XCTestCase {
    func testRejectsInvalidCoordinatesAndAccuracy() {
        let now = Date()
        for values in [(91.0, 0.0, 5.0), (0, -181, 5), (.nan, 0, 5), (0, .infinity, 5), (0, 0, -1), (0, 0, 1001)] {
            XCTAssertNil(CaptureLocation(latitude: values.0, longitude: values.1, horizontalAccuracy: values.2, capturedAt: now))
        }
    }

    func testFixAgeUsesCaptureTimeAndNeverUploadTime() throws {
        let captured = Date(timeIntervalSince1970: 1000)
        let fix = try XCTUnwrap(CaptureLocation(latitude: 55.6761, longitude: 12.5683, horizontalAccuracy: 35, capturedAt: captured))
        XCTAssertTrue(fix.isUsable(at: captured.addingTimeInterval(120)))
        XCTAssertFalse(fix.isUsable(at: captured.addingTimeInterval(120.001)))
        XCTAssertTrue(fix.isUsable(at: captured.addingTimeInterval(-5)))
        XCTAssertFalse(fix.isUsable(at: captured.addingTimeInterval(-5.001)))
        XCTAssertEqual(fix.uploadQuery["location_captured_at"], DurableStore.stamp(captured))
    }

    func testOutboxKeepsOriginalObservationAcrossRestartAndRetry() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let captured = Date(timeIntervalSince1970: 1000.125)
        let fix = try XCTUnwrap(CaptureLocation(latitude: 55.6761, longitude: 12.5683, horizontalAccuracy: 35, capturedAt: captured.addingTimeInterval(-20)))
        let store = try DurableStore(directory: directory)
        try store.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: captured), location: fix)
        try store.append(frame: Data(count: 640), decoded: Data(count: 640))
        try store.finish()
        let original = try XCTUnwrap(store.pending.first)
        try store.failed(original.id, message: "Offline")
        let restored = try DurableStore(directory: directory)
        XCTAssertEqual(restored.pending.first?.id, original.id)
        XCTAssertEqual(restored.pending.first?.location, fix)
        XCTAssertEqual(restored.pending.first?.location?.uploadQuery, fix.uploadQuery)
        try restored.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: captured.addingTimeInterval(300)), location: fix)
        XCTAssertNil(restored.items.last?.location)
    }
}

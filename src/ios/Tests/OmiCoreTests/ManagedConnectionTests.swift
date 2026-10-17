import XCTest
@testable import OmiCore

final class ManagedConnectionTests: XCTestCase {
    func testManagedAuthorizationRequiresTheExactHTTPSOrigin() throws {
        let service = try XCTUnwrap(ManagedServiceOrigin("https://daythread.elcc.dk"))
        XCTAssertTrue(service.contains(URL(string: "https://daythread.elcc.dk/v1/chat")!))
        XCTAssertTrue(service.contains(URL(string: "https://DAYTHREAD.elcc.dk:443/v1/audio")!))
        for address in ["http://daythread.elcc.dk/v1/chat", "https://daythread.elcc.dk.evil.example/v1/chat",
                        "https://daythread.elcc.dk:8443/v1/chat", "https://other.example", "https://user@daythread.elcc.dk"] {
            XCTAssertFalse(service.contains(URL(string: address)!), address)
        }
        for address in ["", "http://daythread.elcc.dk", "https://example.com/account", "https://user:secret@example.com", "https://example.com?key=x"] {
            XCTAssertNil(ManagedServiceOrigin(address), address)
        }
    }
    func testAccountsAtTheSameOriginHaveDifferentDestinations() throws {
        let service = try XCTUnwrap(ManagedServiceOrigin("https://daythread.elcc.dk"))
        XCTAssertNotEqual(service.destination(accountID: "a"), service.destination(accountID: "b"))
        XCTAssertNotEqual(service.destination(accountID: "a"), ManagedServiceOrigin.ownDestination(archiveIdentity: service.url.absoluteString))
    }
    func testPendingAndLocalPlaybackRemainWithTheOriginalDestinationAfterSwitchAndRestart() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try DurableStore(directory: directory)
        let date = Date(timeIntervalSince1970: 1000)
        store.destinationID = "own:original"
        try store.begin(deviceID: "pendant", codec: 0, clock: CaptureClock(wall: date))
        try store.append(frame: Data(count: 640), decoded: Data(count: 640))
        // Even an in-flight segment retains its original destination until finished.
        store.destinationID = "managed:account-a"
        try store.finish()
        let managed = try store.event(deviceID: "pendant", at: date, action: .mark)
        let restored = try DurableStore(directory: directory)
        XCTAssertEqual(restored.pending(destinationID: "own:original").count, 1)
        XCTAssertEqual(restored.pending(destinationID: "managed:account-a").map(\.id), [managed])
        XCTAssertTrue(restored.pending(destinationID: "managed:account-b").isEmpty)
        restored.destinationID = "managed:account-a"
        XCTAssertThrowsError(try restored.rewind(minutes: 1, end: date.addingTimeInterval(60)))
        restored.destinationID = "own:original"
        let rewind = try restored.rewind(minutes: 1, end: date.addingTimeInterval(60))
        try FileManager.default.removeItem(at: rewind.url)
    }
    func testLegacyBindingNeverReassignsAnAlreadyOwnedItem() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try DurableStore(directory: directory)
        let old = try store.event(deviceID: "pendant", at: Date(), action: .mark)
        try store.bindLegacyDestination("own:original")
        try store.bindLegacyDestination("managed:account-a")
        let reopened = try DurableStore(directory: directory)
        XCTAssertEqual(reopened.pending.first { $0.id == old }?.destinationID, "own:original")
    }
}

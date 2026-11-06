import XCTest
import SwiftUI
import Network
@testable import Daythread

@MainActor
final class DayRefreshTests: XCTestCase {
    func testEmptyActionPointsDoNotInsertRowsDuringBackgroundRefresh() async throws {
        let defaults = UserDefaults.standard
        let keys = ["backendURL", "backendEndpoints.v1", "selectedProfileID", "capturePaused"]
        let saved = keys.map { defaults.object(forKey: $0) }
        let server = try DayRefreshServer()
        server.start()
        try await waitUntil { server.port != nil }
        defaults.set("http://127.0.0.1:\(try XCTUnwrap(server.port))", forKey: "backendURL")
        defaults.removeObject(forKey: "backendEndpoints.v1")
        defaults.set("default", forKey: "selectedProfileID")
        defaults.set(true, forKey: "capturePaused")
        defer {
            server.stop()
            for (key, value) in zip(keys, saved) {
                if let value { defaults.set(value, forKey: key) }
                else { defaults.removeObject(forKey: key) }
            }
        }

        let model = AppModel()
        let controller = UIHostingController(rootView: List {
            DayAtGlanceSections(day: "2026-11-02", compact: true)
        }.environment(model).environment(\.scenePhase, .active))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 402, height: 874))
        window.rootViewController = controller
        window.isHidden = false
        defer { window.isHidden = true; window.rootViewController = nil }
        controller.view.layoutIfNeeded()

        try await waitUntil { server.inboxRequests >= 2 }
        controller.view.layoutIfNeeded()
        let list = try XCTUnwrap(findCollection(in: controller.view))
        let loadingRows = rowCount(list)
        try await Task.sleep(for: .seconds(1))
        controller.view.layoutIfNeeded()
        list.layoutIfNeeded()
        let initialRows = rowCount(list)
        let initialHeight = list.contentSize.height
        let initialRequests = server.inboxRequests
        XCTAssertGreaterThan(initialRows, 0)
        XCTAssertEqual(loadingRows, initialRows + 1, "The initial load should still show its loading row")

        // Exercise the real 15-second polling task with identical empty responses.
        try await waitUntil(timeout: 20) { server.inboxRequests >= initialRequests + 2 }
        try await Task.sleep(for: .milliseconds(200))
        controller.view.layoutIfNeeded()
        list.layoutIfNeeded()
        XCTAssertEqual(rowCount(list), initialRows, "Polling an already loaded empty day must not insert a loading row")
        XCTAssertEqual(list.contentSize.height, initialHeight, accuracy: 1, "Background refresh must not shift the day list")
    }

    private func rowCount(_ list: UICollectionView) -> Int {
        (0..<list.numberOfSections).reduce(0) { $0 + list.numberOfItems(inSection: $1) }
    }

    private func findCollection(in view: UIView) -> UICollectionView? {
        if let list = view as? UICollectionView { return list }
        return view.subviews.lazy.compactMap { self.findCollection(in: $0) }.first
    }

    private func waitUntil(timeout: TimeInterval = 5, _ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition(), Date() < deadline { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertTrue(condition(), "Timed out waiting for day refresh")
        if !condition() { throw TestFailure.timedOut }
    }

    private enum TestFailure: Error { case timedOut }
}

private final class DayRefreshServer {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "day-refresh-test")
    private let lock = NSLock()
    private var requests = 0
    var inboxRequests: Int { lock.withLock { requests } }
    var port: UInt16? { listener.port.flatMap { $0.rawValue == 0 ? nil : $0.rawValue } }
    init() throws { listener = try NWListener(using: .tcp, on: .any) }
    func start() {
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { return }
            connection.start(queue: self.queue)
            connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { data, _, _, _ in
                let request = String(data: data ?? Data(), encoding: .utf8) ?? ""
                let inbox = request.hasPrefix("GET /v1/inbox?")
                if inbox { self.lock.withLock { self.requests += 1 } }
                let body = inbox
                    ? #"{"items":[],"total":0,"pending":0,"coverage":{"jobs":{},"unscanned_sources":0},"recent_jobs":[]}"#
                    : #"{"items":[],"total":0}"#
                let response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n" + body
                self.queue.asyncAfter(deadline: .now() + 0.7) {
                    connection.send(content: Data(response.utf8), completion: .contentProcessed { _ in connection.cancel() })
                }
            }
        }
        listener.start(queue: queue)
    }
    func stop() { listener.cancel() }
}

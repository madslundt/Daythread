import XCTest
@testable import OmiCore

final class BackendEndpointsTests: XCTestCase {
    func testConfigurationRoundTripsAndPreservesActiveEndpoint() throws {
        let first = BackendEndpoint(address: "https://home.example")
        let second = BackendEndpoint(address: "https://tail.example")
        let configuration = BackendEndpointConfiguration(endpoints: [first, second], activeID: second.id, archiveIdentity: "home-archive")

        let data = try JSONEncoder().encode(configuration)
        let restored = try JSONDecoder().decode(BackendEndpointConfiguration.self, from: data)

        XCTAssertEqual(restored, configuration)
        XCTAssertEqual(restored.activeEndpoint?.address, "https://tail.example")
        XCTAssertEqual(restored.archiveIdentity, "home-archive")
    }

    func testCandidatesStartAtActiveThenFollowConfiguredOrder() {
        let first = BackendEndpoint(address: "https://home.example")
        let second = BackendEndpoint(address: "https://tail.example")
        let third = BackendEndpoint(address: "https://away.example")
        let router = BackendEndpointRouter(configuration: .init(endpoints: [first, second, third], activeID: second.id))

        XCTAssertEqual(router.orderedCandidates().map(\.id), [second.id, first.id, third.id])
        XCTAssertEqual(router.endpoint(after: second.id)?.id, first.id)
        XCTAssertNil(router.endpoint(after: first.id, excluding: [third.id]))
    }

    func testSuccessfulFallbackBecomesActiveForRecovery() {
        let first = BackendEndpoint(address: "https://home.example")
        let second = BackendEndpoint(address: "https://tail.example")
        let router = BackendEndpointRouter(configuration: .init(endpoints: [first, second], activeID: first.id))
        var changes = 0
        router.onChange = { _ in changes += 1 }

        router.markSuccess(second.id)

        XCTAssertEqual(router.activeEndpoint?.id, second.id)
        XCTAssertEqual(router.orderedCandidates().map(\.id), [second.id, first.id])
        XCTAssertEqual(changes, 1)
    }

    func testChangingEndpointListChangesUploadDestinationOrderWithoutChangingIDs() {
        let home = BackendEndpoint(address: "http://192.168.1.20:8787")
        let tail = BackendEndpoint(address: "https://daythread.tailnet.ts.net")
        var configuration = BackendEndpointConfiguration(endpoints: [home, tail], activeID: home.id)
        let router = BackendEndpointRouter(configuration: configuration)

        configuration.move(fromOffsets: IndexSet(integer: 1), toOffset: 0)
        router.replace(configuration)
        router.setActive(tail.id)

        XCTAssertEqual(router.orderedCandidates().map(\.address), [tail.address, home.address])
        XCTAssertEqual(router.orderedCandidates().map(\.id), [tail.id, home.id])
    }
}

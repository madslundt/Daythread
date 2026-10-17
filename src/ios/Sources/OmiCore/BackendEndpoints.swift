import Foundation

public struct BackendEndpoint: Codable, Equatable, Hashable, Identifiable {
    public let id: UUID
    public var address: String

    public init(id: UUID = UUID(), address: String) {
        self.id = id
        self.address = address
    }
}

public struct BackendEndpointConfiguration: Codable, Equatable {
    public var endpoints: [BackendEndpoint]
    public var activeID: UUID?
    public var archiveIdentity: String

    public init(endpoints: [BackendEndpoint], activeID: UUID? = nil, archiveIdentity: String? = nil) {
        self.endpoints = endpoints
        self.activeID = activeID ?? endpoints.first?.id
        self.archiveIdentity = archiveIdentity ?? endpoints.first?.address ?? ""
    }

    public var activeEndpoint: BackendEndpoint? {
        endpoints.first { $0.id == activeID } ?? endpoints.first
    }

    public mutating func setActive(_ id: UUID) {
        guard endpoints.contains(where: { $0.id == id }) else { return }
        activeID = id
    }

    public mutating func replaceEndpoints(_ endpoints: [BackendEndpoint], activeID: UUID? = nil) {
        self.endpoints = endpoints
        self.activeID = activeID ?? self.activeID.flatMap { id in endpoints.contains(where: { $0.id == id }) ? id : nil } ?? endpoints.first?.id
    }

    public mutating func move(fromOffsets offsets: IndexSet, toOffset destination: Int) {
        let moved = offsets.map { endpoints[$0] }
        for index in offsets.sorted(by: >) { endpoints.remove(at: index) }
        let adjustedDestination = destination - offsets.filter { $0 < destination }.count
        endpoints.insert(contentsOf: moved, at: max(0, min(adjustedDestination, endpoints.count)))
    }

    public mutating func remove(id: UUID) {
        endpoints.removeAll { $0.id == id }
        if activeID == id { activeID = endpoints.first?.id }
    }
}

/// Shared endpoint state for foreground requests and background uploads.
/// The router only changes which URL is used; URLSession continues to perform
/// its normal certificate and ATS validation for every request.
public final class BackendEndpointRouter {
    public private(set) var configuration: BackendEndpointConfiguration
    public var onChange: ((BackendEndpointConfiguration) -> Void)?

    public init(configuration: BackendEndpointConfiguration) {
        self.configuration = configuration
    }

    public var activeEndpoint: BackendEndpoint? { configuration.activeEndpoint }

    public func orderedCandidates() -> [BackendEndpoint] {
        guard let active = configuration.activeEndpoint else { return configuration.endpoints }
        return [active] + configuration.endpoints.filter { $0.id != active.id }
    }

    public func endpoint(after id: UUID, excluding: Set<UUID> = []) -> BackendEndpoint? {
        let candidates = orderedCandidates()
        guard let index = candidates.firstIndex(where: { $0.id == id }) else { return nil }
        return candidates.dropFirst(index + 1).first { !excluding.contains($0.id) }
    }

    public func markSuccess(_ id: UUID) {
        guard configuration.activeID != id else { return }
        configuration.setActive(id)
        notifyChange()
    }

    public func setActive(_ id: UUID) {
        let old = configuration.activeID
        configuration.setActive(id)
        if old != configuration.activeID { notifyChange() }
    }

    public func replace(_ configuration: BackendEndpointConfiguration) {
        self.configuration = configuration
        notifyChange()
    }

    private func notifyChange() {
        onChange?(configuration)
    }
}

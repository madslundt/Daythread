import Foundation

/// One phone observation near the start of a chunk; never a spoken destination.
public struct CaptureLocation: Codable, Equatable {
    public let latitude: Double
    public let longitude: Double
    public let horizontalAccuracy: Double
    public let capturedAt: Date

    public init?(latitude: Double, longitude: Double, horizontalAccuracy: Double, capturedAt: Date) {
        guard latitude.isFinite, longitude.isFinite, horizontalAccuracy.isFinite,
              (-90...90).contains(latitude), (-180...180).contains(longitude),
              (0...1000).contains(horizontalAccuracy) else { return nil }
        self.latitude = latitude; self.longitude = longitude
        self.horizontalAccuracy = horizontalAccuracy; self.capturedAt = capturedAt
    }

    public func isUsable(at date: Date) -> Bool {
        (-5...120).contains(date.timeIntervalSince(capturedAt))
    }

    public var uploadQuery: [String: String] {
        ["latitude": String(latitude), "longitude": String(longitude),
         "horizontal_accuracy": String(horizontalAccuracy),
         "location_captured_at": DurableStore.stamp(capturedAt)]
    }
}

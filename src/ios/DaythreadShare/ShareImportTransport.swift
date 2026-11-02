import UIKit
import CoreLocation
import OmiCore

extension ShareViewController {
    func send(_ path: String, method: String, configuration: BackendEndpointConfiguration, body: Data? = nil) async throws -> Data {
        try Task.checkCancellation()
        guard let endpoint = configuration.activeEndpoint, let address = URL(string: endpoint.address) else {
            throw ShareImportError("Daythread’s archive address is unavailable. Open the app and try again.")
        }
        var request = URLRequest(url: address.appendingPathComponent(path))
        request.httpMethod = method; request.httpBody = body; request.timeoutInterval = 180
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(profileID, forHTTPHeaderField: "X-Omi-Profile")
        request.setValue(ManagedServiceOrigin.ownDestination(archiveIdentity: configuration.archiveIdentity), forHTTPHeaderField: "X-Daythread-Destination")
        return try await checkedData(for: request)
    }

    func checkedData(for request: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: request)
        try Task.checkCancellation()
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw ShareImportError(Self.message(from: data)) }
        return data
    }

    static func message(from data: Data) -> String {
        (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String ?? "Daythread couldn’t import this recording. Open the app to retry."
    }

}

struct ShareImportError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct ShareTag: Decodable, Identifiable {
    let id: String
    let name: String
}
struct ShareTagDirectory: Decodable { let items: [ShareTag] }
struct SharePlace: Decodable, Identifiable {
    let id: String
    let label: String
}
struct ShareZone: Decodable, Identifiable {
    let id: String
    let name: String
    let locations: [SharePlace]
}
struct ShareZoneDirectory: Decodable { let items: [ShareZone] }

final class ShareLocationReader: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private var continuation: CheckedContinuation<CLLocation, Error>?

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    func currentLocation() async throws -> CLLocation {
        guard continuation == nil else { throw ShareImportError("A location request is already running.") }
        return try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            switch manager.authorizationStatus {
            case .authorizedAlways, .authorizedWhenInUse: manager.requestLocation()
            case .notDetermined: manager.requestWhenInUseAuthorization()
            default: finish(.failure(ShareImportError("Allow Daythread to use your location, or choose a saved place.")))
            }
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        guard continuation != nil else { return }
        if [.authorizedAlways, .authorizedWhenInUse].contains(manager.authorizationStatus) { manager.requestLocation() }
        else if manager.authorizationStatus != .notDetermined {
            finish(.failure(ShareImportError("Allow Daythread to use your location, or choose a saved place.")))
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last,
              location.horizontalAccuracy >= 0, location.horizontalAccuracy <= 1000,
              abs(location.timestamp.timeIntervalSinceNow) < 120 else {
            finish(.failure(ShareImportError("Current location is unavailable. Choose a saved place instead.")))
            return
        }
        finish(.success(location))
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) { finish(.failure(error)) }

    private func finish(_ result: Result<CLLocation, Error>) {
        let pending = continuation
        continuation = nil
        pending?.resume(with: result)
    }
}

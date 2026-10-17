import CoreLocation
import Observation
import OmiCore

@MainActor @Observable
final class PhoneLocation: NSObject, @preconcurrency CLLocationManagerDelegate {
    private(set) var latest: CaptureLocation?
    private(set) var error: String?
    private(set) var authorized = false
    @ObservationIgnored private let manager = CLLocationManager()
    @ObservationIgnored private var captureActive = false
    @ObservationIgnored private var running = false
    @ObservationIgnored private var pending: CheckedContinuation<CaptureLocation, Error>?
    @ObservationIgnored private var timeout: Task<Void, Never>?

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        manager.distanceFilter = 100
        manager.activityType = .other
        manager.pausesLocationUpdatesAutomatically = false
        manager.showsBackgroundLocationIndicator = true
        authorized = [.authorizedWhenInUse, .authorizedAlways].contains(manager.authorizationStatus)
    }

    func requestPermission() {
        if manager.authorizationStatus == .notDetermined { manager.requestWhenInUseAuthorization() }
        else { updateAuthorization() }
    }

    func setCaptureActive(_ active: Bool) {
        captureActive = active
        reconcile()
        if !active { latest = nil }
    }

    private func reconcile() {
        let shouldRun = captureActive && authorized
        guard shouldRun != running else { return }
        running = shouldRun
        if shouldRun {
            manager.allowsBackgroundLocationUpdates = true
            manager.startUpdatingLocation()
        } else {
            manager.stopUpdatingLocation()
            manager.allowsBackgroundLocationUpdates = false
        }
    }

    func currentLocation() async throws -> CaptureLocation {
        guard pending == nil else { throw BackendFailure(message: String(localized: "Location is already being requested.")) }
        return try await withCheckedThrowingContinuation { continuation in
            pending = continuation; error = nil
            timeout = Task { [weak self] in
                try? await Task.sleep(for: .seconds(30))
                guard !Task.isCancelled else { return }
                self?.complete(.failure(BackendFailure(message: String(localized: "Location is unavailable. Search for a place instead."))))
            }
            if manager.authorizationStatus == .notDetermined { requestPermission() }
            else if authorized { manager.requestLocation() }
            else { updateAuthorization() }
        }
    }

    func cancelRequest() {
        complete(.failure(CancellationError()))
    }

    private func complete(_ result: Result<CaptureLocation, Error>) {
        timeout?.cancel(); timeout = nil
        let continuation = pending; pending = nil
        continuation?.resume(with: result)
    }

    private func updateAuthorization() {
        authorized = [.authorizedWhenInUse, .authorizedAlways].contains(manager.authorizationStatus)
        if authorized {
            error = nil
            if pending != nil { manager.requestLocation() }
        } else if manager.authorizationStatus != .notDetermined {
            latest = nil
            error = String(localized: "Location access is off. Enable it in iPhone Settings or search for a place.")
            complete(.failure(BackendFailure(message: error!)))
        }
        reconcile()
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) { updateAuthorization() }
    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let fix = locations.last,
              let observation = CaptureLocation(latitude: fix.coordinate.latitude, longitude: fix.coordinate.longitude,
                                                horizontalAccuracy: fix.horizontalAccuracy, capturedAt: fix.timestamp),
              observation.isUsable(at: Date()) else { return }
        latest = observation; error = nil
        complete(.success(observation))
    }
    func locationManager(_ manager: CLLocationManager, didFailWithError failure: Error) {
        error = String(localized: "Location is unavailable. Search for a place instead.")
        complete(.failure(BackendFailure(message: error!)))
    }
}

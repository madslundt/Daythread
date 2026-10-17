import Foundation
import OmiCore

extension BackendClient {
    @MainActor
    func uploadRecording(_ file: URL, id: UUID, query: [String: String], wiFiOnly: Bool,
                         progress: @escaping @MainActor @Sendable (Int64) -> Void) async throws -> Data {
        guard !profileID.isEmpty else { throw BackendFailure(message: L10n.source("Choose a profile in Settings first.")) }
        let candidates = router.orderedCandidates()
        var lastError: Error?
        for (index, endpoint) in candidates.enumerated() {
            try Task.checkCancellation()
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForResource = 300
            configuration.urlCache = nil
            let session = URLSession(configuration: configuration, delegate: authorization == nil ? nil : ManagedRedirectPolicy.shared, delegateQueue: nil)
            defer { session.invalidateAndCancel() }
            do {
                let url = RecordingImport.uploadURL(base: try Self.validatedURL(endpoint.address), id: id, query: query)
                var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData)
                request.httpMethod = "PUT"
                request.timeoutInterval = 180
                request.allowsCellularAccess = !wiFiOnly
                request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
                request.setValue(profileID, forHTTPHeaderField: "X-Omi-Profile")
                try authorize(&request)
                progress(0)
                let delegate = RecordingTransferProgress(progress: progress)
                let (data, response) = try await session.upload(for: request, fromFile: file, delegate: delegate)
                try Task.checkCancellation()
                try validateResponse(response, data: data)
                router.markSuccess(endpoint.id)
                return data
            } catch {
                lastError = error
                guard index < candidates.count - 1, Self.shouldTryNextEndpoint(error) else { throw error }
            }
        }
        throw lastError ?? BackendFailure(message: L10n.source("The recording could not be uploaded."))
    }
}

private final class RecordingTransferProgress: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private let progress: @MainActor @Sendable (Int64) -> Void
    init(progress: @escaping @MainActor @Sendable (Int64) -> Void) { self.progress = progress }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        Task { @MainActor in progress(totalBytesSent) }
    }
}

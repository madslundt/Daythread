import Foundation
import Observation
import OmiCore

/// One bounded, user-requested copy. The pendant's read frontier is never advanced.
@MainActor @Observable
final class OmiStorageRecovery {
    private(set) var supported = false
    private(set) var busy = false
    private(set) var info: OmiRingInfo?
    private(set) var message: String?
    private(set) var faulted = false
    @ObservationIgnored private var write: ((Data) -> Void)?
    @ObservationIgnored private var read: (() -> Void)?
    @ObservationIgnored private var pending: CheckedContinuation<Data, Error>?
    @ObservationIgnored private var expected: Response?
    @ObservationIgnored private var timeout: Task<Void, Never>?
    @ObservationIgnored private var transfer: OmiRingTransfer?
    @ObservationIgnored private var session = UUID()
    private enum Response { case info, status, custody, transfer }

    func configure(available: Bool, write: @escaping (Data) -> Void, read: @escaping () -> Void) {
        supported = available && !faulted
        self.write = write; self.read = read
    }
    func disconnect() {
        session = UUID()
        finish(.failure(OmiRingError.changed))
        supported = false; info = nil; busy = false; faulted = false
        write = nil; read = nil; transfer = nil
    }
    func cancel() {
        guard busy else { return }
        // A timed out/abandoned ACK cannot safely be assigned to a later request.
        session = UUID(); faulted = true; supported = false
        finish(.failure(OmiRingError.changed))
    }
    func transportFailed() {
        faulted = true; supported = false
        finish(.failure(OmiRingError.changed))
    }
    func receiveStatus(_ data: Data) {
        guard expected == .status else { return }
        finish(.success(data))
    }
    func receiveControl(_ data: Data) {
        guard let first = data.first else { return }
        if expected == .transfer {
            do {
                try transfer?.receive(data)
                if transfer?.complete == true { finish(.success(data)) }
            } catch { transportFailed() }
        } else if (expected == .info && first == 2) || (expected == .custody && first == 1) {
            finish(.success(data))
        }
    }
    private func finish(_ result: Result<Data, Error>) {
        timeout?.cancel(); timeout = nil
        let waiter = pending; pending = nil; expected = nil
        waiter?.resume(with: result)
    }
    private func request(_ response: Response, command: Data? = nil) async throws -> Data {
        guard supported, !faulted, pending == nil, write != nil, read != nil else { throw OmiRingError.unsupported }
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                expected = response; pending = continuation
                timeout = Task { [weak self] in
                    do { try await Task.sleep(for: .seconds(30)) } catch { return }
                    self?.transportFailed()
                }
                if let command { write?(command) } else { read?() }
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.cancel() }
        }
    }
    private func probe() async throws -> OmiRingInfo {
        let status = try await request(.status)
        guard OmiRingProtocol.rtcValid(status) else { throw OmiRingError.invalidClock }
        return try OmiRingInfo(await request(.info, command: OmiRingProtocol.infoCommand))
    }
    func check() async {
        guard supported, !busy else { return }
        busy = true; message = L10n.source("Checking pendant storage…")
        defer { busy = false }
        do { info = try await probe(); message = nil }
        catch { show(error) }
    }
    func copy(deviceID: String, codec: Int, store: DurableStore) async {
        guard supported, !busy else { return }
        busy = true; message = L10n.source("Copying pendant recordings…")
        defer { busy = false; transfer = nil }
        let destination = store.destinationID, profile = store.profileID, session = self.session
        do {
            let before = try await probe()
            let ack = try await request(.custody, command: OmiRingProtocol.custodyCommand)
            guard OmiRingProtocol.custodyAccepted(ack) else { throw OmiRingError.unsupported }
            let fresh = try OmiRingInfo(await request(.info, command: OmiRingProtocol.infoCommand))
            guard before.ringID == fresh.ringID else { throw OmiRingError.changed }
            info = fresh
            let local = try store.ringRecoveryCursor(deviceID: deviceID, ringID: fresh.ringID)
            var start = max(fresh.readSequence, local ?? fresh.readSequence)
            guard start <= fresh.writeSequence else { throw OmiRingError.changed }
            guard start < fresh.writeSequence else { message = L10n.source("No new pendant recordings"); return }
            let targetWrite = fresh.writeSequence
            while start < targetWrite {
                let count = Int(min(UInt64(OmiRingProtocol.maximumBatch), targetWrite - start))
                transfer = try OmiRingTransfer(start: start, count: count)
                _ = try await request(.transfer, command: OmiRingProtocol.readCommand(start: start, count: count))
                let confirmed = try OmiRingInfo(await request(.info, command: OmiRingProtocol.infoCommand))
                guard confirmed.ringID == fresh.ringID, confirmed.readSequence <= start,
                      confirmed.writeSequence >= start + UInt64(count),
                      session == self.session, supported, !Task.isCancelled,
                      store.destinationID == destination, store.profileID == profile,
                      let transfer else { throw OmiRingError.changed }
                let records = try transfer.records()
                _ = try store.copyRingRecords(deviceID: deviceID, ringID: fresh.ringID, records: records, codec: codec)
                start += UInt64(count); info = confirmed
                await Task.yield()
            }
            // No ADVANCE/ADVANCE_ID/CLEAR command is ever sent. Recovered bytes
            // become ordinary durable uploads, and device copies remain intact.
            message = L10n.source("Pendant copies saved to the upload queue")
        } catch { show(error) }
    }
    private func show(_ error: Error) {
        if case OmiRingError.invalidClock = error {
            message = L10n.source("The pendant clock is unavailable. Original recordings remain on the pendant.")
        } else if case OmiRingError.unsupported = error {
            faulted = true; supported = false
            message = L10n.source("Safe storage recovery is unavailable on this firmware.")
        } else if case OmiRingError.overlap = error {
            message = L10n.source("Pendant audio overlaps a local recording. Original recordings remain on the pendant.")
        } else if case OmiRingError.changed = error {
            message = L10n.source("Pendant storage changed. Reconnect before trying again.")
        } else {
            message = L10n.source("Could not copy pendant recordings. Original recordings remain on the pendant.")
        }
    }
}

extension AppModel {
    func copyPendantRecordings() async {
        guard hasSelectedProfile, !bluetooth.captureEnabled, let store,
              let codec = bluetooth.codec else { return }
        await bluetooth.storage.copy(deviceID: bluetooth.identifier, codec: codec, store: store)
        checkpoint(); retryUploads()
    }
}

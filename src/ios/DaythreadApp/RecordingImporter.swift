import Foundation
import Observation
import OmiCore

struct ImportRecording: Identifiable {
    let id = UUID()
    let url: URL
    let name: String
    let size: Int
    let digest: String
    let suggestedEnd: Bool
    var recordedAt: Date
    var timeConfirmed: Bool
    var reference = RecordingTimeReference.end
    var converted: ConvertedRecording?
    var plan: RecordingUploadPlan?
    var completed = false
    var skipped: String?
    var duplicateOf: UUID?
    var error: String?
    var locked: Bool { plan != nil || completed || skipped != nil }
    var handled: Bool { completed || skipped != nil }
}

/// The directory is retained by in-flight staging and conversion operations, so
/// dismissing the sheet cannot delete files while the decoder is still using them.
private final class ImportWorkspace: @unchecked Sendable {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("recording-import-" + UUID().uuidString, isDirectory: true)
    deinit { try? FileManager.default.removeItem(at: url) }
}

@MainActor @Observable
final class RecordingImporter {
    var recordings: [ImportRecording] = []
    var busy = false
    var stage = ""
    var activeName = ""
    var error: String?
    var sentBytes: Int64 = 0
    var totalBytes: Int64 = 0
    var confirmedParts = 0
    var partCount = 0
    var confirming = false
    var linking = false
    var conversationSourceID = ""
    var vendorID = "manual"
    private let workspace = ImportWorkspace()
    private var transferID = UUID()

    var allHandled: Bool { !recordings.isEmpty && recordings.allSatisfy(\.handled) }
    var completed: Int { recordings.filter(\.completed).count }
    var skipped: Int { recordings.filter { $0.skipped != nil }.count }
    var locked: Bool { busy || recordings.contains { $0.plan != nil || $0.completed } }
    var canUpload: Bool {
        !busy && !recordings.isEmpty && !allHandled && (!linking || UUID(uuidString: conversationSourceID) != nil)
            && recordings.filter { !$0.handled }.allSatisfy(\.timeConfirmed)
    }

    func add(_ urls: [URL], client: BackendClient) async {
        guard !locked else { return }
        busy = true; error = nil; stage = L10n.source("Checking recordings…")
        defer { busy = false; stage = ""; activeName = "" }
        for url in urls {
            do {
                try Task.checkCancellation()
                activeName = url.lastPathComponent
                var recording = try await Self.stage(url, workspace: workspace)
                if let existing = recordings.first(where: { $0.digest == recording.digest }) {
                    recording.skipped = L10n.source("Same file as \(existing.name)")
                    recording.duplicateOf = existing.id
                } else {
                    do { recording.skipped = try await duplicate(recording.digest, client: client) }
                    catch {
                        try Task.checkCancellation()
                        // Network failure must not prevent selecting dates or retrying.
                        recording.error = L10n.source("Duplicate check unavailable. Upload will check again.")
                    }
                }
                try Task.checkCancellation()
                recordings.append(recording)
            } catch {
                if Task.isCancelled { return }
                self.error = "\(url.lastPathComponent): \(L10n.error(error))"
            }
        }
    }

    func remove(_ id: UUID) {
        guard !busy, let record = recordings.first(where: { $0.id == id }), !record.locked else { return }
        try? FileManager.default.removeItem(at: record.url.deletingLastPathComponent())
        recordings.removeAll { $0.id == id }
        // If the selected original is removed before upload, another selected
        // copy must become eligible rather than remaining a skipped duplicate.
        if let promoted = recordings.firstIndex(where: { $0.duplicateOf == id }) {
            recordings[promoted].skipped = nil
            recordings[promoted].duplicateOf = nil
            let owner = recordings[promoted]
            for index in recordings.indices where recordings[index].duplicateOf == id {
                recordings[index].duplicateOf = owner.id
                recordings[index].skipped = L10n.source("Same file as \(owner.name)")
            }
        }
    }

    func upload(client: BackendClient, wiFiOnly: Bool, onImported: () -> Void) async {
        guard canUpload else { return }
        busy = true; error = nil
        let before = completed
        defer {
            transferID = UUID()
            busy = false; stage = ""; activeName = ""
            if completed > before { onImported() }
        }
        for index in recordings.indices where !recordings[index].handled {
            if Task.isCancelled { break }
            activeName = recordings[index].name
            recordings[index].error = nil
            sentBytes = 0; totalBytes = 0; confirming = false
            do { try await upload(index, client: client, wiFiOnly: wiFiOnly) }
            catch {
                if Task.isCancelled { break }
                recordings[index].error = L10n.error(error)
            }
        }
        if Task.isCancelled {
            error = L10n.source("Upload paused. Retry to resume with the same recording IDs.")
        } else if recordings.contains(where: { !$0.handled && $0.error != nil }) {
            error = L10n.source("Some recordings need another try. Completed parts are saved and will not be uploaded twice.")
        }
    }

    private func duplicate(_ digest: String, client: BackendClient) async throws -> String? {
        let result = try await client.request("v1/recording-uploads/check", method: "POST", body: ["digest": digest])
        try Task.checkCancellation()
        switch result["duplicate"] {
        case .bool(true):
            let source = result["source_id"].text
            return source.isEmpty ? L10n.source("Already imported") : L10n.source("Already imported as \(source)")
        case .bool(false): return nil
        default: throw RecordingImportError(L10n.source("The server returned an invalid duplicate check."))
        }
    }

    private func upload(_ index: Int, client: BackendClient, wiFiOnly: Bool) async throws {
        stage = L10n.source("Checking for duplicates…")
        if let match = try await duplicate(recordings[index].digest, client: client) {
            recordings[index].skipped = match
            return
        }
        if recordings[index].converted == nil {
            stage = L10n.source("Preparing audio on this iPhone…")
            let file = recordings[index].url
            let owner = workspace
            let converted = try await RecordingAudioConverter.convert(file, into: file.deletingLastPathComponent().appendingPathComponent("parts"))
            withExtendedLifetime(owner) {}
            try Task.checkCancellation()
            recordings[index].converted = converted
        }
        guard let converted = recordings[index].converted else { return }
        let record = recordings[index]
        let bounds = try RecordingBounds(recordedAt: record.recordedAt, sampleCount: converted.sampleCount, reference: record.reference)
        stage = L10n.source("Confirming the upload plan…")
        var body: [String: Any] = [
            "file_digest": record.digest, "digests": converted.parts.map(\.digest), "sample_count": converted.sampleCount,
            "source_id": record.name, "started_at": record.plan?.startedAt ?? DurableStore.stamp(bounds.startedAt),
            "ended_at": record.plan?.endedAt ?? DurableStore.stamp(bounds.endedAt),
            "conversation_source_id": linking ? conversationSourceID as Any : NSNull(),
        ]
        // A resumed server plan owns provenance, including legacy manual plans.
        let plannedVendor = record.plan?.resolvedVendorID ?? vendorID
        for (key, value) in RecordingImport.vendorParameters(vendorID: plannedVendor) {
            body[key] = value
        }
        let response = try await client.decoded("v1/recording-uploads/prepare", method: "POST", body: body, as: RecordingPreparation.self)
        try Task.checkCancellation()
        guard case .upload(let plan) = response else {
            if case .duplicate(let source) = response { recordings[index].skipped = L10n.source("Already imported as \(source)") }
            return
        }
        try plan.validate(digests: converted.parts.map(\.digest), sampleCount: converted.sampleCount)
        recordings[index].plan = plan
        totalBytes = Int64(converted.bytes)
        partCount = plan.parts.count
        confirmedParts = plan.parts.filter(\.uploaded).count
        var confirmedBytes = zip(plan.parts, converted.parts).reduce(0) { $0 + ($1.0.uploaded ? $1.1.bytes : 0) }
        sentBytes = Int64(confirmedBytes)
        for partIndex in plan.parts.indices where !plan.parts[partIndex].uploaded {
            try Task.checkCancellation()
            let part = converted.parts[partIndex]
            let baseline = confirmedBytes
            let currentTransfer = UUID()
            transferID = currentTransfer
            stage = L10n.source("Uploading part \(partIndex + 1) of \(partCount)…")
            let receipt = try await client.uploadRecording(part.url, id: plan.parts[partIndex].id,
                query: plan.query(for: partIndex), wiFiOnly: wiFiOnly) { [weak self] bytes in
                guard let self, self.transferID == currentTransfer else { return }
                let sent = max(0, min(bytes, Int64(part.bytes)))
                self.sentBytes = Int64(baseline) + sent
                self.confirming = sent == Int64(part.bytes)
            }
            try Task.checkCancellation()
            try plan.validateReceipt(receipt, part: partIndex, sampleCount: part.samples)
            transferID = UUID()
            confirmedBytes += part.bytes
            confirmedParts += 1
            sentBytes = Int64(confirmedBytes)
            confirming = false
        }
        recordings[index].completed = true
        // The server has acknowledged every part; release temporary audio promptly.
        try? FileManager.default.removeItem(at: record.url.deletingLastPathComponent())
    }

    nonisolated private static func stage(_ source: URL, workspace: ImportWorkspace) async throws -> ImportRecording {
        let access = source.startAccessingSecurityScopedResource()
        defer { if access { source.stopAccessingSecurityScopedResource() } }
        try Task.checkCancellation()
        let values = try source.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey, .isRegularFileKey])
        guard values.isRegularFile == true else { throw RecordingImportError(L10n.source("Choose an audio file.")) }
        try RecordingImport.validateFile(name: source.lastPathComponent, bytes: values.fileSize ?? 0)
        let directory = workspace.url.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                               attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
        let copy = directory.appendingPathComponent(source.lastPathComponent)
        do {
            try FileManager.default.copyItem(at: source, to: copy)
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: copy.path)
            try Task.checkCancellation()
            let digest = try RecordingAudioConverter.fileDigest(copy)
            let suggested = RecordingImport.suggestedEnd(modifiedAt: values.contentModificationDate)
            return ImportRecording(url: copy, name: source.lastPathComponent, size: values.fileSize ?? 0,
                                   digest: digest, suggestedEnd: suggested != nil, recordedAt: suggested ?? Date(), timeConfirmed: suggested != nil)
        } catch {
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
    }
}

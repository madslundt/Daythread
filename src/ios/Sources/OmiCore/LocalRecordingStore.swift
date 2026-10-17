import Foundation

public struct LocalRecordingDraft: Codable, Equatable, Identifiable, Sendable {
    public let id: UUID
    public let archiveIdentity: String
    public let startedAt: Date
    /// Nil also identifies a recording interrupted before its completion was saved.
    public var endedAt: Date?

    public init(id: UUID, archiveIdentity: String, startedAt: Date, endedAt: Date? = nil) {
        self.id = id
        self.archiveIdentity = archiveIdentity
        self.startedAt = startedAt
        self.endedAt = endedAt
    }
}

public struct LocalRecordingLoadIssue: Equatable, Sendable {
    public let directoryName: String
    public let message: String
}

/// Local microphone recordings awaiting the owner's upload or removal decision.
/// Use from one serial executor. Filenames come only from UUIDs, never metadata paths.
public final class LocalRecordingStore {
    public enum StoreError: Error, LocalizedError {
        case invalidMetadata
        case invalidEndDate
        case unsafeDirectory

        public var errorDescription: String? {
            switch self {
            case .invalidMetadata: return AppLocalization.text("The saved recording metadata is invalid.")
            case .invalidEndDate: return AppLocalization.text("The recording cannot end before it started.")
            case .unsafeDirectory: return AppLocalization.text("The saved recording directory is invalid.")
            }
        }
    }

    public let directory: URL
    /// A listing preserves unreadable drafts and reports them here for recovery attention.
    public private(set) var loadingIssues: [LocalRecordingLoadIssue] = []
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    private let fileManager = FileManager.default

    public init(directory: URL) throws {
        self.directory = directory
        // Date's native Codable representation round-trips its full precision.
        encoder.dateEncodingStrategy = .deferredToDate
        decoder.dateDecodingStrategy = .deferredToDate
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        try protect(directory)
    }

    public func create(archiveIdentity: String, startedAt: Date = Date()) throws -> LocalRecordingDraft {
        let draft = LocalRecordingDraft(id: UUID(), archiveIdentity: archiveIdentity, startedAt: startedAt)
        try validate(draft)
        let folder = folder(for: draft.id)
        try fileManager.createDirectory(at: folder, withIntermediateDirectories: false)
        try protect(folder)
        // Persist identity and capture time before the microphone writes any audio.
        try save(draft)
        return draft
    }

    public func fileURL(for draft: LocalRecordingDraft) -> URL {
        folder(for: draft.id).appendingPathComponent("recording.m4a")
    }

    @discardableResult
    public func finish(id: UUID, endedAt: Date = Date()) throws -> LocalRecordingDraft {
        var draft = try load(id: id)
        // Retrying completion must not move the original capture boundary.
        if draft.endedAt != nil { return draft }
        guard endedAt.timeIntervalSince1970.isFinite, endedAt >= draft.startedAt else {
            throw StoreError.invalidEndDate
        }
        draft.endedAt = endedAt
        let audio = fileURL(for: draft)
        if fileManager.fileExists(atPath: audio.path) {
            let values = try audio.resourceValues(forKeys: [.isSymbolicLinkKey])
            guard values.isSymbolicLink != true else { throw StoreError.unsafeDirectory }
            try protect(audio)
        }
        try save(draft)
        return draft
    }

    /// Includes unfinished recordings after a crash, even when no audio file survived.
    /// Corrupt drafts remain on disk and do not hide other recoverable recordings.
    public func recordings(for archiveIdentity: String) throws -> [LocalRecordingDraft] {
        loadingIssues = []
        let entries = try fileManager.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        var drafts: [LocalRecordingDraft] = []
        for entry in entries.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
            guard let id = UUID(uuidString: entry.lastPathComponent) else { continue }
            do {
                let draft = try load(id: id)
                if draft.archiveIdentity == archiveIdentity { drafts.append(draft) }
            } catch {
                loadingIssues.append(LocalRecordingLoadIssue(directoryName: entry.lastPathComponent, message: error.localizedDescription))
            }
        }
        return drafts.sorted {
            if $0.startedAt == $1.startedAt { return $0.id.uuidString < $1.id.uuidString }
            return $0.startedAt > $1.startedAt
        }
    }

    public func remove(id: UUID) throws {
        let folder = folder(for: id)
        guard fileManager.fileExists(atPath: folder.path) else { return }
        try requireSafeDirectory(folder)
        try fileManager.removeItem(at: folder)
    }

    private func folder(for id: UUID) -> URL {
        directory.appendingPathComponent(id.uuidString, isDirectory: true)
    }

    private func validate(_ draft: LocalRecordingDraft) throws {
        guard !draft.archiveIdentity.isEmpty, draft.startedAt.timeIntervalSince1970.isFinite,
              draft.endedAt.map({ $0.timeIntervalSince1970.isFinite && $0 >= draft.startedAt }) ?? true else {
            throw StoreError.invalidMetadata
        }
    }

    private func requireSafeDirectory(_ url: URL) throws {
        let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard values.isDirectory == true, values.isSymbolicLink != true else { throw StoreError.unsafeDirectory }
    }

    private func load(id: UUID) throws -> LocalRecordingDraft {
        let folder = folder(for: id)
        try requireSafeDirectory(folder)
        let metadata = folder.appendingPathComponent("metadata.json")
        let values = try metadata.resourceValues(forKeys: [.isSymbolicLinkKey])
        guard values.isSymbolicLink != true else { throw StoreError.unsafeDirectory }
        let draft = try decoder.decode(LocalRecordingDraft.self, from: Data(contentsOf: metadata))
        guard draft.id == id else { throw StoreError.invalidMetadata }
        try validate(draft)
        return draft
    }

    private func save(_ draft: LocalRecordingDraft) throws {
        try requireSafeDirectory(folder(for: draft.id))
        var options: Data.WritingOptions = .atomic
        #if os(iOS)
        options.insert(.completeFileProtectionUntilFirstUserAuthentication)
        #endif
        try encoder.encode(draft).write(to: folder(for: draft.id).appendingPathComponent("metadata.json"), options: options)
    }

    private func protect(_ url: URL) throws {
        #if os(iOS)
        try fileManager.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: url.path)
        #endif
    }
}

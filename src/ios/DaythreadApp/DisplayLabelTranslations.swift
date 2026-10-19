import Foundation
import OmiCore
import SwiftUI

/// A response is visible only for the exact current archive, language and canonical labels.
@MainActor @Observable final class DisplayLabelTranslationState {
    private var requestID = UUID()
    private var loadedKey = ""
    private var translations: [DisplayTranslations.Reference: String] = [:]
    private var failure: String?

    func values(for key: String) -> [DisplayTranslations.Reference: String] { loadedKey == key ? translations : [:] }
    func error(for key: String) -> String? { loadedKey == key ? failure : nil }
    func isLoading(key: String, language: String?, labels: [DisplayTranslations.Label]) -> Bool {
        language != nil && !labels.isEmpty && loadedKey != key
    }
    func load(key: String, language: String?, labels: [DisplayTranslations.Label], client: () throws -> BackendClient) async {
        let token = UUID(); requestID = token
        translations = [:]; failure = nil; loadedKey = ""
        guard let language, !language.isEmpty, !labels.isEmpty else { loadedKey = key; return }
        do {
            let result = try await client().displayTranslations(language: language, labels: labels)
            guard !Task.isCancelled, requestID == token else { return }
            translations = result; loadedKey = key
        } catch {
            guard !Task.isCancelled, requestID == token else { return }
            failure = L10n.text("The overview translation could not be loaded. Try again."); loadedKey = key
        }
    }
}

extension BackendClient {
    func displayTranslations(language: String, labels: [DisplayTranslations.Label]) async throws -> [DisplayTranslations.Reference: String] {
        guard !language.isEmpty, !labels.isEmpty else { return [:] }
        var result: [DisplayTranslations.Reference: String] = [:]
        for start in stride(from: 0, to: labels.count, by: 50) {
            try Task.checkCancellation()
            let batch = Array(labels[start..<min(labels.count, start + 50)])
            let response: DisplayTranslations.Response = try await decisionRequest("v1/display-translations", method: "POST",
                body: DisplayTranslations.Request(language: language, items: batch.map(\.reference)))
            guard let projected = DisplayTranslations.project(response, language: language, labels: batch) else {
                throw BackendFailure(message: L10n.text("The overview translation could not be loaded. Try again."))
            }
            result.merge(projected) { _, new in new }
        }
        return result
    }
}

extension RecapOriginalTopics.Topic {
    var displayLabel: DisplayTranslations.Label? {
        guard let part = parts.first else { return nil }
        return .init(reference: .init(kind: "source_topic", id: part.sourceID, source_version: part.sourceVersion,
            transcript_version: part.transcriptVersion, start_offset: part.start, end_offset: part.end,
            chapter_id: part.chapterID.isEmpty ? nil : part.chapterID), title: title)
    }
}

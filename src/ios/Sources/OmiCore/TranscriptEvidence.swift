import Foundation

public struct TranscriptPassage: Equatable, Sendable {
    public let before: String
    public let quote: String
    public let after: String
}

public enum TranscriptEvidence {
    /// Backend offsets count Unicode code points, not Swift grapheme clusters or UTF-16 units.
    public static func passage(text: String, sourceID: String, sourceVersion: String,
                               expectedID: String, expectedVersion: String,
                               start: Int, end: Int, quote: String) -> TranscriptPassage? {
        guard !text.isEmpty, sourceID == expectedID, sourceVersion == expectedVersion,
              start >= 0, end > start else { return nil }
        let scalars = Array(text.unicodeScalars)
        guard end <= scalars.count else { return nil }
        let selected = String(String.UnicodeScalarView(scalars[start..<end]))
        guard selected.unicodeScalars.elementsEqual(quote.unicodeScalars) else { return nil }
        return TranscriptPassage(before: String(String.UnicodeScalarView(scalars[..<start])),
                                 quote: selected,
                                 after: String(String.UnicodeScalarView(scalars[end...])))
    }
}

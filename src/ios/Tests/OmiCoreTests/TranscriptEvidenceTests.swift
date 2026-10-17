import XCTest
@testable import OmiCore

final class TranscriptEvidenceTests: XCTestCase {
    func testUsesBackendUnicodeCodePointOffsets() {
        let text = "Hi 👩🏽‍💻, cafe\u{301} tomorrow"
        let passage = TranscriptEvidence.passage(text: text, sourceID: "one", sourceVersion: "v1",
                                                expectedID: "one", expectedVersion: "v1",
                                                start: 9, end: 14, quote: "cafe\u{301}")
        XCTAssertEqual(passage?.before, "Hi 👩🏽‍💻, ")
        XCTAssertEqual(passage?.quote, "cafe\u{301}")
        XCTAssertEqual(passage?.after, " tomorrow")
    }

    func testRejectsHistoricalSourcesChangedQuotesAndInvalidBounds() {
        func locate(id: String = "one", version: String = "v1", start: Int = 0, end: Int = 5, quote: String = "Hello") -> TranscriptPassage? {
            TranscriptEvidence.passage(text: "Hello world", sourceID: id, sourceVersion: version,
                                       expectedID: "one", expectedVersion: "v1", start: start, end: end, quote: quote)
        }
        XCTAssertNotNil(locate())
        XCTAssertNil(locate(id: "another"))
        XCTAssertNil(locate(version: "v2"))
        XCTAssertNil(locate(start: -1))
        XCTAssertNil(locate(end: 0))
        XCTAssertNil(locate(end: 12))
        XCTAssertNil(locate(quote: "hello"))
    }

    func testDoesNotTreatNormalizedButDifferentQuoteAsExactEvidence() {
        XCTAssertNil(TranscriptEvidence.passage(text: "cafe\u{301}", sourceID: "one", sourceVersion: "v1",
                                               expectedID: "one", expectedVersion: "v1", start: 0, end: 5, quote: "café"))
    }
}

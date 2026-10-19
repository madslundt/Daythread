import XCTest
@testable import OmiCore

final class RecapOriginalTopicsTests: XCTestCase {
    private func source(_ id: String = "a", version: String = "v1", transcript: String = "t1",
                        text: String = "☕️ Café notes.", title: String = "Café", chapter: String = "") -> RecapOriginalTopics.Source {
        .init(id: id, sourceVersion: version, transcriptVersion: transcript, text: text,
              sections: [.init(title: title, chapterID: chapter, start: 0, end: text.unicodeScalars.count)])
    }

    func testOriginalSectionsRemainBrowsableWithoutAnyGeneratedRecap() {
        let original = source()
        let topics = RecapOriginalTopics.topics(sources: [original])
        XCTAssertEqual(topics.count, 1)
        XCTAssertEqual(topics[0].title, "Café")
        XCTAssertEqual(topics[0].parts[0].quote, original.text)
        XCTAssertEqual(topics[0].parts[0].end, original.text.unicodeScalars.count)
        XCTAssertEqual(RecapOriginalTopics.topics(sources: [original], query: "CAFE"), topics)
        XCTAssertTrue(RecapOriginalTopics.topics(sources: [original], query: "missing").isEmpty)
        XCTAssertTrue(RecapOriginalTopics.topics(sources: [original], excluding: [
            .init(sourceID: "a", start: 0, end: original.text.unicodeScalars.count)
        ]).isEmpty)
    }

    func testOnlyBackendChapterIdentityJoinsContinuingOriginalParts() {
        let first = source(chapter: "shared")
        let continuation = source("b", text: "Continuing notes.", chapter: "shared")
        let unrelated = source("c")
        let topics = RecapOriginalTopics.topics(sources: [first, continuation, unrelated])
        XCTAssertEqual(topics.count, 2)
        XCTAssertEqual(topics[0].parts.map(\.sourceID), ["a", "b"])
        XCTAssertEqual(topics[1].parts.map(\.sourceID), ["c"])
    }

    func testCurrentSourceMustKeepVersionsExactQuoteAndOriginalSectionIdentity() throws {
        let original = source(chapter: "chapter")
        let part = try XCTUnwrap(RecapOriginalTopics.topics(sources: [original]).first?.parts.first)
        XCTAssertTrue(RecapOriginalTopics.matchesCurrent(part, source: original))
        XCTAssertFalse(RecapOriginalTopics.matchesCurrent(part, source: source(version: "v2", chapter: "chapter")))
        XCTAssertFalse(RecapOriginalTopics.matchesCurrent(part, source: source(transcript: "t2", chapter: "chapter")))
        XCTAssertFalse(RecapOriginalTopics.matchesCurrent(part, source: source(text: "Different text", chapter: "chapter")))
        XCTAssertFalse(RecapOriginalTopics.matchesCurrent(part, source: source(title: "New analysis", chapter: "chapter")))
        XCTAssertFalse(RecapOriginalTopics.matchesCurrent(part, source: source(chapter: "other")))
        XCTAssertFalse(RecapOriginalTopics.matchesCurrent(part, source: .init(id: "a", sourceVersion: "v1", transcriptVersion: "t1",
            text: original.text, sections: [.init(title: "Café", chapterID: "chapter", start: 0, end: 5)])))
    }

    func testInvalidOrUnversionedOriginalSectionsNeverBecomeNavigationTargets() {
        XCTAssertTrue(RecapOriginalTopics.topics(sources: [source(version: "")]).isEmpty)
        XCTAssertTrue(RecapOriginalTopics.topics(sources: [source(transcript: "")]).isEmpty)
        let invalid = RecapOriginalTopics.Source(id: "a", sourceVersion: "v1", transcriptVersion: "t1", text: "Short",
            sections: [.init(title: "Outside", start: 0, end: 100), .init(title: "Backwards", start: 4, end: 2)])
        XCTAssertTrue(RecapOriginalTopics.topics(sources: [invalid]).isEmpty)
    }
}

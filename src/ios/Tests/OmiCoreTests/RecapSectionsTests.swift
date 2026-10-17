import XCTest
@testable import OmiCore

final class RecapSectionsTests: XCTestCase {
    func testTopicSummariesCopyOnlyTheirOwnContentWithoutCitationsOrEmptyTitles() {
        let markdown = "# Dagbogsoversigt 2026-10-16\n## Omi\nEn lydoptager. [11111111-1111-4111-8111-111111111111]\n## Tesla Roadster\nEn samtale om bilen.\n## Empty"
        let topics = RecapSections.topics(markdown)
        XCTAssertEqual(topics.map(\.title), ["Omi", "Tesla Roadster"])
        XCTAssertEqual(topics[1].copyText, "Tesla Roadster\n\nEn samtale om bilen.")
        XCTAssertEqual(topics[0].copyText, "Omi\n\nEn lydoptager.")
    }

    func testMatchesWebRecapKeysForHeadingsListsContinuationAndLegacyFollowUps() {
        XCTAssertEqual(RecapSections.texts("## Action items\n1. Share notes\n   before Thursday.\n- [x] Call Sam\n\n## Highlights\n+ A useful conversation"), [
            "Action items", "Share notes before Thursday.", "Call Sam", "Highlights", "A useful conversation"
        ])
        XCTAssertEqual(RecapSections.texts("**To follow up:** share the prototype, call the bike shop, bring the photo album, and confirm game night. Completion is not recorded."), [
            "To follow up", "share the prototype", "call the bike shop", "bring the photo album", "confirm game night."
        ])
        XCTAssertEqual(RecapSections.texts("To follow up: email Alice, Bob and Charlie, and confirm the time."), [
            "To follow up", "email Alice, Bob and Charlie", "confirm the time."
        ])
        XCTAssertEqual(RecapSections.texts("A paragraph\n\n**Next steps**\n- Book a service"), ["A paragraph", "Next steps", "Book a service"])
    }
}

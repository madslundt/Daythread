import XCTest
@testable import OmiCore

final class RecapPresentationTests: XCTestCase {
    private let sourceID = "11111111-1111-4111-8111-111111111111"

    func testGroupsMaintainImmutableImportanceKeysAndHideEmptyHeaders() {
        let markdown = "# Diary Recap: 2026-10-16\n**Highlights**\n- **Gym** — A workout [\(sourceID)].\n## Follow-ups\n- Call Sam\n## Empty"
        let groups = RecapPresentation.groups(markdown, day: "2026-10-16")
        XCTAssertEqual(groups.map(\.title), ["**Highlights**", "Follow-ups"])
        XCTAssertEqual(groups[0].parts[0].text, "**Gym** — A workout [\(sourceID)].")
        XCTAssertEqual(groups[0].text, "**Gym** — A workout.")
        XCTAssertFalse(groups[0].hasActions)
        XCTAssertTrue(groups[1].hasActions)
        XCTAssertEqual(RecapPresentation.readingText("Bring [the original] notes."), "Bring [the original] notes.")
    }

    func testLegacyFollowUpsContinuationAndCheckboxArePresentationOnly() {
        let parts = RecapPresentation.parts("## Action items\n1. Share notes\n   before Thursday.\n- [x] Call Sam\n## Highlights\n+ A useful conversation")
        XCTAssertEqual(parts.filter { $0.kind == .item }.map(\.text), ["Share notes before Thursday.", "Call Sam", "A useful conversation"])
        XCTAssertEqual(parts.filter { $0.kind == .item }.map(\.action), [true, true, false])
        XCTAssertEqual(RecapPresentation.parts("To follow up: email Alice, Bob and Charlie, and confirm the time. Completion is not recorded.").filter { $0.action }.map(\.text), ["email Alice, Bob and Charlie", "confirm the time."])
        XCTAssertEqual(RecapPresentation.parts("**Next steps**\n- Book a service").filter { $0.action }.map(\.text), ["Book a service"])
    }

    func testTopicCopiesRemoveDuplicateParagraphsButKeepAllUniqueDetails() {
        let groups = RecapPresentation.groups("## Cocoa\nA useful detail [\(sourceID)].\n- A useful detail [\(sourceID)].\n- Another useful detail.", day: "2026-10-16")
        XCTAssertEqual(groups[0].text, "A useful detail.\n\nAnother useful detail.")
    }

    func testCitationsOutrankWordsAndNeverFallBackWhenDeleted() {
        let sources: [RecapPresentation.Source] = [.init(id: sourceID, text: "Gym workout"), .init(id: "22222222-2222-4222-8222-222222222222", text: "Work prototype")]
        let cited = RecapPresentation.sources(for: "Work prototype [\(sourceID)]", in: sources)
        XCTAssertEqual(cited.basis, .citation)
        XCTAssertEqual(cited.ids, [sourceID])
        let missing = RecapPresentation.sources(for: "Gym workout [33333333-3333-4333-8333-333333333333]", in: sources)
        XCTAssertEqual(missing.basis, .citation)
        XCTAssertTrue(missing.ids.isEmpty)
        XCTAssertTrue(RecapPresentation.sources(for: "09:30 recording", in: sources).ids.isEmpty)
    }

    func testSubjectAndDistinctiveWordsSupportUncitedOlderRecaps() {
        let sources: [RecapPresentation.Source] = [.init(id: sourceID, text: "The Gym workout was vigorous."), .init(id: "other", text: "Work prototype moved forward.")]
        XCTAssertEqual(RecapPresentation.sources(for: "**Gym · 07:15** — Exercise", in: sources).ids, [sourceID])
        XCTAssertEqual(RecapPresentation.sources(for: "Vigorous workout", in: sources).ids, [sourceID])
        XCTAssertEqual(RecapPresentation.sources(for: "Gym", in: sources).ids, [])
    }

    func testPassagesRespectUnicodeOffsetsAndTopicPrecedence() {
        let cocoa = "☕️ Tested a milk frother with cocoa. "
        let tesla = "We discussed the Tesla Roadster."
        let sections: [RecapPresentation.Section] = [
            .init(title: "Cocoa and milk frother", start: 0, end: cocoa.unicodeScalars.count),
            .init(title: "Tesla Roadster", start: cocoa.unicodeScalars.count, end: (cocoa + tesla).unicodeScalars.count)
        ]
        XCTAssertEqual(RecapPresentation.sections(for: "Tested a milk frother with cocoa [\(sourceID)]", text: cocoa + tesla, sections: sections), [sections[0]])
        XCTAssertEqual(RecapPresentation.sections(for: "Milk frother discussion", topic: "Tesla Roadster", text: cocoa + tesla, sections: sections), [sections[1]])
        XCTAssertEqual(RecapPresentation.sections(for: "Unrelated point", text: cocoa + tesla, sections: sections), [])
        XCTAssertEqual(RecapPresentation.sections(for: "milk frother cocoa", text: cocoa, sections: [.init(title: "Cocoa", start: 0, end: 500)]), [])
    }

    func testGenericDanishOverlapAndAmbiguousMatchesDoNotClaimEvidence() {
        let omi = "OMI lydoptager: Det kan optage lyd på arbejdet og give et recap af dagen. "
        let tesla = "Tesla Roadster: Bilen kan køre på vægge og loftet ifølge samtalen."
        let sections: [RecapPresentation.Section] = [
            .init(title: "Oplevelse af OMI lydoptager", start: 0, end: omi.unicodeScalars.count),
            .init(title: "Diskussion af Tesla Roadster", start: omi.unicodeScalars.count, end: (omi + tesla).unicodeScalars.count)
        ]
        XCTAssertEqual(RecapPresentation.sections(for: "Tesla Roadster\nDet nævnes, at bilen kan køre på vægge og loftet", text: omi + tesla, sections: sections), [sections[1]])
        let text = "cocoa frother. cocoa frother."
        let tied: [RecapPresentation.Section] = [.init(title: "First", start: 0, end: 14), .init(title: "Second", start: 14, end: text.unicodeScalars.count)]
        XCTAssertEqual(RecapPresentation.sections(for: "cocoa frother", text: text, sections: tied), [])
    }

    func testOptionalChecksPersistAndStaySeparateAcrossArchivesDaysAndVersions() throws {
        let suite = "RecapPresentationTests." + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = RecapCheckStore(defaults: defaults)
        let key = "Call Sam [\(sourceID)]"
        store.save([key], archive: "home.profile.alice", day: "2026-10-16", version: "v1")
        XCTAssertEqual(RecapCheckStore(defaults: defaults).checks(archive: "home.profile.alice", day: "2026-10-16", version: "v1"), [key])
        XCTAssertTrue(store.checks(archive: "home.profile.bob", day: "2026-10-16", version: "v1").isEmpty)
        XCTAssertTrue(store.checks(archive: "home.profile.alice", day: "2026-10-17", version: "v1").isEmpty)
        XCTAssertTrue(store.checks(archive: "home.profile.alice", day: "2026-10-16", version: "v2").isEmpty)
        store.save([], archive: "home.profile.alice", day: "2026-10-16", version: "v1")
        XCTAssertTrue(store.checks(archive: "home.profile.alice", day: "2026-10-16", version: "v1").isEmpty)
    }
}

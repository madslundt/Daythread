import XCTest
@testable import OmiCore

final class RecapTranslationMappingTests: XCTestCase {
    private let first = "11111111-1111-4111-8111-111111111111"
    private let second = "22222222-2222-4222-8222-222222222222"
    private let day = "2026-10-16"

    private func structure(_ original: String, _ translated: String, version: String = "current") -> RecapTranslationMapping.Structure {
        let source = original.components(separatedBy: "\n"), display = translated.components(separatedBy: "\n")
        precondition(source.count == display.count)
        var offset = 0, blocks: [RecapTranslationMapping.Block] = []
        for (left, right) in zip(source, display) {
            if !left.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                let trimmed = left.trimmingCharacters(in: .whitespaces)
                let kind = trimmed.hasPrefix("#") || (trimmed.hasPrefix("**") && trimmed.hasSuffix("**")) ? "heading" : trimmed.hasPrefix("- ") ? "item" : "text"
                blocks.append(.init(index: blocks.count, kind: kind, start_offset: offset,
                    end_offset: offset + left.unicodeScalars.count, original_text: left, text: right))
            }
            offset += left.unicodeScalars.count + 1
        }
        return .init(summary_version: version, blocks: blocks)
    }
    private func map(_ original: String, _ translated: String, _ structure: RecapTranslationMapping.Structure? = nil) -> RecapTranslationMapping.Mapping? {
        RecapTranslationMapping.map(original: original, translated: translated, structure: structure ?? self.structure(original, translated), summaryVersion: "current", day: day)
    }

    func testTranslatedDisplayKeepsCanonicalTopicIdentityCitationsAndActionOwnership() throws {
        let original = "# Diary Recap: \(day)\n\n## Highlights\n- **Coffee** — Keep cocoa ratio [\(first)].\n  Bring https://example.com/notes\n- **Roadster** — Book service [\(second)].\n## Action points\n- Call Sam [\(first)]."
        let translated = "# Dagopsamling: \(day)\n\n## Højdepunkter\n- **Kaffe** — Behold kakaoforhold [\(first)].\n  Medbring https://example.com/notes\n- **Bilservice** — Bestil service [\(second)].\n## Handlinger\n- Ring til Sam [\(first)]."
        let baseline = RecapTranslationMapping.original(original, day: day)
        let mapped = try XCTUnwrap(map(original, translated))
        XCTAssertEqual(mapped.topics.map(\.id), baseline.topics.map(\.id))
        XCTAssertEqual(mapped.topics.map(\.original), baseline.topics.map(\.original))
        XCTAssertEqual(mapped.topics.map(\.display.title), ["Kaffe", "Bilservice"])
        XCTAssertEqual(RecapPresentation.topicSummary(mapped.topics[0].display), "Behold kakaoforhold. Medbring https://example.com/notes")
        XCTAssertEqual(mapped.followUps.count, 1)
        XCTAssertEqual(mapped.followUps[0].text, "Ring til Sam.")
        let evidence = RecapPresentation.sources(for: mapped.topics[1].original.parts.map(\.text).joined(),
            in: [.init(id: first, text: "Coffee"), .init(id: second, text: "Roadster")])
        XCTAssertEqual(evidence.ids, [second])
    }

    func testOffsetsUseUnicodeScalarsAndRetainBlankLines() throws {
        let original = "## Cocoa\n☕️ Ratio is fixed.\n\n## Café\nCafé is open."
        let translated = "## Kakao\n☕️ Forholdet er fast.\n\n## Caféen\nCaféen er åben."
        let metadata = structure(original, translated)
        XCTAssertEqual(metadata.blocks[2].start_offset, "## Cocoa\n☕️ Ratio is fixed.\n\n".unicodeScalars.count)
        XCTAssertNotNil(map(original, translated, metadata))
        XCTAssertNil(map(original, translated.replacingOccurrences(of: "\n\n", with: "\n"), metadata))
    }

    func testWrongVersionRangesOriginalTextIndexOrKindRejectsTheMapping() {
        let original = "## Coffee\nA useful point.", translated = "## Kaffe\nEn nyttig pointe."
        let metadata = structure(original, translated), block = metadata.blocks[1]
        let changed: [RecapTranslationMapping.Block] = [
            .init(index: 0, kind: block.kind, start_offset: block.start_offset, end_offset: block.end_offset, original_text: block.original_text, text: block.text),
            .init(index: block.index, kind: "heading", start_offset: block.start_offset, end_offset: block.end_offset, original_text: block.original_text, text: block.text),
            .init(index: block.index, kind: block.kind, start_offset: block.start_offset - 1, end_offset: block.end_offset, original_text: block.original_text, text: block.text),
            .init(index: block.index, kind: block.kind, start_offset: block.start_offset, end_offset: block.end_offset, original_text: "Different original", text: block.text)
        ]
        for changed in changed {
            XCTAssertNil(map(original, translated, .init(summary_version: "current", blocks: [metadata.blocks[0], changed])))
        }
        XCTAssertNil(map(original, translated, .init(summary_version: "old", blocks: metadata.blocks)))
        XCTAssertNil(map(original, translated, .init(version: 2, summary_version: "current", blocks: metadata.blocks)))
        XCTAssertNil(map(original, translated, .init(summary_version: "current", blocks: Array(metadata.blocks.reversed()))))
        XCTAssertNil(map(original, translated, .init(summary_version: "current", blocks: [metadata.blocks[0]])))
    }

    func testFlattenedTopicsLostLabelsAndChangedCitationsOrURLsNeverBecomeDisplayMappings() {
        let original = "## Highlights\n- **Coffee** — Bring notes [\(first)] https://example.com/notes\n- **Roadster** — Book service [\(second)]."
        let changed = [
            "## Højdepunkter\nKaffe — Medbring noter [\(first)] https://example.com/notes\nBil — Bestil service [\(second)].",
            "## Højdepunkter\n- Kaffe — Medbring noter [\(first)] https://example.com/notes\n- **Bil** — Bestil service [\(second)].",
            "## Højdepunkter\n- **Kaffe** — Medbring noter [\(second)] https://example.com/notes\n- **Bil** — Bestil service [\(first)].",
            "## Højdepunkter\n- **Kaffe** — Medbring noter [\(first)] https://example.com/other\n- **Bil** — Bestil service [\(second)]."
        ]
        for translated in changed { XCTAssertNil(map(original, translated)) }
    }

    func testTranslatedPlainLegacyFollowUpLabelsKeepTheirOriginalActionGroup() throws {
        let original = "## Work\n- Keep the plan [\(first)].\nTo follow up: email Alice, and confirm the time.\n## Home\n- Keep the note [\(second)]."
        let translated = "## Arbejde\n- Behold planen [\(first)].\nOpfølgning: send en mail til Alice og bekræft tidspunktet.\n## Hjem\n- Behold noten [\(second)]."
        let mapped = try XCTUnwrap(map(original, translated))
        XCTAssertEqual(mapped.topics.map(\.display.title), ["Arbejde", "Hjem"])
        XCTAssertEqual(mapped.topics.map(\.original), RecapTranslationMapping.original(original, day: day).topics.map(\.original))
        XCTAssertEqual(mapped.followUps.map(\.text), ["Opfølgning: send en mail til Alice og bekræft tidspunktet."])
    }

    func testTopicNavigationResolvesCanonicalOriginalTitleWhenDisplayIsTranslated() throws {
        let original = "## Work\nKeep the plan [\(first)].\n## Garden & home\nOrder seeds [\(second)]."
        let translated = "## Arbejde\nBehold planen [\(first)].\n## Have og hjem\nBestil frø [\(second)]."
        let mapped = try XCTUnwrap(map(original, translated))
        XCTAssertEqual(RecapTranslationMapping.topicID(for: "Garden & home", in: mapped), "recap:1")
        XCTAssertNil(RecapTranslationMapping.topicID(for: "Have og hjem", in: mapped))
        XCTAssertNil(RecapTranslationMapping.topicID(for: "Garden", in: mapped))
        XCTAssertNil(RecapTranslationMapping.topicID(for: "Removed topic", in: mapped))
    }

    func testTranslatedLinkAndImageLabelsKeepOriginalTargetsAndOrder() throws {
        let original = "## Project\nRead [the plan](https://example.com/plan) and ![the sketch](https://example.com/sketch.png) [\(first)]."
        let translated = "## Projekt\nLæs [planen](https://example.com/plan) og ![skitsen](https://example.com/sketch.png) [\(first)]."
        let mapped = try XCTUnwrap(map(original, translated))
        XCTAssertTrue(mapped.topics[0].display.text.contains("[planen](https://example.com/plan)"))
        XCTAssertTrue(mapped.topics[0].original.text.contains("[the plan](https://example.com/plan)"))
        XCTAssertNil(map(original, translated.replacingOccurrences(of: "https://example.com/plan", with: "https://example.com/other")))
        XCTAssertNil(map(original, translated.replacingOccurrences(of: "![skitsen]", with: "[skitsen]")))
    }
}

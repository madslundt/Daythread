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

    func testTopicBrowserSeparatesLabelledSubjectsAndPreservesEvidence() {
        let second = "22222222-2222-4222-8222-222222222222"
        let markdown = "## Highlights\n- **Café** — Discussed cocoa [\(sourceID)].\n- **Roadster** — Book a service [\(second)].\n## Action points\n- Book a service"
        let topics = RecapPresentation.topics(markdown, day: "2026-10-16")
        XCTAssertEqual(topics.map(\.title), ["Café", "Roadster"])
        XCTAssertEqual(RecapPresentation.topics(markdown, day: "2026-10-16", query: "CAFE"), [topics[0]])
        XCTAssertEqual(RecapPresentation.sources(for: topics[1].parts.map(\.text).joined(), in: [.init(id: sourceID, text: "cocoa"), .init(id: second, text: "service")]).ids, [second])
        XCTAssertEqual(RecapPresentation.groups(markdown, day: "2026-10-16").last?.parts.first?.text, "Book a service")
    }

    func testMeetingSubtopicsStayInsideTheirParentAndRemainSearchable() {
        let second = "22222222-2222-4222-8222-222222222222"
        let markdown = "## NNCD.SAR meeting\n### Bookmarks\n- **Sharing**: Links preserve analysis state [\(sourceID)].\n### Deployment\nRelease is planned [\(second)].\n## Garden\nOrder seeds [\(second)]."
        let topics = RecapPresentation.topics(markdown, day: "2026-10-16")
        XCTAssertEqual(topics.map(\.title), ["NNCD.SAR meeting", "Garden"])
        XCTAssertEqual(topics[0].parts.filter { $0.kind == .heading }.map(\.text), ["Bookmarks", "Deployment"])
        XCTAssertTrue(topics[0].text.contains("**Bookmarks**"))
        XCTAssertTrue(topics[0].text.contains("**Deployment**"))
        XCTAssertEqual(RecapPresentation.topics(markdown, day: "2026-10-16", query: "Bookmarks"), [topics[0]])
        let sources: [RecapPresentation.Source] = [.init(id: sourceID, text: "Links"), .init(id: second, text: "Release")]
        XCTAssertEqual(RecapPresentation.sources(for: topics[0].parts.map(\.text).joined(), in: sources).ids, [sourceID, second])
    }

    func testNestedFollowUpsDoNotHideTheWholeMeeting() {
        let markdown = "## Meeting\n### Release\nBeta is available.\n### Follow-ups\n- Confirm the date.\n## Action points\n- Call Sam."
        let topics = RecapPresentation.topics(markdown, day: "2026-10-16")
        XCTAssertEqual(topics.map(\.title), ["Meeting"])
        XCTAssertTrue(topics[0].text.contains("Confirm the date."))
    }

    func testSubtopicSectionsKeepIntroductionsAndSiblingEvidenceSeparate() throws {
        let second = "22222222-2222-4222-8222-222222222222"
        let markdown = "## NNCD.SAR meeting\nThe team reviewed the release.\n### Bookmarks\nKeep sharing links [\(sourceID)].\n### Deployment\nReview Friday [\(second)]."
        let topic = try XCTUnwrap(RecapPresentation.topics(markdown, day: "2026-10-16").first)
        let sections = topic.contentSections
        XCTAssertEqual(sections.map(\.title), [nil, "Bookmarks", "Deployment"])
        XCTAssertEqual(sections.map(\.text), ["The team reviewed the release.", "Keep sharing links.", "Review Friday."])
        XCTAssertEqual(sections.flatMap(\.parts), topic.parts.filter { $0.kind != .heading })
        XCTAssertTrue(sections[1].parts[0].text.contains(sourceID))
        XCTAssertFalse(sections[1].parts[0].text.contains(second))
        XCTAssertTrue(sections[2].parts[0].text.contains(second))
        let flat = try XCTUnwrap(RecapPresentation.topics("## Garden\nOrder seeds.", day: "2026-10-16").first)
        XCTAssertEqual(flat.contentSections.map(\.title), [nil])
        XCTAssertEqual(flat.contentSections.flatMap(\.parts), flat.parts)
    }

    func testTopicSummaryOmitsOnlyItsRepeatedHeadingWithoutChangingOriginalParts() throws {
        let markdown = "## Highlights\n- **Café (home)** — Discussed **cocoa** [\(sourceID)].\n  Kept the original evidence.\n- **Roadster**: Book a service."
        let topics = RecapPresentation.topics(markdown, day: "2026-10-16")
        let cafe = try XCTUnwrap(topics.first)
        XCTAssertEqual(RecapPresentation.topicSummary(cafe), "Discussed **cocoa**. Kept the original evidence.")
        XCTAssertTrue(cafe.parts[0].text.contains("[\(sourceID)]"))
        XCTAssertTrue(cafe.text.hasPrefix("**Café (home)**"))
        XCTAssertEqual(RecapPresentation.topicSummary(topics[1]), "Book a service.")
        let plain = try XCTUnwrap(RecapPresentation.topics("## A heading\nA useful **detail**.", day: "2026-10-16").first)
        XCTAssertEqual(RecapPresentation.topicSummary(plain), "A useful **detail**.")
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

    func testWholeRecordingCitationBuildsExactProfileScopedReadPathsAndMatchesExactDocument() throws {
        let recordingID = "44444444-4444-4444-8444-444444444444"
        let sourceID = "55555555-5555-4555-8555-555555555555"
        let resultVersion = String(repeating: "a", count: 64)
        let inputFingerprint = String(repeating: "b", count: 64)
        let pipelineFingerprint = String(repeating: "c", count: 64)
        let citationJSON = """
        {"id":"\(recordingID)","reference_type":"recording_transcription",
         "recording_id":"\(recordingID)","result_version":"\(resultVersion)",
         "input_fingerprint":"\(inputFingerprint)","pipeline_fingerprint":"\(pipelineFingerprint)",
         "source_ids":["\(sourceID)"]}
        """
        let citation = try JSONDecoder().decode(RecordingTranscriptCitation.self, from: Data(citationJSON.utf8))
        XCTAssertEqual(citation.transcriptPath,
            "v1/recordings/\(recordingID)/transcriptions/\(resultVersion)")
        XCTAssertEqual(citation.audioPath, "v1/recordings/\(recordingID)/audio")

        // Exact recording reads do not require fabricated raw-source version fields.
        let documentJSON = """
        {"reference_type":"recording_transcription","recording_id":"\(recordingID)",
         "result_version":"\(resultVersion)","input_fingerprint":"\(inputFingerprint)",
         "pipeline_fingerprint":"\(pipelineFingerprint)","source_ids":["\(sourceID)"],
         "text":"Original full recording text","state":"ready","stale":false,
         "audio_available":true,"duration_seconds":42.5}
        """
        let document = try JSONDecoder().decode(RecordingTranscriptDocument.self, from: Data(documentJSON.utf8))
        XCTAssertTrue(citation.matches(document))
        XCTAssertTrue(document.canPlayCurrentAudio)

        let newerDocumentJSON = documentJSON.replacingOccurrences(of: resultVersion, with: String(repeating: "d", count: 64))
        let newer = try JSONDecoder().decode(RecordingTranscriptDocument.self, from: Data(newerDocumentJSON.utf8))
        XCTAssertFalse(citation.matches(newer), "A historical citation must never resolve to a newer transcript")
    }

    func testWholeRecordingCitationRejectsMalformedIdentityAndHistoricalAudioIsUnavailable() throws {
        let invalid = """
        {"id":"not-a-recording","reference_type":"recording_transcription",
         "recording_id":"not-a-recording","result_version":"\(String(repeating: "a", count: 64))",
         "input_fingerprint":"\(String(repeating: "b", count: 64))",
         "pipeline_fingerprint":"\(String(repeating: "c", count: 64))","source_ids":[]}
        """
        XCTAssertThrowsError(try JSONDecoder().decode(RecordingTranscriptCitation.self, from: Data(invalid.utf8)))

        let recordingID = "44444444-4444-4444-8444-444444444444"
        let historical = """
        {"reference_type":"recording_transcription","recording_id":"\(recordingID)",
         "result_version":"\(String(repeating: "a", count: 64))",
         "input_fingerprint":"\(String(repeating: "b", count: 64))",
         "pipeline_fingerprint":"\(String(repeating: "c", count: 64))","source_ids":[],
         "text":"Historical text","state":"stale","stale":true,"audio_available":true,
         "duration_seconds":42.5}
        """
        let document = try JSONDecoder().decode(RecordingTranscriptDocument.self, from: Data(historical.utf8))
        XCTAssertFalse(document.canPlayCurrentAudio, "Do not play current audio against stale transcript evidence")
    }

    func testRecordingTranscriptSelectionDecodesExactSpeakerRunsAndRejectsPartialEvidence() throws {
        let selectedJSON = #"{"state":"selected","selection_version":7,"result_version":"v2","transcript":{"version":"v2","text":"Hej 😊, ja!","speaker_evidence":{"state":"ready","version":"spk3","utterances":[{"text":"Hej 😊, ","start_offset":0,"end_offset":7,"start":1.25,"end":2.5,"attribution":"attributed","speaker_id":"speaker-1","speaker_label":"Speaker 1","speaker_name":"Maja"},{"text":"ja!","start_offset":7,"end_offset":10,"start":2.5,"end":3.1,"attribution":"unknown","speaker_id":null,"speaker_label":null,"speaker_name":null}]}}}"#
        let selected = try JSONDecoder().decode(RecordingTranscriptSelection.self, from: Data(selectedJSON.utf8))
        XCTAssertEqual(selected.state, .selected)
        XCTAssertEqual(selected.selectionVersion, 7)
        XCTAssertEqual(selected.resultVersion, "v2")
        let utterances = try XCTUnwrap(selected.transcript?.speakerEvidence?.exactUtterances(for: "Hej 😊, ja!"))
        XCTAssertEqual(utterances.map(\.text), ["Hej 😊, ", "ja!"])
        XCTAssertEqual(utterances.first?.speakerName, "Maja")
        XCTAssertEqual(utterances.last?.attribution, "unknown")
        XCTAssertNil(selected.transcript?.speakerEvidence?.exactUtterances(for: "Hej 😊, ja?"))

        let partial = RecordingSpeakerEvidence(state: "ready", version: "spk3", utterances: [utterances[0]])
        XCTAssertNil(partial.exactUtterances(for: "Hej 😊, ja!"), "Partial timing must not hide unknown transcript text")
    }

    func testTranscriptSelectionCASPayloadIncludesExplicitNullToRestoreOriginal() throws {
        let clear = try RecordingTranscriptSelectionRequest(expectedVersion: 9, resultVersion: nil).jsonObject()
        XCTAssertEqual(clear["expected_version"] as? Int, 9)
        XCTAssertTrue(clear["result_version"] is NSNull)

        let select = try RecordingTranscriptSelectionRequest(expectedVersion: 9, resultVersion: "result-v4").jsonObject()
        XCTAssertEqual(select["expected_version"] as? Int, 9)
        XCTAssertEqual(select["result_version"] as? String, "result-v4")

        let stale = try JSONDecoder().decode(RecordingTranscriptSelection.self, from: Data(
            #"{"state":"stale","selection_version":12,"result_version":"old-result","transcript":null}"#.utf8
        ))
        XCTAssertEqual(stale.state, .stale)
        let chooseCurrent = try RecordingTranscriptSelectionRequest(
            expectedVersion: stale.selectionVersion, resultVersion: "new-current-result",
        ).jsonObject()
        XCTAssertEqual(chooseCurrent["expected_version"] as? Int, 12)
        XCTAssertEqual(chooseCurrent["result_version"] as? String, "new-current-result")
    }

    func testSubjectAndDistinctiveWordsSupportUncitedOlderRecaps() {
        let sources: [RecapPresentation.Source] = [.init(id: sourceID, text: "The Gym workout was vigorous."), .init(id: "other", text: "Work prototype moved forward.")]
        XCTAssertEqual(RecapPresentation.sources(for: "**Gym · 07:15** — Exercise", in: sources).ids, [sourceID])
        XCTAssertEqual(RecapPresentation.sources(for: "**Gym**\nA useful conversation.", in: sources).ids, [sourceID])
        XCTAssertEqual(RecapPresentation.sources(for: "**Work prototype**\nA useful conversation [\(sourceID)].", in: sources).ids, [sourceID])
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

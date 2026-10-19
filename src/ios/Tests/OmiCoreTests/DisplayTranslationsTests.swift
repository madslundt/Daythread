import XCTest
@testable import OmiCore

final class DisplayTranslationsTests: XCTestCase {
    private typealias Ref = DisplayTranslations.Reference
    private typealias Label = DisplayTranslations.Label
    private typealias Entry = DisplayTranslations.Entry
    private let task = Ref(kind: "inbox", id: "11111111-1111-4111-8111-111111111111", version: 3)
    private let choice = Ref(kind: "decision", id: "22222222-2222-4222-8222-222222222222", version: 7)
    private let topic = Ref(kind: "source_topic", id: "33333333-3333-4333-8333-333333333333",
        source_version: "source-2", transcript_version: "transcript-4", start_offset: 8, end_offset: 35, chapter_id: "chapter-a")

    func testCanonicalReferencesDecodeAndProjectWithoutChangingIdentity() throws {
        let data = Data("""
        {"language":"da","items":[
          {"kind":"inbox","id":"11111111-1111-4111-8111-111111111111","version":3,"original_title":"Call Alice","title":"Ring til Alice","content_key":"task-content"},
          {"kind":"decision","id":"22222222-2222-4222-8222-222222222222","version":7,"original_title":"Use the green plan","title":"Brug den grønne plan","content_key":"choice-content"},
          {"kind":"source_topic","id":"33333333-3333-4333-8333-333333333333","source_version":"source-2","transcript_version":"transcript-4","start_offset":8,"end_offset":35,"chapter_id":"chapter-a","original_title":"Café ☕️","title":"Kaffemøde ☕️","content_key":"topic-content"}
        ]}
        """.utf8)
        let labels = [Label(reference: task, title: "Call Alice"), Label(reference: choice, title: "Use the green plan"), Label(reference: topic, title: "Café ☕️")]
        let response = try JSONDecoder().decode(DisplayTranslations.Response.self, from: data)
        let projection = try XCTUnwrap(DisplayTranslations.project(response, language: "da", labels: labels))
        XCTAssertEqual(projection[task], "Ring til Alice")
        XCTAssertEqual(projection[choice], "Brug den grønne plan")
        XCTAssertEqual(projection[topic], "Kaffemøde ☕️")
        XCTAssertEqual(labels.map(\.reference), [task, choice, topic])
        XCTAssertEqual(labels.map(\.title), ["Call Alice", "Use the green plan", "Café ☕️"])
    }

    func testRequestSendsVersionedReferencesWithoutClientProse() throws {
        let request = DisplayTranslations.Request(language: "da", items: [task, choice, topic])
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any])
        let items = try XCTUnwrap(json["items"] as? [[String: Any]])
        XCTAssertEqual(items[0].keys.sorted(), ["id", "kind", "version"])
        XCTAssertEqual(items[2].keys.sorted(), ["chapter_id", "end_offset", "id", "kind", "source_version", "start_offset", "transcript_version"])
        XCTAssertFalse(items.contains { $0["title"] != nil || $0["original_title"] != nil || $0["text"] != nil })
    }

    func testOlderVersionsOrChangedOriginalTitlesCannotReplaceCurrentLabels() {
        let current = Label(reference: task, title: "Call Alice tomorrow")
        let stale = Entry(reference: task, original_title: "Call Alice", title: "Ring til Alice", content_key: "old")
        XCTAssertNil(DisplayTranslations.project(.init(language: "da", items: [stale]), language: "da", labels: [current]))
        let oldVersion = Ref(kind: "inbox", id: task.id, version: 2)
        let old = Entry(reference: oldVersion, original_title: current.title, title: "Ring i morgen", content_key: "old")
        XCTAssertNil(DisplayTranslations.project(.init(language: "da", items: [old]), language: "da", labels: [current]))
    }

    func testSourceTopicRequiresExactOriginalSectionVersionsRangeAndChapter() {
        let label = Label(reference: topic, title: "Project review")
        let changed: [Ref] = [
            .init(kind: "source_topic", id: topic.id, source_version: "older", transcript_version: "transcript-4", start_offset: 8, end_offset: 35, chapter_id: "chapter-a"),
            .init(kind: "source_topic", id: topic.id, source_version: "source-2", transcript_version: "older", start_offset: 8, end_offset: 35, chapter_id: "chapter-a"),
            .init(kind: "source_topic", id: topic.id, source_version: "source-2", transcript_version: "transcript-4", start_offset: 9, end_offset: 35, chapter_id: "chapter-a"),
            .init(kind: "source_topic", id: topic.id, source_version: "source-2", transcript_version: "transcript-4", start_offset: 8, end_offset: 36, chapter_id: "chapter-a"),
            .init(kind: "source_topic", id: topic.id, source_version: "source-2", transcript_version: "transcript-4", start_offset: 8, end_offset: 35, chapter_id: "other")
        ]
        for reference in changed {
            let response = DisplayTranslations.Response(language: "da", items: [.init(reference: reference, original_title: label.title, title: "Projektgennemgang", content_key: "key")])
            XCTAssertNil(DisplayTranslations.project(response, language: "da", labels: [label]))
        }
    }

    func testMixedBatchFailsClosedForWrongLanguageOrderingOrIncompleteResponse() {
        let labels = [Label(reference: task, title: "Call Alice"), Label(reference: choice, title: "Use green")]
        let entries = [Entry(reference: task, original_title: labels[0].title, title: "Ring til Alice", content_key: "a"),
            Entry(reference: choice, original_title: labels[1].title, title: "Brug grøn", content_key: "b")]
        XCTAssertNil(DisplayTranslations.project(.init(language: "de", items: entries), language: "da", labels: labels))
        XCTAssertNil(DisplayTranslations.project(.init(language: "da", items: Array(entries.reversed())), language: "da", labels: labels))
        XCTAssertNil(DisplayTranslations.project(.init(language: "da", items: [entries[0]]), language: "da", labels: labels))
        XCTAssertNil(DisplayTranslations.project(.init(language: "da", items: [entries[0], entries[0]]), language: "da", labels: [labels[0], labels[0]]))
        XCTAssertNil(DisplayTranslations.project(.init(language: "da", items: entries), language: "", labels: labels))
    }

    func testProjectionIdentityChangesForOriginalLanguageAndCanonicalEdits() {
        let labels = [Label(reference: task, title: "Call Alice")]
        let current = DisplayTranslations.identity(language: "da", labels: labels)
        XCTAssertNotEqual(current, DisplayTranslations.identity(language: nil, labels: labels))
        XCTAssertNotEqual(current, DisplayTranslations.identity(language: "de", labels: labels))
        XCTAssertNotEqual(current, DisplayTranslations.identity(language: "da", labels: [.init(reference: task, title: "Call Bob")]))
        XCTAssertNotEqual(current, DisplayTranslations.identity(language: "da", labels: [.init(reference: .init(kind: "inbox", id: task.id, version: 4), title: "Call Alice")]))
    }
}

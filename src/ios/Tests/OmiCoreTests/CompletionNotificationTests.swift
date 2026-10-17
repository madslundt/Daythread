import XCTest
@testable import OmiCore

final class CompletionNotificationTests: XCTestCase {
    private func event(_ sequence: Int, id: UUID = UUID(), target: String = "{\"job_id\":\"EB66476B-B8D5-484B-B696-D6EA7A322166\"}") throws -> CompletionNotification {
        let json = """
        {"id":"\(id)","sequence":\(sequence),"kind":"translation","title":"Danish translation ready","body":"Team meeting · 17 October · Danish","target":\(target),"created_at":"2026-10-17T10:00:00Z"}
        """
        return try JSONDecoder().decode(CompletionNotification.self, from: Data(json.utf8))
    }

    func testPushBeforeFeedDoesNotSkipEarlierEventsOrDuplicateBanner() throws {
        var inbox = CompletionInbox()
        let pushed = try event(9)
        XCTAssertTrue(inbox.receive(pushed))
        XCTAssertEqual(inbox.cursor, 0)
        XCTAssertTrue(inbox.receive(try event(8)))
        XCTAssertFalse(inbox.receive(pushed))
        inbox.advanceCursor(to: 9)
        inbox.advanceCursor(to: 8)
        XCTAssertEqual(inbox.cursor, 9)
        XCTAssertEqual(inbox.items.map(\.sequence), [9, 8])
        let restored = try JSONDecoder().decode(CompletionInbox.self, from: JSONEncoder().encode(inbox))
        XCTAssertTrue(restored.items.isEmpty, "Persist transport IDs without private preview text")
        var reopened = restored
        XCTAssertFalse(reopened.receive(pushed))
        XCTAssertEqual(reopened.items, [pushed], "Validated server history is restored without a new banner")
    }

    func testArchiveInvalidationClearsPreviewsWithoutReplayingConsumedEvents() throws {
        var inbox = CompletionInbox()
        let original = try event(1)
        inbox.receive(original)
        inbox.advanceCursor(to: 1)
        inbox.clearPreviews()
        XCTAssertTrue(inbox.items.isEmpty)
        XCTAssertEqual(inbox.cursor, 1)
        XCTAssertFalse(inbox.receive(original))
    }

    func testPushScopeRequiresBothProfileAndArchiveInstallation() {
        XCTAssertTrue(CompletionInbox.acceptsPush(profile: "default", installation: "archive-a", currentProfile: "default", currentInstallation: "archive-a"))
        XCTAssertFalse(CompletionInbox.acceptsPush(profile: "default", installation: "archive-b", currentProfile: "default", currentInstallation: "archive-a"))
        XCTAssertFalse(CompletionInbox.acceptsPush(profile: "other", installation: "archive-a", currentProfile: "default", currentInstallation: "archive-a"))
        XCTAssertFalse(CompletionInbox.acceptsPush(profile: "", installation: "", currentProfile: "", currentInstallation: ""))
    }

    func testTargetsDecodeScheduledRecapAndVersionedWeeklyReview() throws {
        XCTAssertEqual(try event(1, target: "{\"day\":\"2026-10-17\"}").target.day, "2026-10-17")
        let weekly = try event(2, target: "{\"week_start\":\"2026-10-12\",\"version_id\":\"version-1\"}")
        XCTAssertEqual(weekly.target.weekStart, "2026-10-12")
        XCTAssertEqual(weekly.target.versionID, "version-1")
    }
}

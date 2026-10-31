import XCTest
@testable import OmiCore

final class CompletionNotificationTests: XCTestCase {
    private func event(_ sequence: Int, id: UUID = UUID(), target: String = "{\"job_id\":\"EB66476B-B8D5-484B-B696-D6EA7A322166\"}") throws -> CompletionNotification {
        let json = """
        {"id":"\(id)","sequence":\(sequence),"kind":"translation","title":"Danish translation ready","body":"Team meeting · 17 October · Danish","target":\(target),"created_at":"2026-10-17T10:00:00Z"}
        """
        return try JSONDecoder().decode(CompletionNotification.self, from: Data(json.utf8))
    }

    func testPhysicalClickLeaseWaitsForBothHandoffsInEitherOrder() {
        for order: [ClickExecutionState.Part] in [[.confirmation, .haptic], [.haptic, .confirmation]] {
            var state = ClickExecutionState()
            XCTAssertFalse(state.complete(order[0]))
            XCTAssertFalse(state.complete(order[0]), "A duplicate callback cannot finish the other handoff")
            XCTAssertTrue(state.complete(order[1]))
            XCTAssertTrue(state.finished)
            XCTAssertFalse(state.complete(order[1]))
            XCTAssertFalse(state.expire(), "A finished lease must never end twice")
        }
    }
    func testPhysicalClickLeaseExpiryReleasesPendingWorkExactlyOnce() {
        var state = ClickExecutionState()
        XCTAssertFalse(state.complete(.haptic))
        XCTAssertTrue(state.expire())
        XCTAssertTrue(state.finished)
        XCTAssertFalse(state.expire())
        XCTAssertFalse(state.complete(.confirmation), "A late OS callback cannot reopen or end an expired lease again")
    }

    func testClickNotificationsDefaultOnAndKeepExplicitOptOutAcrossReload() throws {
        let suite = "daythread-click-notification-tests-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        XCTAssertTrue(ClickNotification.enabled(in: defaults))
        defaults.set(false, forKey: ClickNotification.preferenceKey)
        XCTAssertFalse(ClickNotification.enabled(in: try XCTUnwrap(UserDefaults(suiteName: suite))))
        defaults.set(true, forKey: ClickNotification.preferenceKey)
        XCTAssertTrue(ClickNotification.enabled(in: defaults))
    }

    func testLocalClickClassificationPreservesForegroundSystemPresentationWithoutOpeningResults() {
        let identifier = ClickNotification.identifier()
        XCTAssertTrue(ClickNotification.isAcknowledgement(identifier: identifier, marker: ClickNotification.marker, isRemote: false))
        XCTAssertFalse(ClickNotification.isAcknowledgement(identifier: identifier, marker: ClickNotification.marker, isRemote: true))
        XCTAssertFalse(ClickNotification.isAcknowledgement(identifier: identifier, marker: nil, isRemote: false))
        XCTAssertFalse(ClickNotification.isAcknowledgement(identifier: "backend-result", marker: ClickNotification.marker, isRemote: false))
        let jobID = UUID(uuidString: "EB66476B-B8D5-484B-B696-D6EA7A322166")!
        let replacementID = ClickNotification.identifier(jobID: jobID)
        XCTAssertEqual(replacementID, "wearable-action:eb66476b-b8d5-484b-b696-d6ea7a322166")
        XCTAssertEqual(replacementID.utf8.count, 52)
        XCTAssertTrue(ClickNotification.isAcknowledgement(identifier: replacementID, marker: ClickNotification.marker, isRemote: false))
        XCTAssertFalse(ClickNotification.isAcknowledgement(identifier: replacementID, marker: nil, isRemote: true), "An APNs result sharing the identifier must never be cleared or routed as a click acknowledgement")
        XCTAssertNotEqual(ClickNotification.identifier(), ClickNotification.identifier())
    }

    func testClickFeedbackReportsQueuedActionDurationAndActualState() {
        XCTAssertEqual(ClickNotification.title(for: .single), "Single click registered")
        XCTAssertEqual(ClickNotification.title(for: .double), "Double click registered")
        XCTAssertEqual(ClickActionFeedback.queued(.summarize, seconds: 30).message, "Summary queued for the last 30 seconds.")
        XCTAssertEqual(ClickActionFeedback.queued(.answer, seconds: 120).message, "Answer queued for the last 120 seconds.")
        XCTAssertEqual(ClickActionFeedback.queued(.translate, seconds: 15).message, "Translation queued for the last 15 seconds.")
        XCTAssertEqual(ClickActionFeedback.noAction.message, "No action assigned.")
        XCTAssertEqual(ClickActionFeedback.alreadyMarked(seconds: 45).message, "Already marked nearby. Another flag is available in 45 seconds.")
        XCTAssertEqual(ClickActionFeedback.meetingStarted(provisionalSeconds: 60).message, "Meeting hint started · provisional for 60 seconds")
        XCTAssertEqual(ClickActionFeedback.meetingEnded.message, "Meeting hint ended")
        XCTAssertEqual(ClickActionFeedback.shortMeetingIgnored.message, "Short meeting ignored. Audio is kept.")
        XCTAssertEqual(ClickActionFeedback.capturePaused.message, "Recording paused on this iPhone.")
        XCTAssertEqual(ClickActionFeedback.captureResumed.message, "Recording resumed on this iPhone.")
        XCTAssertEqual(ClickActionFeedback.failed("Storage is full").message, "Could not complete the click action: Storage is full")
    }

    func testRegisteredClickDecodesEvenWhenNoActionOrCooldownSuppressesExecution() throws {
        let single = try XCTUnwrap(WearableClick(Data([1, 0, 0, 0])))
        let double = try XCTUnwrap(WearableClick(Data([2, 0, 0, 0])))
        XCTAssertEqual(single.action(single: .none, double: .answer), .none)
        XCTAssertEqual(double.action(single: .none, double: .answer), .answer)
        var policy = GesturePolicy(lastAcceptedMark: Date())
        XCTAssertNil(policy.resolve(Data([1, 0, 0, 0]), at: Date()))
        XCTAssertEqual(single, .single, "The physical click can still receive an honest cooldown acknowledgement")
        for code: UInt8 in [0, 3, 4, 5, 255] { XCTAssertNil(WearableClick(Data([code, 0, 0, 0]))) }
        XCTAssertNil(WearableClick(Data([1])))
        XCTAssertNil(WearableClick(Data([1, 1, 0, 0])))
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

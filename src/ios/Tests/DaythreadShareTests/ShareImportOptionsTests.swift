import XCTest
import UIKit
import OmiCore

@MainActor
final class ShareImportOptionsTests: XCTestCase {
    func testMissingDatePreventsImportUntilTheDisplayedTimeIsConfirmed() async throws {
        let controller = ShareViewController()
        controller.endpoints = BackendEndpointConfiguration(endpoints: [.init(address: "https://example.invalid")])
        controller.profileID = "test-profile"
        let url = URL(fileURLWithPath: "/tmp/recording.wav")
        let editor = ShareRecordingTimeView(name: "Recording", suggestion: nil)
        controller.stagedURLs = [url]
        controller.timeEditors = [url: editor]
        editor.onChanged = { controller.updateImportAvailability() }
        controller.updateImportAvailability()
        XCTAssertFalse(controller.saveButton.isEnabled)

        let confirm = try XCTUnwrap(editor.arrangedSubviews.compactMap { $0 as? UIButton }.first)
        confirm.sendActions(for: .touchUpInside)
        XCTAssertTrue(controller.saveButton.isEnabled)
        controller.busy = true
        controller.updateImportAvailability()
        XCTAssertFalse(controller.saveButton.isEnabled)
    }

    func testEmbeddedRecordingStartIsEditableAndRetryLocksIt() async throws {
        let start = Date(timeIntervalSince1970: 1_790_000_000)
        let suggestion = RecordingDateSuggestion(date: start, reference: .start,
                                                 source: "Embedded recording date", needsConfirmation: false)
        let editor = ShareRecordingTimeView(name: "Plaud.mp3", suggestion: suggestion)
        XCTAssertEqual(editor.recordedAt, start)
        XCTAssertEqual(editor.reference, .start)
        XCTAssertTrue(editor.isConfirmed)
        let picker = try XCTUnwrap(editor.arrangedSubviews.compactMap { $0 as? UIDatePicker }.first)
        let reference = try XCTUnwrap(editor.arrangedSubviews.compactMap { $0 as? UISegmentedControl }.first)
        picker.date = start.addingTimeInterval(-3600)
        picker.sendActions(for: .valueChanged)
        reference.selectedSegmentIndex = 0
        reference.sendActions(for: .valueChanged)
        XCTAssertEqual(editor.recordedAt, start.addingTimeInterval(-3600))
        XCTAssertEqual(editor.reference, .end)
        editor.locked = true
        editor.setEnabled(true)
        XCTAssertFalse(picker.isEnabled, "Retry must keep the accepted capture time frozen")
        XCTAssertFalse(reference.isEnabled)
    }

    func testExportAndFilesystemDateSuggestionsRequireReview() async {
        for source in ["Embedded creation date", "File modification date"] {
            let suggestion = RecordingDateSuggestion(date: Date().addingTimeInterval(-3600), reference: .end,
                                                     source: source, needsConfirmation: true)
            let editor = ShareRecordingTimeView(name: "Plaud.wav", suggestion: suggestion)
            XCTAssertFalse(editor.isConfirmed)
            XCTAssertTrue(editor.arrangedSubviews.compactMap { $0 as? UILabel }.contains { $0.text?.contains(source) == true })
        }
    }

    func testTagsMenuAllowsCreatingATagWhenArchiveHasNoTags() async throws {
        let controller = ShareViewController()
        controller.refreshOptionMenus()
        let actions = try XCTUnwrap(controller.tagsButton.menu).children.compactMap { $0 as? UIAction }
        XCTAssertTrue(actions.contains { $0.title == "Create tag…" })
        controller.pendingTagNames = ["Work"]
        controller.refreshOptionMenus()
        let selected = controller.tagsButton.menu?.children.compactMap { $0 as? UIAction }.first { $0.title == "Work" }
        XCTAssertEqual(selected?.state, .on)
    }
}

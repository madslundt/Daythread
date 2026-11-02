import XCTest
import SwiftUI
import Vision
import OmiCore
@testable import Daythread

@MainActor
final class ChatMarkdownTests: XCTestCase {
    private let answer = """
    **Weekend summary**

    ### Weekend activities

    There is **no direct evidence** for the latest weekend.

    * Discussed work and projects.
    * Reviewed the recordings.
      * Followed up on experiments.

    1. Check the diary.
    2. Ask a follow-up.

    > Based on recorded evidence.

    Read the [diary](https://example.com) for details.
    """

    func testSavedAnswerRendersHeadingsListsAndEmphasisInLightAndDarkMode() throws {
        let message = JSONValue.object(["role": .string("assistant"), "content": .string(answer)])
        for scheme in [ColorScheme.light, .dark] {
            let image = try render(ChatMessageView(message: message).environment(\.colorScheme, scheme), width: 320)
            let text = try recognizedText(image)
            XCTAssertTrue(text.contains("Weekend activities"), text)
            XCTAssertTrue(text.contains("Discussed work and projects."), text)
            XCTAssertTrue(text.contains("Followed up on experiments."), text)
            XCTAssertFalse(text.contains("###"), text)
            XCTAssertFalse(text.contains("**"), text)
            XCTAssertFalse(text.contains("* Discussed"), text)
            XCTAssertFalse(text.contains("](https://"), text)
            attach(image, name: "chat-answer-320-\(scheme)")
        }
    }

    func testStreamingDraftKeepsEarlierFormattingWithIncompleteMarkdown() async throws {
        for suffix in ["\n\nA **partial", "\n\n```swift\nlet count = 1"] {
            // ImageRenderer omits UIKit-backed scroll views (including code blocks).
            let image = try await renderHosted(ChatMarkdownText(content: answer + suffix), width: 320)
            let text = try recognizedText(image)
            XCTAssertTrue(text.contains("Weekend activities"), text)
            XCTAssertFalse(text.contains("###"), text)
            XCTAssertTrue(text.contains(suffix.contains("partial") ? "partial" : "count"), text)
        }
    }

    func testAnswerSupportsDynamicType() throws {
        let normal = try render(ChatMarkdownText(content: answer).environment(\.dynamicTypeSize, .large), width: 320)
        let enlarged = try render(ChatMarkdownText(content: answer).environment(\.dynamicTypeSize, .accessibility1), width: 320)
        XCTAssertGreaterThan(enlarged.height, normal.height)
        let text = try recognizedText(enlarged)
        XCTAssertTrue(text.contains("Weekend activities"), text)
        attach(enlarged, name: "chat-answer-320-accessibility")
    }

    func testCodeAndTablesRenderWithinPhoneWidth() async throws {
        let content = """
        ## Technical notes

        Use `count` for the total.

        ```swift
        let count = 1
        ```

        | Activity | Status |
        | --- | --- |
        | Planning | Done |
        """
        let image = try await renderHosted(ChatMarkdownText(content: content), width: 320)
        let text = try recognizedText(image)
        XCTAssertTrue(text.contains("count"), text)
        XCTAssertTrue(text.contains("Planning"), text)
        XCTAssertTrue(text.contains("Done"), text)
        XCTAssertFalse(text.contains("```"), text)
        XCTAssertFalse(text.contains("| ---"), text)
    }

    func testNativeSelectionCanSpanParagraphsAndUnicode() throws {
        let content = "First paragraph.\n\nSecond paragraph: æøå 🧵.\n\nLast paragraph."
        let view = ChatSelectableText.textView(content: content)
        let fragment = "paragraph.\n\nSecond paragraph: æøå 🧵."
        view.selectedRange = (content as NSString).range(of: fragment)
        let selected = try XCTUnwrap(view.selectedTextRange)
        XCTAssertEqual(view.text(in: selected), fragment)
        XCTAssertFalse(view.isEditable, "Selecting a message must not edit the saved content")
        view.selectAll(nil)
        XCTAssertEqual(view.text(in: try XCTUnwrap(view.selectedTextRange)), content)
    }

    func testSelectingMarkdownShowsReadableTextAndPreservesLiteralUserMessages() {
        let content = "## Heading\n\nFirst **bold** paragraph.\n\nSecond paragraph with `code`."
        let plain = ChatMessageText.selectionText(content, markdown: true)
        XCTAssertTrue(plain.contains("Heading"))
        XCTAssertTrue(plain.contains("First bold paragraph."))
        XCTAssertTrue(plain.contains("Second paragraph with code."))
        XCTAssertFalse(plain.contains("##"))
        XCTAssertFalse(plain.contains("**"))
        XCTAssertEqual(ChatMessageText.selectionText(content, markdown: false), content)
    }

    func testCopyMessagePreservesTheEntireOriginalMarkdownWithoutTruncation() {
        let pasteboard = UIPasteboard.withUniqueName()
        defer { UIPasteboard.remove(withName: pasteboard.name) }
        let content = answer + "\n\n" + String(repeating: "More text: æøå 🧵.\n", count: 1000) + "\nLast line."
        ChatMessageText.copy(content, to: pasteboard)
        XCTAssertEqual(pasteboard.string, content)
    }

    func testSelectionScreenUsesOneScrollableTextDocument() async throws {
        let controller = UIHostingController(rootView: ChatTextSelectionView(content: answer, markdown: true))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 320, height: 640))
        window.rootViewController = controller
        window.isHidden = false
        defer { window.isHidden = true }
        controller.view.frame = window.bounds
        controller.view.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(100))
        func textViews(in view: UIView) -> [UITextView] {
            (view as? UITextView).map { [$0] } ?? view.subviews.flatMap { textViews(in: $0) }
        }
        let views = textViews(in: controller.view)
        XCTAssertEqual(views.count, 1, "Selection must span every paragraph in one document")
        let view = try XCTUnwrap(views.first)
        XCTAssertTrue(view.isScrollEnabled)
        XCTAssertTrue(view.text.contains("Read the diary for details."))
        view.selectAll(nil)
        XCTAssertEqual(view.text(in: try XCTUnwrap(view.selectedTextRange)), view.text)
        let snapshot = UIGraphicsImageRenderer(size: window.bounds.size).image { _ in
            controller.view.drawHierarchy(in: controller.view.bounds, afterScreenUpdates: true)
        }
        attach(try XCTUnwrap(snapshot.cgImage), name: "chat-select-text-320")
    }

    private func render(_ content: some View, width: CGFloat) throws -> CGImage {
        let renderer = ImageRenderer(content: content
            .frame(width: width, alignment: .leading)
            .padding(16)
            .background(Color(uiColor: .systemBackground)))
        renderer.scale = 2
        return try XCTUnwrap(renderer.cgImage)
    }

    private func recognizedText(_ image: CGImage) throws -> String {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        try VNImageRequestHandler(cgImage: image).perform([request])
        return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
    }

    private func renderHosted(_ content: some View, width: CGFloat) async throws -> CGImage {
        let controller = UIHostingController(rootView: content.padding(16).background(Color(uiColor: .systemBackground)))
        var size = controller.sizeThatFits(in: CGSize(width: width, height: .greatestFiniteMagnitude))
        XCTAssertLessThanOrEqual(size.width, width + 0.5, "Markdown must not widen the chat")
        let window = UIWindow(frame: CGRect(origin: .zero, size: size))
        window.rootViewController = controller
        window.isHidden = false
        defer { window.isHidden = true }
        controller.view.frame = window.bounds
        controller.view.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(100))
        size = controller.sizeThatFits(in: CGSize(width: width, height: .greatestFiniteMagnitude))
        window.frame.size = size
        controller.view.frame = window.bounds
        controller.view.layoutIfNeeded()
        let image = UIGraphicsImageRenderer(size: size).image { _ in
            controller.view.drawHierarchy(in: controller.view.bounds, afterScreenUpdates: true)
        }
        attach(try XCTUnwrap(image.cgImage), name: "chat-streaming-draft")
        return try XCTUnwrap(image.cgImage)
    }

    private func attach(_ image: CGImage, name: String) {
        let attachment = XCTAttachment(image: UIImage(cgImage: image))
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

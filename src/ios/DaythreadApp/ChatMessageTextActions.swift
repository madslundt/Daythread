import SwiftUI
import MarkdownUI

enum ChatMessageText {
    static func selectionText(_ content: String, markdown: Bool) -> String {
        markdown ? MarkdownContent(content).renderPlainText() : content
    }

    @MainActor
    static func copy(_ content: String, to pasteboard: UIPasteboard = .general) {
        pasteboard.string = content
    }
}

private struct ChatTextSelection: Identifiable {
    let id = UUID()
    let content: String
    let markdown: Bool
}

struct ChatMessageTextActions: View {
    let content: String
    var markdown = true
    @State private var selection: ChatTextSelection?

    var body: some View {
        HStack(spacing: 0) {
            Button { selection = ChatTextSelection(content: content, markdown: markdown) } label: {
                Image(systemName: "text.cursor").frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.text("Select text"))
            .accessibilityIdentifier("chat.selectText")
            ChatCopyMessageButton(content: content)
        }
        .buttonStyle(.borderless)
        .sheet(item: $selection) { selection in
            ChatTextSelectionView(content: selection.content, markdown: selection.markdown)
        }
    }
}

struct ChatCopyMessageButton: View {
    let content: String
    @State private var copied = false

    var body: some View {
        Button {
            ChatMessageText.copy(content)
            copied = true
        } label: {
            Image(systemName: copied ? "checkmark" : "doc.on.doc").frame(width: 44, height: 44)
        }
        .accessibilityLabel(copied ? L10n.text("Copied") : L10n.text("Copy message"))
        .accessibilityIdentifier("chat.copyMessage")
        .task(id: copied) {
            guard copied else { return }
            do { try await Task.sleep(for: .seconds(2)); copied = false } catch { }
        }
    }
}

struct ChatTextSelectionView: View {
    let content: String
    let markdown: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var selectAllRequest = 0

    var body: some View {
        NavigationStack {
            ChatSelectableText(content: ChatMessageText.selectionText(content, markdown: markdown), selectAllRequest: selectAllRequest)
                .background(Theme.canvas)
                .navigationTitle(L10n.text("Select text"))
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button(L10n.text("Done")) { dismiss() }
                    }
                    ToolbarItemGroup(placement: .bottomBar) {
                        Button(L10n.text("Select all")) { selectAllRequest += 1 }
                            .accessibilityIdentifier("chat.selectAllText")
                        Spacer()
                        ChatCopyMessageButton(content: content)
                    }
                }
        }.presentationDragIndicator(.visible)
    }
}

/// One native text document lets selection handles cross Markdown paragraphs and lists.
/// Its text is a snapshot, so streaming updates cannot move an active selection.
struct ChatSelectableText: UIViewRepresentable {
    let content: String
    var selectAllRequest = 0

    final class Coordinator {
        var lastSelectAllRequest = 0
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    static func textView(content: String) -> UITextView {
        let view = UITextView()
        view.isEditable = false
        view.isSelectable = true
        view.font = .preferredFont(forTextStyle: .body)
        view.adjustsFontForContentSizeCategory = true
        view.textColor = .label
        view.backgroundColor = .clear
        view.textContainerInset = UIEdgeInsets(top: 20, left: 16, bottom: 20, right: 16)
        view.textContainer.lineFragmentPadding = 0
        view.dataDetectorTypes = [.link]
        view.isFindInteractionEnabled = true
        view.accessibilityLabel = L10n.text("Message text")
        view.accessibilityIdentifier = "chat.messageText"
        view.text = content
        return view
    }

    func makeUIView(context: Context) -> UITextView { Self.textView(content: content) }

    func updateUIView(_ view: UITextView, context: Context) {
        if view.text != content { view.text = content }
        if selectAllRequest != context.coordinator.lastSelectAllRequest {
            context.coordinator.lastSelectAllRequest = selectAllRequest
            view.becomeFirstResponder()
            view.selectAll(nil)
        }
    }
}

import SwiftUI
import OmiCore

struct ChatBottomPreference: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
}

struct ChatQuestionBubble: View {
    let text: String
    var body: some View {
        HStack {
            Spacer(minLength: 32)
            VStack(alignment: .leading, spacing: 6) {
                Text(L10n.text("You")).font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
                Text(text).textSelection(.enabled)
            }.padding(16).background(Theme.accent.opacity(0.09), in: RoundedRectangle(cornerRadius: 22))
        }.frame(maxWidth: .infinity, alignment: .trailing)
    }
}

struct ChatMessageView: View {
    let message: JSONValue
    @State private var sourcesExpanded = false
    @State private var evidenceExpanded = false

    var body: some View {
        if message["role"].text == "user" {
            ChatQuestionBubble(text: message["content"].text)
        } else {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Label("Daythread", systemImage: "sparkle").font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
                    Spacer()
                    ShareLink(item: message["content"].text) {
                        Image(systemName: "square.and.arrow.up").frame(width: 44, height: 44)
                    }.accessibilityLabel(L10n.text("Share answer"))
                }
                if message["stale"] == .bool(true) {
                    Label(L10n.text("Historical answer: transcript evidence changed. Ask again for a current answer."), systemImage: "clock.arrow.circlepath")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text(.init(message["content"].text)).textSelection(.enabled).lineSpacing(4)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if !message["retrieval_details"]["notice"].text.isEmpty {
                    Text(L10n.key(message["retrieval_details"]["notice"].text)).font(.caption).foregroundStyle(.secondary)
                }
                if !message["sources"].array.isEmpty {
                    DisclosureGroup(isExpanded: $sourcesExpanded) {
                        SourceList(sources: message["sources"].array)
                    } label: {
                        Label(L10n.text("Recordings (\(message["sources"].array.count))"), systemImage: "waveform")
                            .font(.subheadline.weight(.medium))
                    }
                }
                DisclosureGroup(isExpanded: $evidenceExpanded) {
                    if evidenceExpanded {
                        EvidenceFeedbackView(outputID: message["id"].text, evidence: message["evidence"], outputText: message["content"].text)
                            .padding(.top, 8)
                    }
                } label: {
                    Text(L10n.text("Evidence & feedback")).font(.caption)
                }.tint(.secondary)
            }.padding(16).background(Theme.surface, in: RoundedRectangle(cornerRadius: 22))
                .contextMenu {
                    Button { UIPasteboard.general.string = message["content"].text } label: {
                        Label(L10n.text("Copy"), systemImage: "doc.on.doc")
                    }
                }
        }
    }
}

struct ChatWelcomeView: View {
    let choose: (String) -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Image(systemName: "bubble.left.and.text.bubble.right")
                .font(.system(size: 30, weight: .light)).foregroundStyle(Theme.accent)
                .frame(width: 64, height: 64).background(Theme.accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 20))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 10) {
                Text(L10n.text("Ask your days.")).font(.largeTitle.weight(.bold))
                Text(L10n.text("Explore a conversation, decision, or commitment. Answers link back to your recorded evidence."))
                    .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            VStack(spacing: 10) {
                ForEach([L10n.text("What did we decide?"), L10n.text("What did I say I would follow up on?")], id: \.self) { suggestion in
                    Button { choose(suggestion) } label: {
                        HStack(spacing: 12) {
                            Text(suggestion).multilineTextAlignment(.leading).foregroundStyle(.primary)
                            Spacer(minLength: 0)
                            Image(systemName: "arrow.up.left").foregroundStyle(Theme.accent).accessibilityHidden(true)
                        }.padding(16).frame(maxWidth: .infinity, minHeight: 52)
                            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 16))
                    }.buttonStyle(.plain)
                }
            }
        }.padding(.vertical, 24)
    }
}

struct ChatHistoryView: View {
    let sessions: [JSONValue]
    let selectedID: String?
    let loading: Bool
    let error: String?
    let refresh: () async -> Void
    let select: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""

    private var matches: [JSONValue] {
        let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return sessions.filter { text.isEmpty || $0["title"].text.localizedStandardContains(text) }
    }

    var body: some View {
        NavigationStack {
            List {
                if let error { ErrorNote(message: error) }
                if matches.isEmpty {
                    ContentUnavailableView(query.isEmpty ? L10n.text("No conversations yet") : L10n.text("No matching conversations"), systemImage: "bubble.left.and.bubble.right")
                        .listRowBackground(Color.clear)
                }
                ForEach(matches, id: \.self["id"].text) { session in
                    Button { select(session["id"].text) } label: {
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(session["title"].text.isEmpty ? L10n.text("Conversation") : session["title"].text)
                                    .font(.body.weight(.medium)).foregroundStyle(.primary).lineLimit(2)
                                if let date = AppModel.date(session["updated_at"].text) {
                                    Text(date, style: .date).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            Spacer(minLength: 0)
                            if session["id"].text == selectedID {
                                Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.accent)
                            }
                        }.padding(.vertical, 6).contentShape(Rectangle())
                    }.buttonStyle(.plain).disabled(loading)
                }
            }.omiScreen().navigationTitle(L10n.text("Conversation history")).navigationBarTitleDisplayMode(.inline)
                .refreshable { await refresh() }
                .searchable(text: $query, prompt: L10n.text("Search conversations"))
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L10n.text("Done")) { dismiss() } } }
        }.presentationDragIndicator(.visible)
    }
}

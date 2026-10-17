import SwiftUI

struct WeeklyReviewContent: View {
    let version: JSONValue
    let sources: [JSONValue]
    let selectSource: (JSONValue) -> Void
    private let categories = [
        (key: "decision", title: L10n.text("Supported decisions"), icon: "arrow.triangle.branch"),
        (key: "commitment", title: L10n.text("Commitments needing attention"), icon: "checkmark.circle"),
        (key: "discussion", title: L10n.text("Recurring discussions"), icon: "bubble.left.and.bubble.right"),
        (key: "change", title: L10n.text("Changes since last week"), icon: "arrow.up.right")
    ]
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        ForEach(categories, id: \.key) { category in
            Section {
                let items = version["items"].array.filter { $0["category"].text == category.key }
                if items.isEmpty {
                    Text(L10n.text("No supported items found in the recorded evidence.")).font(.subheadline).foregroundStyle(.secondary)
                }
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    WeeklyReviewItem(item: item, sources: sources, selectSource: selectSource)
                }
            } header: { Label(category.title, systemImage: category.icon) }
        }
    }
}

private struct WeeklyReviewItem: View {
    let item: JSONValue
    let sources: [JSONValue]
    let selectSource: (JSONValue) -> Void
    @State private var showEvidence = false
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            Text(item["claim"].text).font(.headline).textSelection(.enabled)
            if item["category"].text == "commitment" {
                Label(item["status"].text == "explicitly_open" ? L10n.text("Explicitly still open") : L10n.text("Follow-through unconfirmed"), systemImage: "clock")
                    .font(.caption.weight(.medium)).foregroundStyle(Theme.accent)
            }
            Text(item["qualification"].text).font(.subheadline).foregroundStyle(.secondary).textSelection(.enabled)
            DisclosureGroup(L10n.text("Recorded evidence"), isExpanded: $showEvidence) {
                if showEvidence {
                    ForEach(Array(item["evidence"].array.enumerated()), id: \.offset) { _, ref in
                        VStack(alignment: .leading, spacing: 10) {
                            Text(ref["quote"].text).font(.subheadline).textSelection(.enabled)
                                .padding(.leading, 12)
                                .overlay(alignment: .leading) { Rectangle().fill(Theme.accent.opacity(0.3)).frame(width: 3) }
                            if let source = sources.first(where: { $0["id"].text == ref["source_id"].text }) {
                                Button { selectSource(source) } label: { Label(L10n.text("Inspect original recording"), systemImage: "waveform") }
                                    .buttonStyle(.borderless)
                            } else {
                                Text(L10n.text("Source is no longer available in the current archive.")).font(.caption).foregroundStyle(.secondary)
                            }
                        }.padding(.vertical, 8)
                    }
                }
            }.font(.subheadline)
        }.padding(.vertical, 8)
    }
}

struct WeeklyCoverageRow: View {
    let title: String
    let coverage: JSONValue
    private func rounded(_ key: String, divisor: Double = 1) -> Int {
        guard case .number(let value) = coverage[key] else { return 0 }
        return Int((value / divisor).rounded())
    }
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline.weight(.medium))
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 18) { metrics }
                VStack(alignment: .leading, spacing: 8) { metrics }
            }
            DisclosureGroup(L10n.text("Coverage details")) {
                Text(L10n.text("\(coverage["pending_chunks"].integer) pending · \(coverage["empty_chunks"].integer) empty or silent · \(coverage["expired_chunks"].integer) expired transcripts"))
                Text(L10n.text("\(coverage["capture_gaps"].integer) known capture gaps (\(rounded("known_gap_seconds")) seconds) · \(coverage["audio_unavailable"].integer) sources without audio"))
            }.font(.caption).foregroundStyle(.secondary)
        }.padding(.vertical, 5)
    }
    private var metrics: some View {
        Group {
            Label(L10n.text("\(coverage["speech_days"].integer) \(coverage["speech_days"].integer == 1 ? L10n.text("day") : L10n.text("days")) with speech"), systemImage: "calendar")
            Label(coverage["speech_sources"].integer == 1 ? L10n.text("1 source") : L10n.text("\(coverage["speech_sources"].integer) sources"), systemImage: "waveform")
            Label(L10n.text("\(rounded("captured_seconds", divisor: 60)) min"), systemImage: "clock")
        }.font(.caption).foregroundStyle(.secondary)
    }
}

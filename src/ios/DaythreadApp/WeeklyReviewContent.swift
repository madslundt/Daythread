import SwiftUI

struct WeeklyReviewContent: View {
    let version: JSONValue
    let sources: [JSONValue]
    let selectSource: (JSONValue) -> Void
    private let categories = [
        (key: "decision", title: "Supported decisions", icon: "arrow.triangle.branch"),
        (key: "commitment", title: "Commitments needing attention", icon: "checkmark.circle"),
        (key: "discussion", title: "Recurring discussions", icon: "bubble.left.and.bubble.right"),
        (key: "change", title: "Changes since last week", icon: "arrow.up.right")
    ]
    var body: some View {
        ForEach(categories, id: \.key) { category in
            Section {
                let items = version["items"].array.filter { $0["category"].text == category.key }
                if items.isEmpty {
                    Text("No supported items found in the recorded evidence.").font(.subheadline).foregroundStyle(.secondary)
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
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(item["claim"].text).font(.headline).textSelection(.enabled)
            if item["category"].text == "commitment" {
                Label(item["status"].text == "explicitly_open" ? "Explicitly still open" : "Follow-through unconfirmed", systemImage: "clock")
                    .font(.caption.weight(.medium)).foregroundStyle(Theme.accent)
            }
            Text(item["qualification"].text).font(.subheadline).foregroundStyle(.secondary).textSelection(.enabled)
            DisclosureGroup("Recorded evidence", isExpanded: $showEvidence) {
                if showEvidence {
                    ForEach(Array(item["evidence"].array.enumerated()), id: \.offset) { _, ref in
                        VStack(alignment: .leading, spacing: 10) {
                            Text(ref["quote"].text).font(.subheadline).textSelection(.enabled)
                                .padding(.leading, 12)
                                .overlay(alignment: .leading) { Rectangle().fill(Theme.accent.opacity(0.3)).frame(width: 3) }
                            if let source = sources.first(where: { $0["id"].text == ref["source_id"].text }) {
                                Button { selectSource(source) } label: { Label("Inspect original recording", systemImage: "waveform") }
                                    .buttonStyle(.borderless)
                            } else {
                                Text("Source is no longer available in the current archive.").font(.caption).foregroundStyle(.secondary)
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
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline.weight(.medium))
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 18) { metrics }
                VStack(alignment: .leading, spacing: 8) { metrics }
            }
            DisclosureGroup("Coverage details") {
                Text("\(coverage["pending_chunks"].integer) pending · \(coverage["empty_chunks"].integer) empty or silent · \(coverage["expired_chunks"].integer) expired transcripts")
                Text("\(coverage["capture_gaps"].integer) known capture gaps (\(rounded("known_gap_seconds")) seconds) · \(coverage["audio_unavailable"].integer) sources without audio")
            }.font(.caption).foregroundStyle(.secondary)
        }.padding(.vertical, 5)
    }
    private var metrics: some View {
        Group {
            Label("\(coverage["speech_days"].integer) \(coverage["speech_days"].integer == 1 ? "day" : "days") with speech", systemImage: "calendar")
            Label("\(coverage["speech_sources"].integer) \(coverage["speech_sources"].integer == 1 ? "source" : "sources")", systemImage: "waveform")
            Label("\(rounded("captured_seconds", divisor: 60)) min", systemImage: "clock")
        }.font(.caption).foregroundStyle(.secondary)
    }
}

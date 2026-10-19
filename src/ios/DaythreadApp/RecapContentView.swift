import SwiftUI
import OmiCore

struct RecapContentView: View {
    let recap: JSONValue
    let day: String
    var onCorrect: (() -> Void)? = nil
    @Environment(AppModel.self) private var model
    @State private var checks: Set<String> = []
    private var version: String {
        [recap["output_id"].text, recap["generated_at"].text, "unversioned"].first { !$0.isEmpty }!
    }
    private var identity: String { model.archiveIdentity + "|" + day + "|" + version }
    private var groups: [RecapPresentation.Group] { RecapPresentation.groups(recap["summary"].text, day: day) }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        SummaryTranslationView(output: recap) {
            original
        }
        .buttonStyle(.borderless)
        .disclosureGroupStyle(RecapDisclosureStyle())
        .id(identity)
        .onChange(of: identity, initial: true) { _, _ in
            checks = RecapCheckStore().checks(archive: model.archiveIdentity, day: day, version: version)
        }
    }
    private var original: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(Array(groups.enumerated()), id: \.offset) { _, group in
                RecapTopicView(group: group, day: day, version: version, sources: recap["sources"].array,
                               checks: $checks, onCheck: saveChecks, onCorrect: onCorrect)
            }
            if UUID(uuidString: version) != nil {
                ImportanceButton(target: .init(kind: "recap", id: version), label: L10n.text("day recap"))
                    .font(.subheadline)
            }
        }
    }
    private func saveChecks() {
        RecapCheckStore().save(checks, archive: model.archiveIdentity, day: day, version: version)
    }
}

private struct RecapTopicView: View {
    let group: RecapPresentation.Group
    let day: String
    let version: String
    let sources: [JSONValue]
    @Binding var checks: Set<String>
    let onCheck: () -> Void
    let onCorrect: (() -> Void)?
    @State private var expanded = false
    @State private var copied = false
    @State private var asking = false
    private var title: String { (group.title ?? L10n.text("Day recap")).replacingOccurrences(of: "**", with: "") }
    private var plain: String { group.text.replacingOccurrences(of: "**", with: "") }
    private var prompt: String { L10n.text("Tell me more about this topic from my day recap: \(title)\n\(plain)") }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        if group.title == nil { content }
        else {
            DisclosureGroup(isExpanded: $expanded) {
                if expanded { content.padding(.top, 12) }
            } label: {
                VStack(alignment: .leading, spacing: 5) {
                    Text(title).font(.headline).foregroundStyle(.primary)
                    if !expanded { Text(plain).font(.subheadline).foregroundStyle(.secondary).lineLimit(2) }
                }.padding(.vertical, 5)
            }
            .onAppear { if group.hasActions { expanded = true } }
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 14) {
            if group.hasActions || group.title == nil {
                ForEach(Array(group.parts.enumerated()), id: \.offset) { _, part in
                    RecapPointView(part: part, topic: group.title == nil ? "" : title, day: day, version: version,
                                   sources: sources, checks: $checks, onCheck: onCheck, onCorrect: onCorrect)
                }
            } else { Text(.init(group.text)).textSelection(.enabled) }
            HStack(spacing: 18) {
                Button {
                    UIPasteboard.general.string = (group.title == nil ? "" : title + "\n\n") + plain
                    copied = true
                } label: { Label(copied ? L10n.text("Copied") : L10n.text("Copy"), systemImage: copied ? "checkmark" : "doc.on.doc") }
                    .accessibilityLabel(L10n.text("Copy summary: \(title)"))
                Button { asking = true } label: {
                    Label(L10n.text("Ask about this"), systemImage: "bubble.left.and.bubble.right")
                }
            }.buttonStyle(.borderless).font(.subheadline)
                .sheet(isPresented: $asking) { RecapQuestionView(day: day, question: prompt) }
            if UUID(uuidString: version) != nil, let heading = group.title {
                ImportanceButton(target: .init(kind: "recap", id: version, section: heading), label: title)
            }
            if !group.hasActions && group.title != nil {
                RecapEvidenceList(point: group.parts.map(\.text).joined(separator: "\n"), topic: title,
                                  sources: sources, onCorrect: onCorrect)
            }
        }
    }
}

private struct RecapPointView: View {
    let part: RecapPresentation.Part
    let topic: String
    let day: String
    let version: String
    let sources: [JSONValue]
    @Binding var checks: Set<String>
    let onCheck: () -> Void
    let onCorrect: (() -> Void)?
    @State private var expanded = false
    @State private var asking = false
    private var plain: String { RecapPresentation.readingText(part.text).replacingOccurrences(of: "**", with: "") }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 8) {
            if part.kind == .item {
                HStack(alignment: .top, spacing: 10) {
                    if part.action {
                        Button {
                            if checks.contains(part.text) { checks.remove(part.text) } else { checks.insert(part.text) }
                            onCheck()
                        } label: {
                            Image(systemName: checks.contains(part.text) ? "checkmark.circle.fill" : "circle")
                                .font(.title3).frame(minWidth: 44, minHeight: 44)
                        }.buttonStyle(.borderless)
                            .accessibilityLabel(L10n.text("Mark complete: ") + plain)
                            .accessibilityValue(checks.contains(part.text) ? L10n.text("Completed") : L10n.text("Not completed"))
                    }
                    DisclosureGroup(isExpanded: $expanded) {
                        if expanded { details.padding(.vertical, 8) }
                    } label: {
                        Text(.init(RecapPresentation.readingText(part.text)))
                            .strikethrough(part.action && checks.contains(part.text))
                            .fixedSize(horizontal: false, vertical: true)
                            .foregroundStyle(.primary).padding(.vertical, 8)
                    }.disclosureGroupStyle(RecapDisclosureStyle())
                }
            } else {
                Text(.init(RecapPresentation.readingText(part.text))).textSelection(.enabled)
                importance
            }
        }
    }
    private var details: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button { asking = true } label: { Label(L10n.text("Ask about this"), systemImage: "bubble.left.and.bubble.right") }
                .buttonStyle(.borderless)
                .sheet(isPresented: $asking) {
                    RecapQuestionView(day: day, question: L10n.text("Tell me more about this point from my day recap: ") + part.text)
                }
            importance
            RecapEvidenceList(point: part.text, topic: topic, sources: sources, onCorrect: onCorrect)
        }.font(.subheadline)
    }
    @ViewBuilder private var importance: some View {
        if UUID(uuidString: version) != nil {
            ImportanceButton(target: .init(kind: "recap", id: version, section: part.text), label: L10n.text("recap point: ") + plain)
        }
    }
}

private struct RecapEvidenceList: View {
    let point: String
    let topic: String
    let sources: [JSONValue]
    let onCorrect: (() -> Void)?
    @State private var expanded = false
    private var related: RecapPresentation.Matches {
        RecapPresentation.sources(for: point, in: sources.map { .init(id: $0["id"].text, text: $0["text"].text) })
    }
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        DisclosureGroup(L10n.text("Original passages"), isExpanded: $expanded) {
            if expanded {
                if related.ids.isEmpty {
                    Text(L10n.text("No linked or matching recordings for this topic.")).font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(related.basis == .citation ? L10n.text("Cited recordings · check the original context.") : L10n.text("Possible matching recordings · relevance is inferred from shared words."))
                        .font(.caption).foregroundStyle(.secondary)
                    ForEach(Array(sources.filter { related.ids.contains($0["id"].text) }.enumerated()), id: \.element["id"].text) { _, source in
                        RecapEvidenceView(source: source, point: point, topic: topic, onCorrect: onCorrect)
                    }
                }
            }
        }.font(.subheadline).disclosureGroupStyle(RecapDisclosureStyle())
    }
}

private struct RecapEvidenceView: View {
    let source: JSONValue
    let point: String
    let topic: String
    let onCorrect: (() -> Void)?
    @Environment(AppModel.self) private var model
    @State private var selected: SourceSelection?
    @State private var fresh: JSONValue?
    @State private var error: String?
    @State private var loading = false
    @State private var refresh = UUID()
    private var evidence: JSONValue { fresh ?? source }
    private var sections: [RecapPresentation.Section] {
        RecapPresentation.sections(for: point, topic: topic, text: evidence["text"].text,
            sections: evidence["sections"].array.map { .init(title: $0["title"].text, start: $0["start_offset"].integer, end: $0["end_offset"].integer) })
    }
    private var identity: String { [model.archiveIdentity, model.archiveRevision, source["id"].text, source["source_version"].text, refresh.uuidString].joined(separator: "|") }
    private var time: String {
        guard let date = AppModel.date(evidence["started_at"].text) else { return evidence["started_at"].text }
        return DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current).label(date, template: "d MMM HHmm")
    }
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            Text(time).font(.caption).foregroundStyle(.secondary)
            Text(sections.count == 1 ? sections[0].title : evidence["title"].text.isEmpty ? L10n.text("Recording") : evidence["title"].text).font(.headline)
            if loading { ProgressView(L10n.text("Checking original evidence…")) }
            ErrorNote(message: error)
            if error != nil { Button(L10n.text("Retry original evidence")) { refresh = UUID() } }
            if fresh != nil && evidence != .null {
                if sections.isEmpty {
                    Text(L10n.text("No specific topic passage identified. Open the full recording to inspect its context."))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    ForEach(sections, id: \.start) { section in
                        SourceTranscriptView(source: evidence, editing: false, startOffset: section.start, endOffset: section.end)
                            .padding(.leading, 12).overlay(alignment: .leading) { Rectangle().fill(Theme.accent.opacity(0.3)).frame(width: 3) }
                    }
                }
                ImportanceButton(target: .init(kind: "source", id: evidence["id"].text), label: "recording")
                Button { selected = SourceSelection(source: evidence) } label: { Label(L10n.text("Full recording & tools"), systemImage: "waveform") }
                    .buttonStyle(.borderless)
            }
        }.padding(.vertical, 10)
            .sheet(item: $selected) { selection in
                SourceDetail(source: selection.source, onCorrect: { refresh = UUID(); onCorrect?() })
            }
            .task(id: identity) {
                let requested = identity
                loading = true; error = nil; fresh = nil
                defer { if requested == identity { loading = false } }
                do {
                    let result = try await model.client().request("v1/transcripts/\(source["id"].text)")
                    guard !Task.isCancelled, requested == identity else { return }
                    fresh = result
                } catch {
                    guard !Task.isCancelled, requested == identity else { return }
                    self.error = L10n.error(error)
                    if (error as? BackendFailure)?.status == 404 { fresh = .null }
                }
            }
    }
}

private struct RecapQuestionView: View {
    let day: String
    let question: String
    @Environment(\.dismiss) private var dismiss
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            ChatView(initialScope: .day(day), initialQuestion: question)
                .toolbar { ToolbarItem(placement: .topBarTrailing) { Button(L10n.text("Close")) { dismiss() } } }
        }
    }
}

private struct RecapDisclosureStyle: DisclosureGroupStyle {
    func makeBody(configuration: Configuration) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.18)) { configuration.isExpanded.toggle() }
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    configuration.label.frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                        .rotationEffect(.degrees(configuration.isExpanded ? 90 : 0))
                        .accessibilityHidden(true)
                }.contentShape(Rectangle())
            }.buttonStyle(.plain)
                .accessibilityValue(configuration.isExpanded ? L10n.text("Expanded") : L10n.text("Collapsed"))
            if configuration.isExpanded { configuration.content }
        }
    }
}

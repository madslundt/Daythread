import SwiftUI
import OmiCore

struct RecapHistoryView: View {
    let day: String
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var versions: [JSONValue] = []
    @State private var loading = false
    @State private var exporting = false
    @State private var error: String?
    @State private var exportError: String?
    @State private var refresh = UUID()
    @State private var export: ArchiveMarkdownExport?
    @State private var operation: Task<Void, Never>?
    private var context: String { model.archiveIdentity + model.archiveRevision + day }
    private var loadKey: String { context + refresh.uuidString }
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }

    var body: some View {
        List {
            Section {
                Text(diary.date(for: day).map { diary.label($0, template: "EEEE d MMMM yyyy") } ?? day)
                    .font(.system(.title2, design: .serif))
                Button { exportMarkdown() } label: {
                    Label(exporting ? "Preparing export…" : "Export latest diary as Markdown", systemImage: "square.and.arrow.up")
                }.disabled(exporting)
                ErrorNote(message: exportError)
            }
            Section {
                if loading { ProgressView("Loading saved versions…") }
                ErrorNote(message: error)
                if error != nil { Button("Try again") { refresh = UUID() } }
                if !loading && error == nil && versions.isEmpty {
                    ContentUnavailableView("No saved versions", systemImage: "clock.arrow.circlepath", description: Text("Recaps are saved automatically when completed conversations are ready."))
                }
                ForEach(Array(versions.enumerated()), id: \.element["id"].text) { _, version in
                    NavigationLink {
                        SavedRecapVersionView(day: day, version: version)
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(timestamp(version["created_at"].text)).font(.headline)
                            Text(RecapPresentation.readingText(version["content"]["summary"].text).replacingOccurrences(of: "#", with: "").replacingOccurrences(of: "**", with: ""))
                                .font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                        }.padding(.vertical, 5)
                    }
                }
            } header: { Text("Saved versions") } footer: { Text("Each version preserves the summary as generated. Its original passages open the current transcript.") }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle("Recap history").navigationBarTitleDisplayMode(.inline)
            .task(id: loadKey) { await load() }
            .refreshable { refresh = UUID() }
            .onChange(of: scenePhase) { _, phase in if phase == .active { refresh = UUID() } }
            .onChange(of: context) { _, _ in operation?.cancel(); operation = nil; exporting = false; export?.remove(); export = nil }
            .onDisappear { operation?.cancel(); operation = nil; exporting = false }
            .sheet(item: $export) { ArchiveMarkdownShareSheet(export: $0) }
    }

    private func load() async {
        let key = loadKey
        loading = true; error = nil; versions = []
        defer { if key == loadKey { loading = false } }
        do {
            let value = try await model.client().request("v1/diary/\(day)/versions")
            guard !Task.isCancelled, key == loadKey else { return }
            versions = value["items"].array
        } catch { if !Task.isCancelled, key == loadKey { self.error = error.localizedDescription } }
    }
    private func exportMarkdown() {
        guard !exporting else { return }
        let key = context, requestedDay = day
        exporting = true; exportError = nil
        operation = Task {
            defer { if context == key { exporting = false; operation = nil } }
            do {
                let result = try await ArchiveMarkdownExport.prepare(client: model.client(), path: "v1/diary/\(requestedDay).md", name: requestedDay)
                guard !Task.isCancelled, key == context else { result.remove(); return }
                export = result
            } catch { if !Task.isCancelled, key == context { exportError = error.localizedDescription } }
        }
    }
    private func timestamp(_ text: String) -> String { AppModel.date(text).map { diary.label($0, template: "d MMM yyyy HHmmss") } ?? text }
}

private struct SavedRecapVersionView: View {
    let day: String
    let version: JSONValue
    @State private var copied = false
    var body: some View {
        List {
            Section {
                Text("Saved interpretation").font(.caption.weight(.medium)).foregroundStyle(Theme.accent)
                Text("This summary reflects the evidence available when it was generated. Source inspection shows current transcripts.")
                    .font(.subheadline).foregroundStyle(.secondary)
                if version["content"]["summary"].text.isEmpty { Text("No summary in this version.") }
                else {
                    RecapContentView(recap: version["content"], day: day)
                    Button {
                        UIPasteboard.general.string = RecapPresentation.readingText(version["content"]["summary"].text)
                        copied = true
                    } label: { Label(copied ? "Copied recap" : "Copy recap", systemImage: copied ? "checkmark" : "doc.on.doc") }
                        .buttonStyle(.borderless)
                }
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle("Saved recap").navigationBarTitleDisplayMode(.inline)
    }
}

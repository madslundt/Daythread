import SwiftUI
import OmiCore

struct CompletionNotificationsView: View {
    @Environment(CompletionNotifications.self) private var notifications
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            if notifications.items.isEmpty {
                ContentUnavailableView(L10n.text("No notifications yet"), systemImage: "bell", description: Text(L10n.text("Completed rewinds, recaps, translations and weekly reviews will appear here.")))
            }
            ForEach(notifications.items) { event in
                Button { notifications.open(event) } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(L10n.key(event.title)).font(.headline).foregroundStyle(.primary)
                        Text(L10n.key(event.body)).font(.subheadline).foregroundStyle(.secondary)
                        if let date = AppModel.date(event.createdAt) { Text(date, style: .relative).font(.caption).foregroundStyle(.secondary) }
                    }.padding(.vertical, 6)
                }.buttonStyle(.plain)
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle(L10n.text("Notifications"))
    }
}

struct CompletionResultView: View {
    let event: CompletionNotification
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @State private var job: JSONValue = .null
    @State private var source: JSONValue = .null
    @State private var error: String?
    @State private var loading = true
    @State private var playing = false
    @State private var recapFocus: RecapTopicFocus?
    @State private var contentLanguage: String?
    private var result: JSONValue { job["result"] }
    private var parameters: JSONValue { job["parameters"] }
    private var recapFilters: DayFilterScope {
        .init(tagID: parameters["tag_id"].text.isEmpty ? result["scope"]["tag_id"].text : parameters["tag_id"].text,
              zoneID: parameters["zone_id"].text.isEmpty ? result["scope"]["zone_id"].text : parameters["zone_id"].text)
    }
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            Group {
                if let week = event.target.weekStart, event.target.jobID == nil {
                    WeeklyReviewView(week: week, version: event.target.versionID ?? "")
                } else if let day = event.target.day, event.target.jobID == nil, let date = diary.date(for: day) {
                    DayView(date: date)
                } else if job["kind"].text == "recap", job["state"].text == "completed", recapFilters.isFiltered,
                          let date = diary.date(for: parameters["day"].text) {
                    // Scoped jobs are snapshots. Reopen the current scope so edits to
                    // tags, zones or original evidence cannot expose a stale narrative.
                    DayView(date: date, filters: recapFilters)
                } else {
                    savedResult
                }
            }
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button(L10n.text("Done")) { dismiss() } } }
        }
        .task(id: model.archiveIdentity + event.id.uuidString + String(describing: scenePhase)) {
            if scenePhase == .active { await load() }
        }
        .onDisappear { if playing { model.stopPlayback() } }
    }

    private var savedResult: some View {
        List {
            Section {
                Text(L10n.key(event.title)).font(.title2.weight(.semibold))
                Text(L10n.key(event.body)).foregroundStyle(.secondary)
                if loading { ProgressView(L10n.text("Opening saved result…")) }
                ErrorNote(message: error)
                if error != nil { Button(L10n.text("Try again")) { Task { await load() } } }
            }
            if job["state"].text == "failed" {
                Section(L10n.text("Could not finish")) { Text(job["error"].text.isEmpty ? L10n.text("This request failed. Return to the original recording or day to try again.") : L10n.key(job["error"].text)) }
            } else if job["state"].text == "completed" {
                if job["kind"].text == "wearable_action" {
                    Section {
                        if result["action"].text == "answer" {
                            Text(result["text"].text).textSelection(.enabled)
                        } else {
                            Text(.init(result["text"].text)).textSelection(.enabled)
                        }
                        Text(L10n.text("Last \(parameters["duration_seconds"].integer) seconds · ending \(parameters["end"].text)"))
                            .font(.caption).foregroundStyle(.secondary)
                        CompletenessView(value: result["completeness"])
                        if result["approximate_window"] == .bool(true) {
                            Text(L10n.text("Transcripts include whole overlapping recordings. Markers and meeting gestures are approximate context hints."))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if !result["sources"].array.isEmpty {
                        Section(L10n.text("Recorded evidence")) { SourceList(sources: result["sources"].array) }
                    }
                } else if ["translation", "summary_translation"].contains(job["kind"].text) {
                    Section(L10n.text("Translation")) {
                        let summary = job["kind"].text == "summary_translation"
                        let translation = result["translation"]
                        let language = translation["language"].text
                        if !language.isEmpty { Text(L10n.locale.localizedString(forLanguageCode: language) ?? language).font(.headline) }
                        if !translation["text"].text.isEmpty {
                            if summary { Text(.init(translation["text"].text)).textSelection(.enabled) }
                            else { Text(translation["text"].text).textSelection(.enabled) }
                        } else { Text(summary ? L10n.text("This summary is already in the requested language.") : L10n.text("This recording is already in the requested language.")) }
                        Text(summary ? L10n.text("The original summary is preserved.") : L10n.text("The original transcript is preserved.")).font(.caption).foregroundStyle(.secondary)
                    }
                    if source != .null { Section(L10n.text("Original recording")) { SourceList(sources: [source]) } }
                } else {
                    if !result["summary"].text.isEmpty {
                        Section(L10n.text("Saved summary")) {
                            if job["kind"].text == "recap" {
                                RecapContentView(recap: result, day: parameters["day"].text, onFocusChange: { recapFocus = $0 }, onContentLanguageChange: { contentLanguage = $0 })
                            } else {
                                SummaryTranslationView(output: result) {
                                    Text(.init(result["summary"].text)).textSelection(.enabled)
                                }
                            }
                        }
                    }
                    if job["kind"].text == "recap", let recapFocus {
                        DayAtGlanceSections(day: parameters["day"].text, topicSources: recapFocus.sourceIDs,
                            topicPassages: recapFocus.passages, topicVersions: recapFocus.versions, topicTitle: recapFocus.displayTitle ?? recapFocus.title,
                            contentLanguage: contentLanguage).id(recapFocus.id)
                    }
                    if job["kind"].text == "rewind" {
                        Section(L10n.text("Rewind window")) {
                            Text(L10n.text("Last \(parameters["minutes"].integer) minutes · ending \(parameters["end"].text)")).font(.caption).foregroundStyle(.secondary)
                            Button(playing ? L10n.text("Stop audio") : L10n.text("Play saved window"), systemImage: playing ? "stop.circle" : "play.circle") {
                                if playing { model.stopPlayback(); playing = false }
                                else { Task { await playRewind() } }
                            }
                            CompletenessView(value: result["completeness"])
                        }
                    }
                    if !result["sources"].array.isEmpty { Section(L10n.text("Recorded evidence")) { SourceList(sources: result["sources"].array) } }
                    else if result["summary"].text.isEmpty { Section { Text(L10n.text("No transcribed speech was available for this result.")).foregroundStyle(.secondary) } }
                }
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle(L10n.text("Saved result")).navigationBarTitleDisplayMode(.inline)
    }

    private func load() async {
        guard let id = event.target.jobID else { loading = false; return }
        let scope = model.archiveIdentity
        loading = true; error = nil
        defer { if scope == model.archiveIdentity { loading = false } }
        do {
            let client = try model.client()
            repeat {
                let value = try await client.request("v1/jobs/" + id.uuidString.lowercased())
                guard scope == model.archiveIdentity, !Task.isCancelled else { return }
                job = value
                if !["queued", "running"].contains(value["state"].text) { break }
                try await Task.sleep(for: .seconds(2))
            } while !Task.isCancelled
            if job["kind"].text == "translation", !parameters["source_id"].text.isEmpty {
                let value = try await client.request("v1/transcripts/" + parameters["source_id"].text)
                guard scope == model.archiveIdentity, !Task.isCancelled else { return }
                source = value["source"] == .null ? value : value["source"]
            }
        } catch { if scope == model.archiveIdentity, !Task.isCancelled { self.error = L10n.error(error) } }
    }

    private func playRewind() async {
        let scope = model.archiveIdentity
        do {
            let url = try await model.client().download("v1/rewind/audio", query: ["minutes": String(parameters["minutes"].integer), "end": parameters["end"].text])
            guard scope == model.archiveIdentity, !Task.isCancelled else { return }
            model.play(url); playing = true
        } catch { if scope == model.archiveIdentity, !Task.isCancelled { self.error = L10n.error(error) } }
    }
}

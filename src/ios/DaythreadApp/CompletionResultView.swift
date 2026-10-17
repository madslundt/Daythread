import SwiftUI
import OmiCore

struct CompletionNotificationsView: View {
    @Environment(CompletionNotifications.self) private var notifications
    var body: some View {
        List {
            if notifications.items.isEmpty {
                ContentUnavailableView("No notifications yet", systemImage: "bell", description: Text("Completed rewinds, recaps, translations and weekly reviews will appear here."))
            }
            ForEach(notifications.items) { event in
                Button { notifications.open(event) } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(event.title).font(.headline).foregroundStyle(.primary)
                        Text(event.body).font(.subheadline).foregroundStyle(.secondary)
                        if let date = AppModel.date(event.createdAt) { Text(date, style: .relative).font(.caption).foregroundStyle(.secondary) }
                    }.padding(.vertical, 6)
                }.buttonStyle(.plain)
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle("Notifications")
    }
}

struct CompletionResultView: View {
    let event: CompletionNotification
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var job: JSONValue = .null
    @State private var source: JSONValue = .null
    @State private var error: String?
    @State private var loading = true
    @State private var playing = false
    private var result: JSONValue { job["result"] }
    private var parameters: JSONValue { job["parameters"] }
    private var diary: DiaryCalendar { DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current) }

    var body: some View {
        NavigationStack {
            Group {
                if let week = event.target.weekStart, event.target.jobID == nil {
                    WeeklyReviewView(week: week, version: event.target.versionID ?? "")
                } else if let day = event.target.day, event.target.jobID == nil, let date = diary.date(for: day) {
                    DayView(date: date)
                } else {
                    savedResult
                }
            }
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
        .task(id: model.archiveIdentity + event.id.uuidString) { await load() }
        .onDisappear { if playing { model.player?.stop(); model.playbackStatus = "" } }
    }

    private var savedResult: some View {
        List {
            Section {
                Text(event.title).font(.title2.weight(.semibold))
                Text(event.body).foregroundStyle(.secondary)
                if loading { ProgressView("Opening saved result…") }
                ErrorNote(message: error)
                if error != nil { Button("Try again") { Task { await load() } } }
            }
            if job["state"].text == "failed" {
                Section("Could not finish") { Text(job["error"].text.isEmpty ? "This request failed. Return to the original recording or day to try again." : job["error"].text) }
            } else if job["state"].text == "completed" {
                if job["kind"].text == "translation" {
                    Section("Translation") {
                        let translation = result["translation"]
                        let language = translation["language"].text
                        if !language.isEmpty { Text(Locale.current.localizedString(forLanguageCode: language) ?? language).font(.headline) }
                        if !translation["text"].text.isEmpty { Text(translation["text"].text).textSelection(.enabled) }
                        else { Text("This recording is already in the requested language.") }
                        Text("The original transcript is preserved.").font(.caption).foregroundStyle(.secondary)
                    }
                    if source != .null { Section("Original recording") { SourceList(sources: [source]) } }
                } else {
                    if !result["summary"].text.isEmpty {
                        Section("Saved summary") {
                            if job["kind"].text == "recap" {
                                RecapContentView(recap: result, day: parameters["day"].text)
                            } else { Text(.init(result["summary"].text)).textSelection(.enabled) }
                        }
                    }
                    if job["kind"].text == "rewind" {
                        Section("Rewind window") {
                            Text("Last \(parameters["minutes"].integer) minutes · ending \(parameters["end"].text)").font(.caption).foregroundStyle(.secondary)
                            Button(playing ? "Stop audio" : "Play saved window", systemImage: playing ? "stop.circle" : "play.circle") {
                                if playing { model.player?.stop(); playing = false }
                                else { Task { await playRewind() } }
                            }
                            CompletenessView(value: result["completeness"])
                        }
                    }
                    if !result["sources"].array.isEmpty { Section("Recorded evidence") { SourceList(sources: result["sources"].array) } }
                    else if result["summary"].text.isEmpty { Section { Text("No transcribed speech was available for this result.").foregroundStyle(.secondary) } }
                }
            }
        }.listStyle(.insetGrouped).omiScreen().navigationTitle("Saved result").navigationBarTitleDisplayMode(.inline)
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
        } catch { if scope == model.archiveIdentity, !Task.isCancelled { self.error = error.localizedDescription } }
    }

    private func playRewind() async {
        let scope = model.archiveIdentity
        do {
            let url = try await model.client().download("v1/rewind/audio", query: ["minutes": String(parameters["minutes"].integer), "end": parameters["end"].text])
            guard scope == model.archiveIdentity, !Task.isCancelled else { return }
            model.play(url); playing = true
        } catch { if scope == model.archiveIdentity, !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

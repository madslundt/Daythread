import SwiftUI
import OmiCore

struct MergedRecordingView: View {
    let group: JSONValue
    let day: String
    var onCorrect: (() -> Void)?
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var expanded = false
    @State private var recording: JSONValue = .null
    @State private var playback = RecordingPlayback()
    @State private var error: String?
    @State private var refresh = UUID()
    @State private var requestID = UUID()
    @State private var selectedTopic = ""

    private var recordingID: String { group["recording_id"].text }
    private var identity: String {
        recordingID + model.archiveIdentity + model.archiveRevision + refresh.uuidString
    }
    private var topics: [JSONValue] { recording["sections"].array }
    private var topicIndex: Int { topics.firstIndex { $0["chapter_id"].text == selectedTopic } ?? 0 }

    private func selectTopic(_ index: Int) {
        guard topics.indices.contains(index) else { return }
        selectedTopic = topics[index]["chapter_id"].text
        if case .number(let seconds) = topics[index]["start_seconds"] { playback.seek(to: seconds) }
    }

    private func time(_ value: String) -> String {
        AppModel.date(value).map {
            DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current)
                .label($0, template: "HHmm")
        } ?? value
    }

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            NavigationLink(L10n.text("Conversation tags")) {
                ConversationTagsView(groupID: group["id"].text, day: day, onChanged: onCorrect)
            }
            ErrorNote(message: error)
            if recording == .null {
                if error == nil { ProgressView(L10n.text("Loading recordings…")) }
                else { Button(L10n.text("Try again")) { refresh = UUID() } }
            } else {
                if recording["audio_available"] == .bool(true) {
                    RecordingAudioPlayer(playback: playback, retry: { refresh = UUID() })
                } else if recording["state"].text == "done" {
                    Text(recording["audio_availability"].text == "missing"
                         ? L10n.text("The archive expects audio, but the file is missing.")
                         : L10n.text("Audio expired under retention; transcript remains available."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                if case .string(let text) = recording["text"] {
                    if !topics.isEmpty {
                        Picker(L10n.text("Topics"), selection: Binding(get: { topicIndex }, set: selectTopic)) {
                            ForEach(Array(topics.enumerated()), id: \.offset) { index, topic in
                                Text(topic["title"].text).tag(index)
                            }
                        }
                        HStack {
                            Button(L10n.text("Previous")) { selectTopic(topicIndex - 1) }
                                .disabled(topicIndex == 0)
                            Spacer()
                            Text("\(topicIndex + 1) / \(topics.count)").font(.caption.monospacedDigit())
                            Spacer()
                            Button(L10n.text("Next")) { selectTopic(topicIndex + 1) }
                                .disabled(topicIndex == topics.count - 1)
                        }
                        let section = topics[topicIndex]
                        Text(String(String.UnicodeScalarView(text.unicodeScalars
                            .dropFirst(section["start_offset"].integer)
                            .prefix(max(0, section["end_offset"].integer - section["start_offset"].integer)))))
                            .textSelection(.enabled)
                    }
                    if recording["sections"].array.isEmpty {
                        Text(text).textSelection(.enabled)
                    } else {
                        DisclosureGroup(L10n.text("Full transcript")) {
                            Text(text).textSelection(.enabled)
                        }
                    }
                } else {
                    Text(recording["state"].text == "expired"
                         ? L10n.text("Transcript expired under your retention policy.")
                         : L10n.text("Processing audio"))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                Text(group["title"].text).font(.body.weight(.medium)).foregroundStyle(.primary)
                Text("\(time(group["started_at"].text))–\(time(group["ended_at"].text)) · "
                     + (group["topic_count"].integer > 0
                        ? L10n.text("\(group["topic_count"].integer) topics") : L10n.text("Recording")))
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(.vertical, 5)
        }
        .task(id: "\(identity).\(expanded).\(scenePhase)") {
            guard expanded, scenePhase == .active else { return }
            await load()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                await load()
            }
        }
        .onChange(of: expanded) { _, open in if !open { playback.close() } }
        .onChange(of: identity) { _, _ in recording = .null; playback.close() }
        .onChange(of: playback.position) { _, seconds in
            guard topics.allSatisfy({ if case .number = $0["start_seconds"] { true } else { false } }) else { return }
            if let topic = topics.last(where: { if case .number(let start) = $0["start_seconds"] { start <= seconds } else { false } }) {
                selectedTopic = topic["chapter_id"].text
            }
        }
        .onDisappear { requestID = UUID(); playback.close() }
    }

    private func load() async {
        let requested = identity, token = UUID()
        requestID = token
        do {
            let result = try await model.client().request("v1/recordings/\(recordingID)")
            guard !Task.isCancelled, requested == identity, requestID == token else { return }
            recording = result; error = nil
            if result["audio_available"] == .bool(true) {
                if !playback.ready && !playback.loading {
                    let start = AppModel.date(result["started_at"].text)
                    let end = AppModel.date(result["ended_at"].text)
                    await playback.load(sourceID: recordingID,
                        durationHint: start.flatMap { start in end.map { $0.timeIntervalSince(start) } } ?? 0,
                        model: model, audioPath: "v1/recordings/\(recordingID)/audio")
                    guard !Task.isCancelled, requested == identity, requestID == token else { return }
                    if let topic = topics.first(where: { $0["chapter_id"].text == selectedTopic }),
                       case .number(let seconds) = topic["start_seconds"] {
                        playback.seek(to: seconds)
                    }
                }
            } else { playback.close() }
        } catch {
            guard !Task.isCancelled, requested == identity, requestID == token else { return }
            self.error = L10n.error(error)
        }
    }
}

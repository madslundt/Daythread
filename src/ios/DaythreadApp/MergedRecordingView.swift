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
    @State private var error: String?
    @State private var refresh = UUID()
    @State private var requestID = UUID()

    private var recordingID: String { group["recording_id"].text }
    private var identity: String {
        recordingID + model.archiveIdentity + model.archiveRevision + refresh.uuidString
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
            } else if expanded {
                MergedRecordingContentView(recording: recording,
                    audioPath: "v1/recordings/\(recordingID)/audio",
                    onRetry: { refresh = UUID() })
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
        .onChange(of: identity) { _, _ in recording = .null }
        .onDisappear { requestID = UUID() }
    }

    private func load() async {
        let requested = identity, token = UUID()
        requestID = token
        do {
            let result = try await model.client().request("v1/recordings/\(recordingID)")
            guard !Task.isCancelled, requested == identity, requestID == token else { return }
            recording = result; error = nil
        } catch {
            guard !Task.isCancelled, requested == identity, requestID == token else { return }
            self.error = L10n.error(error)
        }
    }
}

/// Shared by finished file uploads and nearby live Omi chunks.
struct MergedRecordingContentView: View {
    let recording: JSONValue
    let audioPath: String
    var audioQuery: [String: String] = [:]
    var onRetry: () -> Void
    @Environment(AppModel.self) private var model
    @State private var playback = RecordingPlayback()
    @State private var speakerFilters = RecordingSpeakerFilters()
    @State private var selectedTopic: Int?
    @State private var refresh = UUID()
    @State private var selectionBusy = false
    @State private var selectionError: String?
    @State private var selectionOperation: Task<Void, Never>?
    @State private var transcriptOpen = false
    @State private var originalsOpen = false

    private var topics: [JSONValue] { recording["sections"].array }
    private var transcriptSelection: RecordingTranscriptSelection? {
        guard let data = try? JSONEncoder().encode(recording["transcript_selection"]) else { return nil }
        return try? JSONDecoder().decode(RecordingTranscriptSelection.self, from: data)
    }
    private var selectedTranscript: String? {
        guard transcriptSelection?.state == .selected else { return nil }
        return transcriptSelection?.transcript?.text
    }
    private var speakerUtterances: [RecordingSpeakerUtterance]? {
        guard let text = selectedTranscript,
              let evidence = transcriptSelection?.transcript?.speakerEvidence else { return nil }
        return evidence.exactUtterances(for: text)
    }

    private var originalReading: RecordingSpeakerReading {
        speakerReading(recording["transcript_turns"].array, scope: "original:" + recording["transcript_version"].text)
    }
    private var selectedScope: String { "selected:" + (transcriptSelection?.resultVersion ?? "") }
    private var selectedReading: RecordingSpeakerReading { speakerReading(selectedTurns, scope: selectedScope) }

    private func speakerReading(_ turns: [JSONValue], scope: String) -> RecordingSpeakerReading {
        RecordingSpeakerReading(turns: turns, scope: recording["id"].text + ":" + scope,
            filters: speakerFilters, canPlay: recording["audio_available"] == .bool(true),
            canSkip: recording["mixed_audio"] != .bool(true) && recording["audio_availability"].text != "partial")
    }

    private var playbackIdentity: String {
        model.archiveIdentity + model.archiveRevision + audioPath
        + audioQuery.keys.sorted().map { "\($0)=\(audioQuery[$0] ?? "")" }.joined(separator: "&")
        + (recording["audio_version"].text.isEmpty ? recording["transcript_version"].text : recording["audio_version"].text)
        + String(recording["audio_available"] == .bool(true))
        + refresh.uuidString
    }
    private var durationHint: TimeInterval {
        if case .number(let seconds) = recording["duration_seconds"] { seconds } else { 0 }
    }
    private func time(_ value: String) -> String {
        AppModel.date(value).map {
            DiaryCalendar(timeZone: TimeZone(identifier: model.serverTimeZoneIdentifier ?? "") ?? .current)
                .label($0, template: "HHmmss")
        } ?? value
    }

    private var seek: ((Double) -> Void)? {
        guard recording["audio_available"] == .bool(true) else { return nil }
        return { playback.seek(to: $0) }
    }

    private func chooseTranscript(_ resultVersion: String?) {
        guard let selection = transcriptSelection, !selectionBusy else { return }
        let address = model.archiveIdentity
        let recordingID = recording["id"].text
        let client: BackendClient
        do { client = try model.client().snapshot() }
        catch { selectionError = L10n.error(error); return }
        let request = RecordingTranscriptSelectionRequest(
            expectedVersion: selection.selectionVersion, resultVersion: resultVersion,
        )
        selectionBusy = true; selectionError = nil
        selectionOperation = Task {
            defer { selectionBusy = false; selectionOperation = nil }
            do {
                let body = try request.jsonObject()
                guard !Task.isCancelled, model.archiveIdentity == address else { return }
                _ = try await client.request(
                    "v1/recordings/\(recordingID)/transcript-selection",
                    method: "PUT", body: body,
                )
                guard !Task.isCancelled, model.archiveIdentity == address else { return }
                onRetry()
            } catch {
                guard !Task.isCancelled, model.archiveIdentity == address else { return }
                selectionError = L10n.error(error)
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if recording["audio_available"] == .bool(true) {
                RecordingAudioPlayer(playback: playback, retry: { refresh = UUID(); onRetry() })
                if recording["audio_availability"].text == "partial" {
                    Text(L10n.text("Recording audio is incomplete")).font(.caption).foregroundStyle(.secondary)
                }
            } else if recording["state"].text == "done" {
                Text(recording["audio_availability"].text == "transcript_only"
                     ? L10n.text("Imported transcript only; original audio is unavailable.")
                     : recording["audio_availability"].text == "missing"
                     ? L10n.text("The archive expects audio, but the file is missing.")
                     : L10n.text("Audio expired under retention; transcript remains available."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !recording["recording_summary"].text.isEmpty {
                Text(L10n.text("Recording summary")).font(.headline).accessibilityAddTraits(.isHeader)
                Text(recording["recording_summary"].text).textSelection(.enabled)
                if selectedTranscript != nil {
                    Text(L10n.text("Based on the original part transcripts.")).font(.caption).foregroundStyle(.secondary)
                }
            }
            if !topics.isEmpty { topicSummaries }
            if let text = selectedTranscript {
                HStack {
                    Text(L10n.text("Using selected full-recording transcript"))
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer(minLength: 8)
                    Button(L10n.text("Use original part transcripts")) {
                        chooseTranscript(nil)
                    }.disabled(selectionBusy)
                }
                if selectionBusy { ProgressView() }
                if let selectionError { ErrorNote(message: selectionError) }
                DisclosureGroup(L10n.text("Full transcript"), isExpanded: $transcriptOpen) {
                    if transcriptOpen {
                        RecordingTranscriptTurns(text: text, turns: selectedTurns, onSeek: seek, speakerReading: selectedReading)
                        if case .string(let original) = recording["text"] {
                            DisclosureGroup(L10n.text("Original part transcripts"), isExpanded: $originalsOpen) {
                                if originalsOpen {
                                    RecordingTranscriptTurns(text: original, turns: recording["transcript_turns"].array,
                                        onSeek: seek, speakerReading: originalReading)
                                }
                            }
                        }
                    }
                }
            } else {
                if transcriptSelection?.state == .stale {
                    Text(L10n.text("The selected transcript is out of date. Showing the original part transcripts."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                if recording["recording_transcription"]["state"].text == "ready",
                   !recording["recording_transcription"]["version"].text.isEmpty {
                    HStack {
                        Text(L10n.text("A full-recording transcript is ready"))
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer(minLength: 8)
                        Button(L10n.text("Use full-recording transcript")) {
                            chooseTranscript(recording["recording_transcription"]["version"].text)
                        }.disabled(selectionBusy)
                    }
                }
                if selectionBusy { ProgressView() }
                if let selectionError { ErrorNote(message: selectionError) }
                if case .string(let text) = recording["text"] {
                    DisclosureGroup(L10n.text("Full transcript"), isExpanded: $transcriptOpen) {
                        if transcriptOpen {
                            RecordingTranscriptTurns(text: text, turns: recording["transcript_turns"].array,
                                onSeek: seek, speakerReading: originalReading)
                        }
                    }
                } else {
                    Text(recording["state"].text == "expired"
                         ? L10n.text("Transcript expired under your retention policy.")
                         : L10n.text("Processing audio"))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if recording["text"] != .null && recording["state"].text == "processing" {
                Text(L10n.text("Processing audio")).font(.caption).foregroundStyle(.secondary)
            }
            if !recording["interruptions"].array.isEmpty {
                DisclosureGroup(L10n.text("Playback joins available audio; gaps and overlaps are marked here.")) {
                    ForEach(Array(recording["interruptions"].array.enumerated()), id: \.offset) { _, boundary in
                        Text("\(time(boundary["previous_end"].text)) → \(time(boundary["next_start"].text))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .task(id: playbackIdentity) {
            guard recording["audio_available"] == .bool(true) else { playback.close(); return }
            await playback.load(sourceID: recording["id"].text,
                durationHint: durationHint,
                model: model, audioPath: audioPath, audioQuery: audioQuery)
        }
        .onChange(of: selectedTranscript != nil && speakerFilters.scope == recording["id"].text + ":" + selectedScope
            ? selectedReading.intervals : originalReading.intervals, initial: true) { _, intervals in playback.setSkipIntervals(intervals) }
        .onDisappear { playback.close(); selectionOperation?.cancel() }
    }

    private var topicSummaries: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(selectedTranscript == nil ? L10n.text("Topics") : L10n.text("Chapters in the original transcript"))
                .font(.headline).accessibilityAddTraits(.isHeader)
            ForEach(Array(topics.enumerated()), id: \.offset) { index, topic in
                DisclosureGroup(topic["title"].text, isExpanded: Binding(get: { selectedTopic == index },
                    set: { selectedTopic = $0 ? index : nil })) {
                    if selectedTopic == index {
                        TopicReadingView {
                            TopicSummaryText(text: topic["summary"].text)
                        } transcript: {
                            topicTranscript(topic)
                        }
                        if let seek, case .number(let seconds) = topic["start_seconds"] {
                            Button { seek(seconds) } label: { Label(L10n.text("Listen from this topic"), systemImage: "play.fill") }
                                .buttonStyle(.borderless)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder private func topicTranscript(_ topic: JSONValue) -> some View {
        let text = recording["text"].text
        let scalars = text.unicodeScalars
        let start = topic["start_offset"].integer, end = topic["end_offset"].integer
        if start >= 0, end > start, end <= scalars.count {
            RecordingTranscriptTurns(text: String(String.UnicodeScalarView(scalars.dropFirst(start).prefix(end - start))),
                turns: RecordingTranscriptTurns.passageTurns(text: text, turns: recording["transcript_turns"].array,
                    start: start, end: end), onSeek: seek, speakerReading: originalReading)
        }
    }

    private var selectedTurns: [JSONValue] {
        (speakerUtterances ?? []).map { turn in
            .object(["text": .string(turn.text), "attribution": .string(turn.attribution),
                     "speaker_name": turn.speakerName.map(JSONValue.string) ?? .null,
                     "speaker_label": turn.speakerLabel.map(JSONValue.string) ?? .null,
                     "speaker_id": turn.speakerID.map(JSONValue.string) ?? .null,
                     "start_offset": .number(Double(turn.startOffset)), "end_offset": .number(Double(turn.endOffset)),
                     "start": turn.start.map(JSONValue.number) ?? .null, "end": turn.end.map(JSONValue.number) ?? .null])
        }
    }
}

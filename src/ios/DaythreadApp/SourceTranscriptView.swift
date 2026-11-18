import SwiftUI
import Observation

/// Translation is a separate, on-demand reading of the original evidence.
struct SourceTranscriptView: View {
    let source: JSONValue
    let editing: Bool
    var startOffset: Int? = nil
    var endOffset: Int? = nil
    var onSeek: ((Double) -> Void)? = nil
    var speakerReading: RecordingSpeakerReading? = nil
    var previewPlayback: RecordingPlayback? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var translation: JSONValue = .null
    @AppStorage("lastTranslationLanguage") private var savedTranslationLanguage = ""
    @State private var language = ""
    @State private var availableLanguages: [String] = []
    @State private var languageAnalysis: JSONValue = .null
    @State private var recommendedLanguage = ""
    @State private var showingTranslation = false
    @State private var alreadyInTargetLanguage = false
    @State private var busy = false
    @State private var loaded = false
    @State private var error: String?
    @State private var requestID = UUID()
    @State private var refresh = UUID()
    @State private var generation: Task<Void, Never>?
    @State private var loadedScope = ""
    @State private var refreshedTurns: [JSONValue]?

    private var sourceID: String { source["id"].text }
    private var languageName: String { L10n.locale.localizedString(forLanguageCode: language) ?? language }
    private var hasTranslation: Bool {
        source["state"].text == "done" && !source["text"].text.isEmpty &&
            !alreadyInTargetLanguage && !translation["text"].text.isEmpty && translation["language"].text == language &&
            translation["transcript_version"] == source["transcript_version"]
    }
    private var scopeKey: String {
        [model.archiveIdentity, model.archiveRevision, sourceID, source["transcript_version"].text,
         source["source_version"].text, source["state"].text, String(editing), startOffset.map(String.init) ?? "full", endOffset.map(String.init) ?? "full"].joined(separator: "|")
    }
    private var loadKey: String { scopeKey + "|" + refresh.uuidString }
    private var translationQuery: [String: String] {
        var query: [String: String] = language.isEmpty ? [:] : ["language": language]
        if let startOffset, let endOffset {
            query["start_offset"] = String(startOffset)
            query["end_offset"] = String(endOffset)
            query["transcript_version"] = source["transcript_version"].text
        }
        return query
    }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            let filtering = !(speakerReading?.excluded.isEmpty ?? true)
            if hasTranslation && !filtering {
                Picker(L10n.text("Transcript language"), selection: $showingTranslation) {
                    Text(L10n.text("Original")).tag(false)
                    Text(L10n.text("\(languageName) translation")).tag(true)
                }.pickerStyle(.segmented).accessibilityIdentifier("source.transcriptLanguage")
                Text(showingTranslation ? L10n.text("Showing the \(languageName) translation. The original transcription is preserved.") : L10n.text("Showing the original transcription. A \(languageName) translation is available."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if showingTranslation && hasTranslation && !filtering {
                Text(translation["text"].text).textSelection(.enabled).accessibilityIdentifier("source.transcriptText")
            } else {
                RecordingTranscriptTurns(text: originalText, turns: originalTurns, onSeek: onSeek,
                    speakerReading: speakerReading, allTurns: fullTurns, canEditSpeakers: !editing, previewPlayback: previewPlayback)
                    .accessibilityIdentifier("source.transcriptText")
            }
            if !filtering && !source["text"].text.isEmpty && !originalText.isEmpty {
                DisclosureGroup(L10n.text("Translate")) {
                    if loaded {
                        MixedLanguageTranslationNotice(analysis: languageAnalysis, recommendedLanguage: recommendedLanguage,
                            selectedLanguage: language, availableLanguages: availableLanguages, disabled: busy || editing) { generate(targetLanguage: $0) }
                    }
                    if !availableLanguages.isEmpty {
                        Picker(L10n.text("Translate to"), selection: Binding(get: { language }, set: selectLanguage)) {
                            ForEach(availableLanguages, id: \.self) { code in
                                Text(L10n.locale.localizedString(forLanguageCode: code) ?? code).tag(code)
                            }
                        }.disabled(editing).accessibilityIdentifier("source.translationTarget")
                    }
                    Button(language.isEmpty ? L10n.text("Translate") : L10n.text("Translate to \(languageName)"), systemImage: "translate") { generate() }
                        .disabled(busy || editing || !loaded || !availableLanguages.contains(language) || alreadyInTargetLanguage || hasTranslation)
                        .accessibilityIdentifier("source.translate")
                    if busy {
                        ProgressView(generation == nil ? L10n.text("Checking translation…") : L10n.text("Translating…"))
                    } else if loaded && availableLanguages.isEmpty {
                        Text(L10n.text("Choose translation languages in Settings to translate this transcript."))
                            .font(.caption).foregroundStyle(.secondary)
                        NavigationLink(L10n.text("Translation settings")) { SettingsView() }
                    } else if loaded && alreadyInTargetLanguage {
                        Text(L10n.text("Transcript is already in \(languageName). Choose another translation language above."))
                            .font(.caption).foregroundStyle(.secondary)
                    } else if loaded && !hasTranslation {
                        Text(L10n.text("Translation is generated when requested and preserves the original transcription."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if error != nil {
                        ErrorNote(message: error)
                        if !loaded { Button(L10n.text("Retry translation lookup")) { refresh = UUID() }.disabled(busy || editing) }
                    }
                }
            }
        }
        .task(id: loadKey) { await load() }
        .task(id: scopeKey + model.speakerRevision.uuidString) {
            guard speakerReading == nil, !editing, !sourceID.isEmpty,
                  source["reference_type"].text != "recording_transcription", !source["text"].text.isEmpty else { return }
            let scope = scopeKey
            if let fresh = try? await model.client().request("v1/transcripts/\(sourceID)"),
               !Task.isCancelled, scopeKey == scope, fresh["source_version"] == source["source_version"],
               fresh["text"] == source["text"] {
                refreshedTurns = fresh["transcript_turns"].array
            }
        }
        .onChange(of: scopeKey) { _, _ in refreshedTurns = nil }
        .onChange(of: scenePhase) { _, phase in
            // A temporary loss of focus must not discard a translation request or
            // switch the reader back to the original when the request completes.
            if phase == .active && !busy { refresh = UUID() }
        }
        .onDisappear { invalidate(); loadedScope = "" }
    }

    private var originalText: String {
        if source == .null { return L10n.text("Source removed") }
        if source["state"].text == "expired" { return L10n.text("Transcript expired under your retention policy.") }
        if let startOffset, let endOffset {
            let scalars = source["text"].text.unicodeScalars
            guard startOffset >= 0, endOffset > startOffset, endOffset <= scalars.count else { return "" }
            return String(String.UnicodeScalarView(scalars.dropFirst(startOffset).prefix(endOffset - startOffset)))
        }
        if !source["text"].text.isEmpty { return source["text"].text }
        return L10n.text("No transcript text · \(source["transcription_outcome"].text) · \(source["state"].text)")
    }

    private var fullTurns: [JSONValue] { refreshedTurns ?? source["transcript_turns"].array }

    private var originalTurns: [JSONValue] {
        let turns = fullTurns
        guard let startOffset, let endOffset else { return turns }
        return RecordingTranscriptTurns.passageTurns(text: source["text"].text, turns: turns, start: startOffset, end: endOffset)
    }

    private func invalidate() {
        requestID = UUID()
        generation?.cancel(); generation = nil
        translation = .null; languageAnalysis = .null; recommendedLanguage = ""
        showingTranslation = false; alreadyInTargetLanguage = false; loaded = false; busy = false
    }

    private func load() async {
        if loadedScope != scopeKey {
            invalidate(); language = savedTranslationLanguage; availableLanguages = []; loadedScope = scopeKey
        }
        error = nil
        guard !sourceID.isEmpty, !source["text"].text.isEmpty, !originalText.isEmpty, !editing else { return }
        let request = UUID(), identity = model.archiveIdentity, scope = scopeKey
        requestID = request; busy = true
        defer { if requestID == request { busy = false } }
        do {
            var query = translationQuery
            let result: JSONValue
            do {
                result = try await model.client().request("v1/transcripts/\(sourceID)/translation", query: query)
            } catch let failure as BackendFailure where failure.status == 422 && query["language"] != nil {
                // Keep the passage bounds if an older server rejects the requested target.
                try Task.checkCancellation()
                guard requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                query.removeValue(forKey: "language")
                result = try await model.client().request("v1/transcripts/\(sourceID)/translation", query: query)
            }
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
            apply(result); loaded = true
        } catch {
            guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
            self.error = L10n.error(error)
        }
    }

    private func apply(_ result: JSONValue) {
        let previousTranslation = translation
        language = result["language"].text
        availableLanguages = result["available_languages"].array.map(\.text)
        languageAnalysis = result["language_analysis"]
        recommendedLanguage = result["recommended_language"].text
        alreadyInTargetLanguage = result["already_in_target_language"] == .bool(true)
        translation = alreadyInTargetLanguage ? .null : result["translation"]
        if alreadyInTargetLanguage { showingTranslation = false }
        else if previousTranslation != translation { showingTranslation = hasTranslation }
    }

    private func selectLanguage(_ code: String) {
        guard code != language else { return }
        invalidate()
        language = code
        savedTranslationLanguage = code
        refresh = UUID()
    }

    private func generate(targetLanguage: String? = nil) {
        let target = targetLanguage ?? language
        guard !busy, loaded, !editing, !target.isEmpty, availableLanguages.contains(target),
              target != language || !alreadyInTargetLanguage else { return }
        if target != language {
            translation = .null; showingTranslation = false; alreadyInTargetLanguage = false; language = target
        }
        savedTranslationLanguage = target
        let request = UUID(), identity = model.archiveIdentity, scope = scopeKey
        requestID = request; busy = true; error = nil
        generation = Task {
            defer { if requestID == request { busy = false; generation = nil } }
            do {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                let result = try await model.client().request("v1/transcripts/\(sourceID)/translation", method: "POST", query: translationQuery)
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                apply(result)
                showingTranslation = hasTranslation
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                self.error = L10n.error(error)
            }
        }
    }
}

/// Topic passages and the full transcript share translation behavior and cache scope.
struct SourceTranscriptTopicsView: View {
    let source: JSONValue
    let editing: Bool
    var onSeek: ((Double) -> Void)? = nil
    var onPlayTopic: ((Double) -> Void)? = nil
    var speakerReading: RecordingSpeakerReading? = nil
    var previewPlayback: RecordingPlayback? = nil
    @State private var showingTranscript = false
    @State private var openTopic: Int?

    private var sections: [JSONValue] {
        let length = source["text"].text.unicodeScalars.count
        return source["sections"].array.filter {
            $0["start_offset"].integer >= 0 && $0["end_offset"].integer > $0["start_offset"].integer &&
                $0["end_offset"].integer <= length
        }
    }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        RecordingReadingPicker(showingTranscript: $showingTranscript)
        if !showingTranscript && !source["recording_summary"].text.isEmpty {
            Text(source["recording_summary"].text).textSelection(.enabled)
        } else if !showingTranscript && sections.isEmpty && !source["text"].text.isEmpty {
            Text(L10n.text("No summary in this version.")).font(.caption).foregroundStyle(.secondary)
        }
        if showingTranscript && sections.contains(where: { $0.topicStartOffset == openTopic }) {
            Button(L10n.text("Full transcript")) { openTopic = nil }.buttonStyle(.borderless)
        }
        ForEach(sections, id: \.topicStartOffset) { section in
            let start = section["start_offset"].integer
            RecordingTopicRow(title: section["title"].text.isEmpty ? L10n.text("Topic") : section["title"].text,
                expanded: openTopic == start, toggle: { openTopic = openTopic == start ? nil : start },
                seconds: section["start_seconds"].seconds, play: onPlayTopic) {
                if openTopic == start {
                    if showingTranscript {
                        SourceTranscriptView(source: source, editing: false, startOffset: start,
                            endOffset: section["end_offset"].integer, onSeek: onSeek, speakerReading: speakerReading,
                            previewPlayback: previewPlayback)
                    } else {
                        TopicSummaryText(text: section["summary"].text)
                    }
                }
            }
        }
        if source["text"].text.isEmpty {
            SourceTranscriptView(source: source, editing: editing)
        } else if showingTranscript && !sections.contains(where: { $0.topicStartOffset == openTopic }) {
            Text(L10n.text("Full transcript")).font(.headline).accessibilityAddTraits(.isHeader)
            SourceTranscriptView(source: source, editing: editing, onSeek: onSeek, speakerReading: speakerReading,
                previewPlayback: previewPlayback)
        }
    }
}

private extension JSONValue {
    var topicStartOffset: Int { self["start_offset"].integer }
}

struct RecordingReadingPicker: View {
    @Binding var showingTranscript: Bool
    @Environment(\.locale) private var interfaceLocale

    var body: some View {
        let _ = interfaceLocale
        Picker(L10n.text("Recording"), selection: $showingTranscript) {
            Text(L10n.text("status.summary")).tag(false)
            Text(L10n.text("Transcript")).tag(true)
        }.pickerStyle(.segmented)
    }
}

struct RecordingTopicRow<Content: View>: View {
    let title: String
    let expanded: Bool
    let toggle: () -> Void
    let seconds: Double?
    let play: ((Double) -> Void)?
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Button(action: toggle) {
                    HStack {
                        Text(title).multilineTextAlignment(.leading)
                        Spacer(minLength: 8)
                        Image(systemName: expanded ? "chevron.down" : "chevron.right")
                            .font(.caption).foregroundStyle(.secondary).accessibilityHidden(true)
                    }.frame(minHeight: 44).contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityAddTraits(expanded ? .isSelected : [])
                if let play, let seconds, seconds.isFinite, seconds >= 0 {
                    Button { play(seconds) } label: {
                        Image(systemName: "play.fill").frame(minWidth: 44, minHeight: 44)
                    }.buttonStyle(.borderless).accessibilityLabel(L10n.text("Listen from this topic") + " · " + title)
                }
            }
            if expanded { content() }
        }
    }
}

struct TopicReadingView<Summary: View, Transcript: View>: View {
    @ViewBuilder let summary: () -> Summary
    @ViewBuilder let transcript: () -> Transcript
    @Environment(\.locale) private var interfaceLocale
    @State private var showingTranscript = false

    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 10) {
            Picker(L10n.text("Topic view"), selection: $showingTranscript) {
                Text(L10n.text("Topic summary")).tag(false)
                Text(L10n.text("Topic transcript")).tag(true)
            }.pickerStyle(.segmented)
            if showingTranscript { transcript() } else { summary() }
        }
    }

}

struct TopicSummaryText: View {
    let text: String
    var body: some View {
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            Text(L10n.text("No topic summary yet")).font(.subheadline).foregroundStyle(.secondary)
        } else {
            Text(text).textSelection(.enabled)
        }
    }
}

struct RecordingTranscriptTurns: View {
    let text: String
    let turns: [JSONValue]
    var onSeek: ((Double) -> Void)? = nil
    var speakerReading: RecordingSpeakerReading? = nil
    var allTurns: [JSONValue]? = nil
    var canEditSpeakers = true
    var previewPlayback: RecordingPlayback? = nil
    @State private var showTimes = false
    @State private var filters = RecordingSpeakerFilters()
    @State private var editingSpeaker: TranscriptSpeakerSelection?
    @Environment(\.colorScheme) private var colorScheme

    static func passageTurns(text: String, turns: [JSONValue], start: Int, end: Int) -> [JSONValue] {
        let scalars = Array(text.unicodeScalars)
        return turnsInPlaybackOrder(turns).compactMap { turn in
            let turnStart = turn["start_offset"].integer, turnEnd = turn["end_offset"].integer
            let left = max(start, turnStart), right = min(end, turnEnd)
            guard left >= 0, right <= scalars.count, left < right,
                  case .object(var values) = turn else { return nil }
            if left != turnStart || right != turnEnd {
                values["text"] = .string(String(String.UnicodeScalarView(scalars[left..<right])))
                values["start_offset"] = .number(Double(left))
                values["end_offset"] = .number(Double(right))
                values["original_start_offset"] = .number(Double(turn.previewStartOffset))
                if left != turnStart { values["start"] = nil }
                if right != turnEnd { values["end"] = nil }
            }
            return .object(values)
        }
    }

    var body: some View {
        let reading = speakerReading ?? RecordingSpeakerReading(turns: allTurns ?? turns, scope: "transcript", filters: filters)
        let visible = turnsInPlaybackOrder(turns).filter { !reading.hides($0) && $0.hasSpokenText }
        let editable = canEditSpeakers ? editableSpeakers(reading) : []
        let byIdentity = Dictionary(uniqueKeysWithValues: editable.map { ($0.id, $0) })
        let present = Set(turns.filter(\.hasSpokenText).compactMap(\.recordingSpeakerKey))
        let passageSpeakers = reading.speakers.filter { present.contains($0.id) }
        if turns.isEmpty {
            Text(text).textSelection(.enabled)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                if !passageSpeakers.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(passageSpeakers) { speaker in
                            let included = !reading.excluded.contains(speaker.id)
                            let entries = editable.filter { $0.speaker.id == speaker.id }
                            HStack(spacing: 4) {
                                Button { reading.toggle(speaker.id) } label: {
                                    Label {
                                        Text(speaker.label).strikethrough(!included)
                                    } icon: {
                                        Image(systemName: included ? "checkmark.circle.fill" : "circle")
                                    }.font(.subheadline.weight(.bold)).foregroundStyle(speaker.color(dark: colorScheme == .dark))
                                        .padding(.horizontal, 8).frame(minHeight: 44)
                                        .background(Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 5))
                                }.buttonStyle(.plain).accessibilityAddTraits(included ? .isSelected : [])
                                if !entries.isEmpty { editButton(speaker, entries: entries) }
                            }
                        }
                    }.accessibilityElement(children: .contain).accessibilityLabel(L10n.text("Filter speakers"))
                    if reading.incomplete {
                        Text(L10n.text("Some hidden speech will still play if its timing is unclear or it overlaps other audio."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                if visible.contains(where: { $0["start"].seconds != nil }) {
                    Toggle(L10n.text("Show timestamps"), isOn: $showTimes)
                }
                if visible.isEmpty {
                    Text(L10n.text("No speech from the selected speakers in this passage."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                ForEach(visible, id: \.transcriptStartOffset) { turn in
                    let speaker = reading.speaker(for: turn)
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            if let speaker, let identity = turn.recordingSpeakerIdentity, let entry = byIdentity[identity] {
                                Button { editingSpeaker = TranscriptSpeakerSelection(entries: [entry], row: turn) } label: {
                                    speakerBadge(speaker.label, color: speaker.color(dark: colorScheme == .dark), editable: true)
                                }.buttonStyle(.borderless).accessibilityLabel(L10n.text("Edit speaker: \(speaker.label)"))
                            } else {
                                speakerBadge(speaker?.label ?? (turn["attribution"].text == "overlap"
                                    ? L10n.text("Overlapping speech") : L10n.text("Unassigned speech")),
                                    color: speaker?.color(dark: colorScheme == .dark) ?? .secondary)
                            }
                            if showTimes, let start = turn["start"].seconds, start < Double(Int.max) {
                                let seconds = Int(start)
                                let time = String(format: "%02d:%02d", seconds / 60, seconds % 60)
                                if let onSeek {
                                    Button(time) { onSeek(start) }.buttonStyle(.borderless).font(.caption.monospacedDigit())
                                } else { Text(time).font(.caption.monospacedDigit()) }
                            }
                        }
                        Text(turn["text"].text).textSelection(.enabled)
                    }
                }
            }.sheet(item: $editingSpeaker) { selection in
                TranscriptSpeakerEditor(selection: selection, turns: turns, allTurns: allTurns ?? turns, playback: previewPlayback)
            }
        }
    }

    private func speakerBadge(_ title: String, color: Color, editable: Bool = false) -> some View {
        HStack(spacing: 6) {
            Text(title)
            if editable { Image(systemName: "pencil").font(.caption) }
        }.font(.subheadline.weight(.bold)).foregroundStyle(color)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 5))
            .frame(minHeight: editable ? 44 : nil).contentShape(Rectangle())
    }

    private func editButton(_ speaker: RecordingSpeakerReading.Speaker, entries: [TranscriptEditableSpeaker], row: JSONValue? = nil) -> some View {
        Button { editingSpeaker = TranscriptSpeakerSelection(entries: entries, row: row) } label: {
            Image(systemName: "pencil").frame(minWidth: 44, minHeight: 44)
        }.buttonStyle(.borderless).accessibilityLabel(L10n.text("Edit speaker: \(speaker.label)"))
    }

    private func editableSpeakers(_ reading: RecordingSpeakerReading) -> [TranscriptEditableSpeaker] {
        var seen = Set<String>()
        return turnsInPlaybackOrder(turns).compactMap { turn in
            let edit = turn["speaker_edit"]
            guard turn.hasSpokenText, let speaker = reading.speaker(for: turn),
                  !edit["version"].text.isEmpty, let id = turn.recordingSpeakerIdentity,
                  seen.insert(id).inserted else { return nil }
            return TranscriptEditableSpeaker(id: id, speaker: speaker, edit: edit,
                sample: String(turn["text"].text.split(whereSeparator: \.isWhitespace).joined(separator: " ").prefix(60)))
        }
    }
}

private struct TranscriptEditableSpeaker: Identifiable {
    let id: String
    let speaker: RecordingSpeakerReading.Speaker
    let edit: JSONValue
    let sample: String
}

private struct TranscriptSpeakerSelection: Identifiable {
    let id = UUID()
    let entries: [TranscriptEditableSpeaker]
    let row: JSONValue?
}

private struct TranscriptSpeakerPassage {
    let offset: Int
    let interval: RecordingSkipInterval
    let text: String

    static func forSpeaker(_ identity: String, in turns: [JSONValue]) -> [Self] {
        var nextStarts: [String: Double] = [:], passages: [Self] = []
        for turn in turnsInPlaybackOrder(turns).reversed() {
            guard turn.hasSpokenText, let start = turn["start"].seconds else { continue }
            let part = turn["source_part"].text
            let next = nextStarts.updateValue(start, forKey: part)
            guard turn.recordingSpeakerIdentity == identity else { continue }
            let end = min(turn.speakerAudioInterval?.end ?? min(start + 8, next ?? .infinity),
                turn["part_end"].seconds ?? .infinity)
            guard end > start else { continue }
            passages.append(Self(offset: turn.transcriptStartOffset, interval: .init(start: start, end: end), text: turn["text"].text))
        }
        return Array(passages.reversed())
    }

    static func initial(in passages: [Self], shown: [JSONValue], row: JSONValue?) -> Int? {
        if let row {
            return passages.firstIndex { $0.offset == row.previewStartOffset }
        }
        let offsets = Set(shown.map(\.previewStartOffset))
        return passages.firstIndex { offsets.contains($0.offset) } ?? passages.indices.first
    }
}

private struct TranscriptSpeakerEditor: View {
    let selection: TranscriptSpeakerSelection
    let turns: [JSONValue]
    let allTurns: [JSONValue]
    let playback: RecordingPlayback?
    @State private var target: TranscriptEditableSpeaker
    @State private var passages: [TranscriptSpeakerPassage]
    @State private var passageIndex: Int?
    @State private var name: String
    @State private var busy = false
    @State private var error: String?
    @State private var conflict = false
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.locale) private var interfaceLocale

    init(selection: TranscriptSpeakerSelection, turns: [JSONValue], allTurns: [JSONValue], playback: RecordingPlayback?) {
        self.selection = selection; self.turns = turns; self.allTurns = allTurns; self.playback = playback
        let first = selection.entries[0]
        let passages = TranscriptSpeakerPassage.forSpeaker(first.id, in: allTurns)
        _target = State(initialValue: first)
        _name = State(initialValue: first.speaker.name ?? "")
        _passages = State(initialValue: passages)
        _passageIndex = State(initialValue: TranscriptSpeakerPassage.initial(in: passages, shown: turns, row: selection.row))
    }

    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            Form {
                if selection.entries.count > 1 {
                    Picker(L10n.text("Speaker"), selection: Binding(get: { target.id }, set: { id in
                        guard let entry = selection.entries.first(where: { $0.id == id }) else { return }
                        playback?.endPreview()
                        target = entry; name = entry.speaker.name ?? ""; error = nil; conflict = false
                        let updated = TranscriptSpeakerPassage.forSpeaker(entry.id, in: allTurns)
                        passages = updated
                        passageIndex = TranscriptSpeakerPassage.initial(in: updated, shown: turns, row: nil)
                    })) {
                        ForEach(Array(selection.entries.enumerated()), id: \.element.id) { index, entry in
                            Text("\(index + 1). \(entry.speaker.label) – \(entry.sample)").tag(entry.id)
                        }
                    }.disabled(busy)
                } else {
                    Text(target.speaker.label).font(.subheadline.bold())
                }
                TextField(L10n.text("Name"), text: $name).disabled(busy)
                previewSection
                ErrorNote(message: error)
                if busy { ProgressView() }
            }
            .navigationTitle(L10n.text("Edit speaker")).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(L10n.text("Cancel")) { dismiss() }.disabled(busy)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(L10n.text("Save")) { Task { await save() } }
                        .disabled(busy || conflict || name.unicodeScalars.count > 200)
                }
            }
        }.interactiveDismissDisabled(busy)
            .onAppear { playback?.pauseForPreview() }
            .onDisappear { playback?.endPreview() }
    }

    @ViewBuilder private var previewSection: some View {
        if let playback, !passages.isEmpty {
            let previous = passageIndex.map { $0 - 1 } ?? selection.row.flatMap { row in
                passages.lastIndex { $0.offset < row.previewStartOffset }
            }
            let next = passageIndex.map { $0 + 1 } ?? selection.row.flatMap { row in
                passages.firstIndex { $0.offset > row.previewStartOffset }
            }
            Section {
                if let passageIndex, passages.indices.contains(passageIndex) {
                    let passage = passages[passageIndex]
                    Text(passage.text).font(.caption)
                    if playback.loading {
                        ProgressView(L10n.text("Loading audio…"))
                    } else if playback.ready, passage.interval.start < playback.duration {
                        Button {
                            playback.togglePreview(passage.interval)
                        } label: {
                            Label(playback.isPreviewPlaying(passage.interval) ? L10n.text("Pause playback") : L10n.text("Play original audio"),
                                systemImage: playback.isPreviewPlaying(passage.interval) ? "pause.fill" : "play.fill")
                        }.buttonStyle(.borderless)
                    } else {
                        Text(L10n.text("No audio preview for this passage.")).font(.caption).foregroundStyle(.secondary)
                    }
                    ErrorNote(message: playback.error)
                } else {
                    Text(L10n.text("No audio preview for this passage.")).font(.caption).foregroundStyle(.secondary)
                }
                ViewThatFits(in: .horizontal) {
                    HStack { passageButton(L10n.text("Previous passage"), index: previous); passageButton(L10n.text("Next passage"), index: next) }
                    VStack(alignment: .leading) { passageButton(L10n.text("Previous passage"), index: previous); passageButton(L10n.text("Next passage"), index: next) }
                }
            }
        } else {
            Text(L10n.text("No audio preview for this passage.")).font(.caption).foregroundStyle(.secondary)
        }
    }

    private func passageButton(_ title: String, index: Int?) -> some View {
        Button(title) {
            playback?.endPreview(); passageIndex = index
        }.buttonStyle(.borderless).disabled(index.map { !passages.indices.contains($0) } ?? true)
    }

    @MainActor private func save() async {
        guard !busy, !conflict else { return }
        busy = true; error = nil
        defer { busy = false }
        let archive = model.archiveIdentity, edit = target.edit
        let kind = edit["kind"].text, scope = edit["scope_id"].text, id = edit["id"].text
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            _ = try await model.client().request("v1/speakers/\(kind)/\(scope)/\(id)", method: "PATCH",
                body: ["version": edit["version"].text, "name": name.isEmpty ? NSNull() : name as Any])
            guard archive == model.archiveIdentity else { return }
            model.speakerRevision = UUID()
            dismiss()
        } catch {
            guard archive == model.archiveIdentity else { return }
            self.error = L10n.error(error)
            if (error as? BackendFailure)?.status == 409 {
                conflict = true
                model.speakerRevision = UUID()
            }
        }
    }
}

extension JSONValue {
    var seconds: Double? {
        if case .number(let value) = self, value.isFinite, value >= 0 { return value }
        return nil
    }
}

private extension JSONValue {
    var hasSpokenText: Bool { !self["text"].text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var transcriptStartOffset: Int { self["start_offset"].integer }
    var previewStartOffset: Int {
        if case .number = self["original_start_offset"] { return self["original_start_offset"].integer }
        return transcriptStartOffset
    }
    var assignedSpeakerName: String? {
        let name = self["speaker_name"].text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return nil }
        let parts = name.split(separator: " ", omittingEmptySubsequences: false)
        if self["speaker_id"].text.isEmpty, self["speaker_name_is_custom"] != .bool(true), parts.count == 2, parts[0] == "Speaker",
           parts[1].range(of: "^(?:[0-9]+|[A-Z]+)$", options: .regularExpression) != nil { return nil }
        return name
    }
    var recordingSpeakerIdentity: String? {
        let attribution = self["attribution"].text
        guard attribution != "overlap" else { return nil }
        let edit = self["speaker_edit"]
        if !edit["id"].text.isEmpty { return "edit:\(edit["kind"].text):\(edit["scope_id"].text):\(edit["id"].text)" }
        guard attribution != "unknown" else { return nil }
        let id = self["speaker_id"].text
        if !id.isEmpty { return "id:" + id }
        let part = self["source_part"].text
        if case .number = self["speaker_number"] { return "part:\(part):number:\(self["speaker_number"].integer)" }
        let label = self["speaker_label"].text.isEmpty ? self["speaker_name"].text : self["speaker_label"].text
        return label.isEmpty ? nil : "part:\(part):label:\(label)"
    }
    var recordingSpeakerKey: String? {
        let attribution = self["attribution"].text
        guard attribution != "overlap", attribution != "unknown" || recordingSpeakerIdentity != nil else { return nil }
        return assignedSpeakerName.map { "name:" + $0 } ?? recordingSpeakerIdentity
    }
    var speakerAudioInterval: RecordingSkipInterval? {
        guard let start = self["start"].seconds, let end = self["end"].seconds, start < end else { return nil }
        return .init(start: start, end: end)
    }
}

private func turnsInPlaybackOrder(_ turns: [JSONValue]) -> [JSONValue] {
    // Interleave timed turns from parallel tracks without guessing times for untimed text.
    let timed = turns.enumerated().compactMap { index, turn in
        turn["start"].seconds.map { (time: $0, index: index, turn: turn) }
    }
    guard !zip(timed, timed.dropFirst()).allSatisfy({ $0.0.time <= $0.1.time }) else { return turns }
    var ordered = timed.sorted { ($0.time, $0.index) < ($1.time, $1.index) }.makeIterator()
    return turns.map { turn in
        turn["start"].seconds == nil ? turn : (ordered.next()?.turn ?? turn)
    }
}

@MainActor @Observable
final class RecordingSpeakerFilters {
    private(set) var scope = ""
    private(set) var excluded: Set<String> = []

    func toggle(_ key: String, scope: String) {
        if self.scope != scope { excluded = []; self.scope = scope }
        if !excluded.insert(key).inserted { excluded.remove(key) }
    }
}

struct RecordingSpeakerReading {
    struct Speaker: Identifiable {
        let id: String
        let name: String?
        let number: Int
        let colorIndex: Int
        var label: String { name ?? L10n.text("Speaker \(number)") }

        func color(dark: Bool) -> Color {
            let light: [UInt32] = [0x005e9c, 0xa33a10, 0x087546, 0x793795, 0xa12552, 0x006d72, 0x805b04, 0x404ab2, 0x9e3639, 0x526d13]
            let bright: [UInt32] = [0x82caff, 0xffa579, 0x6fdda4, 0xd5a5f7, 0xff9abb, 0x6ed9e1, 0xecca69, 0xaab6ff, 0xffa19e, 0xb8d982]
            let rgb = (dark ? bright : light)[colorIndex]
            return Color(red: Double(rgb >> 16) / 255, green: Double((rgb >> 8) & 255) / 255, blue: Double(rgb & 255) / 255)
        }
    }

    let speakers: [Speaker]
    let excluded: Set<String>
    let intervals: [RecordingSkipInterval]
    let incomplete: Bool
    private let byID: [String: Speaker]
    private let scope: String
    private let filters: RecordingSpeakerFilters

    @MainActor init(turns: [JSONValue], scope: String, filters: RecordingSpeakerFilters,
                    canPlay: Bool = false, canSkip: Bool = true) {
        self.scope = scope; self.filters = filters
        var speakers: [Speaker] = [], byID: [String: Speaker] = [:], identities: [String: Int] = [:]
        for turn in turnsInPlaybackOrder(turns) where turn.hasSpokenText {
            let index: Int
            if let identity = turn.recordingSpeakerIdentity {
                index = identities[identity, default: identities.count]
                identities[identity] = index
            } else { index = identities.count }
            guard let key = turn.recordingSpeakerKey, byID[key] == nil else { continue }
            let name = turn.assignedSpeakerName
            let color = name?.unicodeScalars.reduce(0) { ($0 * 31 + Int($1.value)) % 10 } ?? index % 10
            let speaker = Speaker(id: key, name: name, number: index + 1, colorIndex: color)
            speakers.append(speaker); byID[key] = speaker
        }
        let excluded = filters.scope == scope ? filters.excluded.intersection(byID.keys) : []
        self.excluded = excluded
        self.speakers = speakers; self.byID = byID
        if canPlay && !excluded.isEmpty {
            if canSkip {
                let plan = Self.skipPlan(turns, excluded: excluded)
                intervals = plan.intervals; incomplete = plan.incomplete
            } else { intervals = []; incomplete = true }
        } else { intervals = []; incomplete = false }
    }

    @MainActor func toggle(_ key: String) { filters.toggle(key, scope: scope) }
    func speaker(for turn: JSONValue) -> Speaker? { turn.recordingSpeakerKey.flatMap { byID[$0] } }
    func hides(_ turn: JSONValue) -> Bool { turn.recordingSpeakerKey.map(excluded.contains) ?? false }

    private static func skipPlan(_ turns: [JSONValue], excluded: Set<String>) -> (intervals: [RecordingSkipInterval], incomplete: Bool) {
        func hidden(_ turn: JSONValue) -> Bool { turn.recordingSpeakerKey.map(excluded.contains) ?? false }
        let hiddenTurns = turns.filter { $0.hasSpokenText && hidden($0) }
        let candidates = RecordingSkipInterval.merge(hiddenTurns.compactMap(\.speakerAudioInterval))
        guard !candidates.isEmpty else { return ([], !hiddenTurns.isEmpty) }
        var kept: [RecordingSkipInterval] = []
        for part in Dictionary(grouping: turns, by: { $0["source_part"].text }).values {
            var previousStart = part.first?["part_start"].seconds ?? -.infinity
            var nextEnd = part.first?["part_end"].seconds ?? .infinity
            var nextEnds = Array(repeating: nextEnd, count: part.count)
            for index in part.indices.reversed() {
                nextEnds[index] = nextEnd
                if let interval = part[index].speakerAudioInterval { nextEnd = interval.end }
            }
            for (index, turn) in part.enumerated() {
                if !hidden(turn), turn["text"].text.unicodeScalars.contains(where: CharacterSet.alphanumerics.contains) {
                    kept.append(turn.speakerAudioInterval ?? .init(start: turn["start"].seconds ?? previousStart, end: nextEnds[index]))
                }
                if let start = turn["start"].seconds { previousStart = start }
            }
        }
        let blockers = RecordingSkipInterval.merge(kept)
        var index = 0, intervals: [RecordingSkipInterval] = []
        for span in candidates {
            var start = span.start
            while index < blockers.count, blockers[index].end <= start { index += 1 }
            while index < blockers.count, blockers[index].start < span.end {
                let kept = blockers[index]
                if kept.start > start { intervals.append(.init(start: start, end: kept.start)) }
                start = max(start, kept.end)
                if start >= span.end { break }
                index += 1
            }
            if start < span.end { intervals.append(.init(start: start, end: span.end)) }
        }
        return (intervals, intervals != candidates || hiddenTurns.contains { $0.speakerAudioInterval == nil })
    }
}

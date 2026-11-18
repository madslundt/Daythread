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
                    speakerReading: speakerReading, allTurns: source["transcript_turns"].array)
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

    private var originalTurns: [JSONValue] {
        let turns = source["transcript_turns"].array
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
    var onPlayTopic: ((Double) -> Void)? = nil
    var speakerReading: RecordingSpeakerReading? = nil
    @State private var transcriptOpen = false
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
        if !source["recording_summary"].text.isEmpty {
            Text(source["recording_summary"].text).textSelection(.enabled)
        }
        ForEach(sections, id: \.topicStartOffset) { section in
            let start = section["start_offset"].integer
            DisclosureGroup(section["title"].text.isEmpty ? L10n.text("Topic") : section["title"].text,
                isExpanded: Binding(get: { openTopic == start }, set: { openTopic = $0 ? start : nil })) {
                if openTopic == start {
                    TopicReadingView {
                        TopicSummaryText(text: section["summary"].text)
                    } transcript: {
                        SourceTranscriptView(source: source, editing: false, startOffset: start,
                            endOffset: section["end_offset"].integer, onSeek: onPlayTopic, speakerReading: speakerReading)
                    }
                    if let onPlayTopic, case .number(let seconds) = section["start_seconds"] {
                        Button { onPlayTopic(seconds) } label: { Label(L10n.text("Listen from this topic"), systemImage: "play.fill") }
                            .buttonStyle(.borderless)
                    }
                }
            }
        }
        if source["text"].text.isEmpty {
            SourceTranscriptView(source: source, editing: editing)
        } else {
            DisclosureGroup(L10n.text("Full transcript"), isExpanded: $transcriptOpen) {
                if transcriptOpen {
                    SourceTranscriptView(source: source, editing: editing, onSeek: onPlayTopic, speakerReading: speakerReading)
                }
            }
        }
    }
}

private extension JSONValue {
    var topicStartOffset: Int { self["start_offset"].integer }
}

struct TopicReadingView<Summary: View, Transcript: View>: View {
    @ViewBuilder let summary: () -> Summary
    @ViewBuilder let transcript: () -> Transcript
    @Environment(\.locale) private var interfaceLocale
    @State private var showingTranscript = false

    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 10) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { choices }.fixedSize(horizontal: true, vertical: false)
                VStack(alignment: .leading, spacing: 4) { choices }
            }.accessibilityElement(children: .contain).accessibilityLabel(L10n.text("Topic view"))
            if showingTranscript { transcript() } else { summary() }
        }
    }

    @ViewBuilder private var choices: some View {
        choice(L10n.text("Topic summary"), transcript: false)
        choice(L10n.text("Topic transcript"), transcript: true)
    }

    private func choice(_ label: String, transcript: Bool) -> some View {
        Button {
            showingTranscript = transcript
        } label: {
            Text(label).font(.subheadline.weight(showingTranscript == transcript ? .semibold : .regular))
                .padding(.vertical, 4)
        }
        .buttonStyle(.bordered)
        .accessibilityAddTraits(showingTranscript == transcript ? .isSelected : [])
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
    @State private var showTimes = false
    @State private var filters = RecordingSpeakerFilters()
    @State private var filterOpen = false
    @Environment(\.colorScheme) private var colorScheme

    static func passageTurns(text: String, turns: [JSONValue], start: Int, end: Int) -> [JSONValue] {
        let scalars = Array(text.unicodeScalars)
        return turns.compactMap { turn in
            let turnStart = turn["start_offset"].integer, turnEnd = turn["end_offset"].integer
            let left = max(start, turnStart), right = min(end, turnEnd)
            guard left >= 0, right <= scalars.count, left < right,
                  case .object(var values) = turn else { return nil }
            if left != turnStart || right != turnEnd {
                values["text"] = .string(String(String.UnicodeScalarView(scalars[left..<right])))
                if left != turnStart { values["start"] = nil }
                if right != turnEnd { values["end"] = nil }
            }
            return .object(values)
        }
    }

    var body: some View {
        let reading = speakerReading ?? RecordingSpeakerReading(turns: allTurns ?? turns, scope: "transcript", filters: filters)
        let visible = turns.filter { !reading.hides($0) && $0.hasSpokenText }
        if turns.isEmpty {
            Text(text).textSelection(.enabled)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                if !reading.speakers.isEmpty {
                    DisclosureGroup(isExpanded: $filterOpen) {
                        ForEach(reading.speakers) { speaker in
                            Toggle(isOn: Binding(get: { !reading.excluded.contains(speaker.id) },
                                set: { _ in reading.toggle(speaker.id) })) {
                                Text(speaker.label).foregroundStyle(speaker.color(dark: colorScheme == .dark))
                            }
                        }
                    } label: {
                        HStack {
                            Text(L10n.text("Filter speakers"))
                            if !reading.excluded.isEmpty { Text("(\(reading.excluded.count))").foregroundStyle(.secondary) }
                        }
                    }
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
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(speaker?.label ?? (turn["attribution"].text == "overlap"
                                ? L10n.text("Overlapping speech") : L10n.text("Unassigned speech")))
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(speaker?.color(dark: colorScheme == .dark) ?? Color.secondary)
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
            }
        }
    }
}

private extension JSONValue {
    var seconds: Double? {
        if case .number(let value) = self, value.isFinite, value >= 0 { return value }
        return nil
    }
    var hasSpokenText: Bool { !self["text"].text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var transcriptStartOffset: Int { self["start_offset"].integer }
    var assignedSpeakerName: String? {
        let name = self["speaker_name"].text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return nil }
        let parts = name.split(separator: " ", omittingEmptySubsequences: false)
        if self["speaker_id"].text.isEmpty, parts.count == 2, parts[0] == "Speaker",
           parts[1].range(of: "^(?:[0-9]+|[A-Z]+)$", options: .regularExpression) != nil { return nil }
        return name
    }
    var recordingSpeakerKey: String? {
        let attribution = self["attribution"].text
        guard attribution != "unknown", attribution != "overlap" else { return nil }
        if let name = assignedSpeakerName { return "name:" + name }
        let id = self["speaker_id"].text
        if !id.isEmpty { return "id:" + id }
        let part = self["source_part"].text
        if case .number = self["speaker_number"] { return "part:\(part):number:\(self["speaker_number"].integer)" }
        let label = self["speaker_label"].text.isEmpty ? self["speaker_name"].text : self["speaker_label"].text
        return label.isEmpty ? nil : "part:\(part):label:\(label)"
    }
    var speakerAudioInterval: RecordingSkipInterval? {
        guard let start = self["start"].seconds, let end = self["end"].seconds, start < end else { return nil }
        return .init(start: start, end: end)
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
        let letter: String
        let colorIndex: Int
        var label: String { name ?? L10n.text("Speaker \(letter)") }

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
        let excluded = filters.scope == scope ? filters.excluded : []
        self.excluded = excluded
        var speakers: [Speaker] = [], byID: [String: Speaker] = [:], anonymous = 0
        for turn in turns where turn.hasSpokenText {
            guard let key = turn.recordingSpeakerKey, byID[key] == nil else { continue }
            let name = turn.assignedSpeakerName
            let color = name?.unicodeScalars.reduce(0) { ($0 * 31 + Int($1.value)) % 10 } ?? anonymous % 10
            let speaker = Speaker(id: key, name: name, letter: Self.letter(anonymous), colorIndex: color)
            speakers.append(speaker); byID[key] = speaker
            if name == nil { anonymous += 1 }
        }
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

    private static func letter(_ index: Int) -> String {
        var value = index, letter = ""
        while value >= 0 {
            if let scalar = UnicodeScalar(65 + value % 26) { letter = String(scalar) + letter }
            value = value / 26 - 1
        }
        return letter
    }

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

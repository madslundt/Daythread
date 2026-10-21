import SwiftUI
import OmiCore

/// Collapsed recaps contain plain text only, so hidden topic links are never focusable.
struct RecapOverviewDisclosure<Content: View>: View {
    let text: String
    @Binding var expanded: Bool
    let identifier: String
    @ViewBuilder let content: () -> Content

    private var preview: String {
        let markdown = (try? AttributedString(markdown: text,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
        let plain = String(markdown.characters).split(whereSeparator: \.isWhitespace).joined(separator: " ")
        return plain.count > 240 ? String(plain.prefix(240)) + "…" : plain
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if expanded { content() }
            else {
                Text(verbatim: preview).lineLimit(3).foregroundStyle(.secondary)
                    .accessibilityIdentifier(identifier + ".preview")
            }
            Button {
                withAnimation(.easeInOut(duration: 0.18)) { expanded.toggle() }
            } label: {
                Label(expanded ? L10n.text("Collapse recap") : L10n.text("Expand recap"),
                    systemImage: expanded ? "chevron.up" : "chevron.down")
            }.buttonStyle(.borderless)
                .accessibilityValue(expanded ? L10n.text("Expanded") : L10n.text("Collapsed"))
                .accessibilityIdentifier(identifier + ".expand")
        }
    }
}
/// A saved summary has a separate translation while its original actions and evidence retain their identity.
struct SummaryTranslationView<Original: View>: View {
    let output: JSONValue
    private let original: () -> Original
    private let renderedContent: ((JSONValue?, Bool) -> Original)?
    private let translationIsCompatible: ((JSONValue) -> Bool)?
    private let onContentLanguageChange: ((String?) -> Void)?
    private let languageLabel: String
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.locale) private var interfaceLocale
    @State private var translation: JSONValue = .null
    @State private var summaryVersion: JSONValue = .null
    @AppStorage("lastTranslationLanguage") private var savedTranslationLanguage = ""
    @State private var language = ""
    @State private var availableLanguages: [String] = []
    @State private var languageAnalysis: JSONValue = .null
    @State private var recommendedLanguage = ""
    @State private var showingTranslation = false
    @State private var alreadyInTargetLanguage = false
    @State private var busy = false
    @State private var loaded = false
    @State private var copied = false
    @State private var error: String?
    @State private var requestID = UUID()
    @State private var refresh = UUID()
    @State private var generation: Task<Void, Never>?
    @State private var loadedScope = ""
    @State private var regenerationRequired = false

    init(output: JSONValue, @ViewBuilder original: @escaping () -> Original) {
        self.output = output; self.original = original; renderedContent = nil; translationIsCompatible = nil; onContentLanguageChange = nil
        languageLabel = L10n.source("Summary language")
    }

    init(output: JSONValue, languageLabel: String = L10n.source("Summary language"), translationIsCompatible: ((JSONValue) -> Bool)? = nil,
         onContentLanguageChange: ((String?) -> Void)? = nil,
         @ViewBuilder renderedContent: @escaping (JSONValue?, Bool) -> Original) {
        self.output = output; self.renderedContent = renderedContent
        self.translationIsCompatible = translationIsCompatible
        self.onContentLanguageChange = onContentLanguageChange
        self.languageLabel = languageLabel
        original = { renderedContent(nil, false) }
    }

    private var outputID: String { output["output_id"].text }
    private var languageName: String { L10n.locale.localizedString(forLanguageCode: language) ?? language }
    private var hasTranslation: Bool {
        loadedScope == scopeKey && !regenerationRequired && !alreadyInTargetLanguage && !translation["text"].text.isEmpty &&
            translation["language"].text == language && summaryVersion != .null &&
            translation["summary_version"] == summaryVersion && (translationIsCompatible?(translation) ?? true)
    }
    private var needsLayoutUpdate: Bool {
        regenerationRequired || (!translation["text"].text.isEmpty && !(translationIsCompatible?(translation) ?? true))
    }
    private var scopeKey: String {
        [model.archiveIdentity, model.archiveRevision, outputID, output["summary"].text].joined(separator: "|")
    }
    private var loadKey: String { scopeKey + "|" + refresh.uuidString }
    private var path: String { "v1/outputs/\(outputID)/translation" }
    private var translationQuery: [String: String] { language.isEmpty ? [:] : ["language": language] }
    private var contentLanguage: String? { showingTranslation && hasTranslation && loadedScope == scopeKey ? language : nil }

    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 12) {
            if hasTranslation {
                Picker(L10n.key(languageLabel), selection: $showingTranslation) {
                    Text(L10n.text("Original")).tag(false)
                    Text(L10n.text("\(languageName) translation")).tag(true)
                }.pickerStyle(.segmented).accessibilityIdentifier("summary.language")
                Text(showingTranslation ? L10n.text("Showing the \(languageName) translation. The original summary is preserved.") : L10n.text("Showing the original summary. A \(languageName) translation is available."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if loaded && needsLayoutUpdate {
                Text(L10n.text("Update this translation to keep the recap’s topics and layout.")).font(.caption).foregroundStyle(.secondary)
            }
            if let renderedContent {
                renderedContent(hasTranslation ? translation : nil, showingTranslation && hasTranslation)
            } else if showingTranslation && hasTranslation {
                Text(.init(translation["text"].text)).textSelection(.enabled).accessibilityIdentifier("summary.translationText")
                Button {
                    UIPasteboard.general.string = translation["text"].text
                    copied = true
                } label: { Label(copied ? L10n.text("Copied") : L10n.text("Copy"), systemImage: copied ? "checkmark" : "doc.on.doc") }
                    .buttonStyle(.borderless)
            } else { original() }
            if !outputID.isEmpty && !output["summary"].text.isEmpty {
                DisclosureGroup(L10n.text("Translate")) {
                    if loaded {
                        MixedLanguageTranslationNotice(analysis: languageAnalysis, recommendedLanguage: recommendedLanguage,
                            selectedLanguage: language, availableLanguages: availableLanguages, disabled: busy) { generate(targetLanguage: $0) }
                    }
                    if !availableLanguages.isEmpty {
                        Picker(L10n.text("Translate to"), selection: Binding(get: { language }, set: selectLanguage)) {
                            ForEach(availableLanguages, id: \.self) { code in
                                Text(L10n.locale.localizedString(forLanguageCode: code) ?? code).tag(code)
                            }
                        }.accessibilityIdentifier("summary.translationTarget")
                    }
                    Button(language.isEmpty ? L10n.text("Translate") : L10n.text("Translate to \(languageName)"), systemImage: "translate") { generate() }
                        .disabled(busy || !loaded || !availableLanguages.contains(language) || alreadyInTargetLanguage || hasTranslation)
                        .accessibilityIdentifier("summary.translate")
                    if busy {
                        ProgressView(generation == nil ? L10n.text("Checking translation…") : L10n.text("Translating…"))
                    } else if loaded && availableLanguages.isEmpty {
                        Text(L10n.text("Choose translation languages in Settings to translate this summary."))
                            .font(.caption).foregroundStyle(.secondary)
                        NavigationLink(L10n.text("Translation settings")) { SettingsView() }
                    } else if loaded && alreadyInTargetLanguage {
                        Text(L10n.text("Summary is already in \(languageName). Choose another translation language above."))
                            .font(.caption).foregroundStyle(.secondary)
                    } else if loaded && !hasTranslation && !needsLayoutUpdate {
                        Text(L10n.text("Translation is generated when requested and preserves the original summary."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if error != nil {
                        ErrorNote(message: error)
                        if !loaded { Button(L10n.text("Retry translation lookup")) { refresh = UUID() }.disabled(busy) }
                    }
                }
            }
        }
        .task(id: loadKey) { await load() }
        .onChange(of: contentLanguage, initial: true) { _, value in onContentLanguageChange?(value) }
        .onChange(of: scenePhase) { _, phase in if phase == .active && !busy { refresh = UUID() } }
        .onDisappear { invalidate(); loadedScope = ""; onContentLanguageChange?(nil) }
    }

    private func invalidate() {
        requestID = UUID()
        generation?.cancel(); generation = nil
        translation = .null; summaryVersion = .null; languageAnalysis = .null; recommendedLanguage = ""; showingTranslation = false
        alreadyInTargetLanguage = false; loaded = false; busy = false; copied = false
        regenerationRequired = false
    }

    private func load() async {
        if loadedScope != scopeKey {
            invalidate(); language = savedTranslationLanguage; availableLanguages = []; loadedScope = scopeKey
        }
        error = nil
        guard !outputID.isEmpty, !output["summary"].text.isEmpty else { return }
        let request = UUID(), identity = model.archiveIdentity, scope = scopeKey
        requestID = request; busy = true
        defer { if requestID == request { busy = false } }
        do {
            let result: JSONValue
            do {
                result = try await model.client().request(path, query: translationQuery)
            } catch let failure as BackendFailure where failure.status == 422 && !language.isEmpty {
                try Task.checkCancellation()
                guard requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                result = try await model.client().request(path)
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
        summaryVersion = result["summary_version"]
        alreadyInTargetLanguage = result["already_in_target_language"] == .bool(true)
        regenerationRequired = result["regeneration_required"] == .bool(true)
        translation = alreadyInTargetLanguage ? .null : result["translation"]
        if alreadyInTargetLanguage { showingTranslation = false }
        else if previousTranslation != translation { showingTranslation = hasTranslation; copied = false }
    }

    private func selectLanguage(_ code: String) {
        guard code != language else { return }
        invalidate(); language = code; savedTranslationLanguage = code; refresh = UUID()
    }

    private func generate(targetLanguage: String? = nil) {
        let target = targetLanguage ?? language
        guard !busy, loaded, !target.isEmpty, availableLanguages.contains(target),
              target != language || !alreadyInTargetLanguage else { return }
        if target != language {
            translation = .null; showingTranslation = false; alreadyInTargetLanguage = false; copied = false; language = target
        }
        savedTranslationLanguage = target
        let request = UUID(), identity = model.archiveIdentity, scope = scopeKey
        requestID = request; busy = true; error = nil
        generation = Task {
            defer { if requestID == request { busy = false; generation = nil } }
            do {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                let result = try await model.client().request(path, method: "POST", query: translationQuery)
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                apply(result); showingTranslation = hasTranslation
            } catch {
                guard !Task.isCancelled, requestID == request, model.archiveIdentity == identity, scopeKey == scope else { return }
                self.error = L10n.error(error)
            }
        }
    }
}

/// Recommendations never start generation until the reader requests it.
struct MixedLanguageTranslationNotice: View {
    let analysis: JSONValue
    let recommendedLanguage: String
    let selectedLanguage: String
    let availableLanguages: [String]
    let disabled: Bool
    let translate: (String) -> Void

    private var dominantLanguage: String { analysis["dominant_language"].text }
    private func baseLanguage(_ code: String) -> String {
        code.lowercased().split(whereSeparator: { $0 == "-" || $0 == "_" }).first.map(String.init) ?? ""
    }
    private var mixedShare: Double? {
        guard !dominantLanguage.isEmpty, !recommendedLanguage.isEmpty,
              baseLanguage(recommendedLanguage) == baseLanguage(dominantLanguage), availableLanguages.contains(recommendedLanguage),
              case .number(let share) = analysis["dominant_language_share"], share > 0, share < 0.9,
              case .object(let languages) = analysis["language_shares"],
              languages.values.filter({ if case .number(let value) = $0 { return value > 0 }; return false }).count > 1 else { return nil }
        return share
    }

    var body: some View {
        if let share = mixedShare {
            let name = L10n.locale.localizedString(forLanguageCode: dominantLanguage) ?? dominantLanguage
            let percent = ((share * 1000).rounded(.down) / 1000)
                .formatted(.percent.precision(.fractionLength(0...1)).locale(L10n.locale))
            Text(L10n.text("Mixed languages · \(name) is dominant (\(percent))."))
                .font(.caption).foregroundStyle(.secondary)
                .accessibilityIdentifier("translation.mixedLanguages")
            if selectedLanguage != recommendedLanguage {
                Button(L10n.text("Translate to \(name)"), systemImage: "translate") { translate(recommendedLanguage) }
                    .disabled(disabled).accessibilityIdentifier("translation.translateToDominant")
            }
        }
    }
}

import SwiftUI

struct VoiceRecognitionReadiness: Decodable {
    let state: String
    let message: String
}

struct SavedVoiceDirectory: Decodable {
    struct Profile: Decodable, Identifiable {
        struct Example: Decodable, Identifiable {
            let id: String
            let scope: ConversationSpeakers.Scope
            let speaker_id: String
            let seconds: Double
            let created_at: String
        }
        let id: String
        let name: String
        let is_self: Bool?
        let example_count: Int
        let conversation_count: Int
        let examples: [Example]
    }
    let revision: String
    let profiles: [Profile]
    let recognition: VoiceRecognitionReadiness
    var myVoice: Profile? { profiles.first { $0.is_self == true } }
}

struct ConversationSpeakers: Decodable {
    struct Scope: Decodable { let kind: String; let id: String }
    struct Speaker: Decodable, Identifiable {
        struct Suggestion: Decodable { let profile_id: String; let name: String }
        let id: String
        let label: String
        let name: String?
        let seconds: Double
        let is_self: Bool?
        let voice_profile_id: String?
        let remembered_example_id: String?
        let suggestion: Suggestion?
        var displayName: String { name?.isEmpty == false ? name! : label }
    }
    struct Turn: Decodable, Identifiable {
        let id: String
        let source_id: String
        let start: Double
        let end: Double
        let speaker_id: String
        let text: String?
    }
    let state: String
    let configured: Bool
    let version: String?
    let speakers: [Speaker]
    let turns: [Turn]
    let audio_available: Bool
    let scope: Scope
    let source_count: Int
    let recognition: VoiceRecognitionReadiness?
}

struct MyVoiceSection: View {
    let voices: SavedVoiceDirectory?
    let busy: Bool
    let onClear: () -> Void
    @Environment(\.locale) private var interfaceLocale

    var body: some View {
        let _ = interfaceLocale
        Section(L10n.text("My voice")) {
            if let voices {
                if let own = voices.myVoice {
                    Text(own.name).font(.headline)
                    Text(L10n.text("\(own.example_count) examples · \(own.conversation_count) conversations"))
                        .font(.caption).foregroundStyle(.secondary)
                    Text(own.conversation_count < 2
                         ? L10n.text("Add at least ten seconds of clear speech from two separate conversations.")
                         : L10n.text("Your voice examples are saved. Suggestions still require a passing evaluation and your confirmation."))
                        .font(.callout).foregroundStyle(.secondary)
                    Button(L10n.text("Clear my voice"), action: onClear).disabled(busy)
                } else {
                    Text(L10n.text("No voice has been marked as yours in this archive."))
                    Text(L10n.text("Add at least ten seconds of clear speech from two separate conversations."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text(L10n.text("Detect speakers, listen to your turns, then choose “This is my voice” when remembering a speaker."))
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text(L10n.text("Loading saved voices…")).foregroundStyle(.secondary)
            }
        }
    }
}

struct RememberVoiceSheet: View {
    let speaker: ConversationSpeakers.Speaker
    let directory: SavedVoiceDirectory?
    private var profiles: [SavedVoiceDirectory.Profile] { directory?.profiles ?? [] }
    let onConfirm: ([String: Any]) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var selectedProfile = ""
    @State private var newName: String
    @State private var isMyVoice: Bool

    init(speaker: ConversationSpeakers.Speaker, directory: SavedVoiceDirectory?, onConfirm: @escaping ([String: Any]) -> Void) {
        self.speaker = speaker; self.directory = directory; self.onConfirm = onConfirm
        _isMyVoice = State(initialValue: speaker.is_self == true)
        _newName = State(initialValue: speaker.name ?? "")
        _selectedProfile = State(initialValue: speaker.voice_profile_id ?? "")
    }

    private var targetProfile: String { isMyVoice ? directory?.myVoice?.id ?? selectedProfile : selectedProfile }
    private var trimmedName: String { newName.trimmingCharacters(in: .whitespacesAndNewlines) }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            Form {
                Section {
                    Text(L10n.text("After listening, explicitly choose whose voice \(speaker.displayName) is. This saves a voice example and names the speaker in this conversation."))
                    Text(L10n.text("Future conversations may suggest this voice; you confirm every suggestion. A new profile remains separate even when its name matches another profile."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section(L10n.text("Save voice to")) {
                    Toggle(L10n.text("This is my voice"), isOn: $isMyVoice)
                    if isMyVoice, let own = directory?.myVoice {
                        LabeledContent(L10n.text("My voice"), value: own.name)
                    } else {
                        Picker(L10n.text("Voice profile"), selection: $selectedProfile) {
                            Text(L10n.text("Create a new voice profile")).tag("")
                            ForEach(profiles) { profile in
                                Text(L10n.text("\(profile.name) · \(profile.example_count) examples")).tag(profile.id)
                            }
                        }
                    }
                    if targetProfile.isEmpty {
                        TextField(L10n.text("New voice profile name"), text: $newName).textInputAutocapitalization(.words)
                        if trimmedName.count > 120 { Text(L10n.text("Use a name up to 120 characters.")).foregroundStyle(.red) }
                    }
                }
                Section {
                    Button(L10n.text("Confirm and remember voice")) {
                        var choice: [String: Any] = targetProfile.isEmpty ? ["name": trimmedName] : ["profile_id": targetProfile]
                        if isMyVoice, let directory { choice["is_self"] = true; choice["voice_revision"] = directory.revision }
                        onConfirm(choice)
                        dismiss()
                    }.disabled(directory == nil || (targetProfile.isEmpty && (trimmedName.isEmpty || trimmedName.count > 120)))
                }
            }
            .navigationTitle(L10n.text("Remember voice")).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() } } }
        }
    }
}

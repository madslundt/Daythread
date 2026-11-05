import SwiftUI

struct VoiceRecognitionReadiness: Decodable {
    let state: String
    let message: String
}

struct SavedVoiceDirectory: Decodable {
    struct Preferences: Decodable {
        let enabled: Bool
        let auto_assign: Bool
        let mode: String
    }
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
    let preferences: Preferences?
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
        let automatic_match: Suggestion?
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
                    Text(voices.preferences?.mode == "personal"
                         ? L10n.text("Your voice is saved for recognition in future recordings.")
                         : own.conversation_count < 2
                         ? L10n.text("Add at least ten seconds of clear speech from two separate conversations.")
                         : L10n.text("Your voice examples are saved. Suggestions still require a passing evaluation and your confirmation."))
                        .font(.callout).foregroundStyle(.secondary)
                    Button(L10n.text("Clear my voice"), action: onClear).disabled(busy)
                } else {
                    Text(L10n.text("No voice has been marked as yours in this archive."))
                    Text(L10n.text("Choose “This is my voice” when assigning your voice."))
                        .font(.caption).foregroundStyle(.secondary)
                }
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
    let onName: (String) -> Void
    let canRemember: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var selectedProfile = ""
    @State private var newName: String
    @State private var isMyVoice: Bool
    @State private var rememberVoice: Bool

    init(speaker: ConversationSpeakers.Speaker, directory: SavedVoiceDirectory?, canRemember: Bool, onName: @escaping (String) -> Void, onConfirm: @escaping ([String: Any]) -> Void) {
        self.speaker = speaker; self.directory = directory; self.onConfirm = onConfirm
        self.onName = onName
        self.canRemember = canRemember
        _rememberVoice = State(initialValue: canRemember)
        _isMyVoice = State(initialValue: speaker.is_self == true)
        _newName = State(initialValue: speaker.name ?? "")
        _selectedProfile = State(initialValue: speaker.voice_profile_id ?? speaker.automatic_match?.profile_id ?? "")
    }

    private var targetProfile: String { isMyVoice ? directory?.myVoice?.id ?? selectedProfile : selectedProfile }
    private var trimmedName: String { newName.trimmingCharacters(in: .whitespacesAndNewlines) }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            Form {
                Section {
                    Text(L10n.text("Listen to a voice, choose a person, then assign. Remembered voices are recognized in later recordings."))
                }
                Section(L10n.text("Speaker")) {
                    Toggle(L10n.text("Remember voice for future recordings"), isOn: $rememberVoice)
                        .disabled(!canRemember)
                        .onChange(of: rememberVoice) { _, value in if !value { isMyVoice = false } }
                    if rememberVoice { Toggle(L10n.text("This is my voice"), isOn: $isMyVoice) }
                    if isMyVoice, let own = directory?.myVoice {
                        LabeledContent(L10n.text("My voice"), value: own.name)
                    } else {
                        Picker(L10n.text("Speaker"), selection: $selectedProfile) {
                            Text(L10n.text("New speaker")).tag("")
                            ForEach(profiles) { profile in
                                Text(L10n.text("\(profile.name) · \(profile.example_count) examples")).tag(profile.id)
                            }
                        }
                    }
                    if targetProfile.isEmpty {
                        TextField(L10n.text("Name"), text: $newName).textInputAutocapitalization(.words)
                        if trimmedName.count > 120 { Text(L10n.text("Use a name up to 120 characters.")).foregroundStyle(.red) }
                    }
                }
                Section {
                    Button(L10n.text("Assign speaker")) {
                        if rememberVoice {
                            var choice: [String: Any] = targetProfile.isEmpty ? ["name": trimmedName] : ["profile_id": targetProfile]
                            choice["auto_assign"] = true
                            if isMyVoice, let directory { choice["is_self"] = true; choice["voice_revision"] = directory.revision }
                            onConfirm(choice)
                        } else {
                            onName(targetProfile.isEmpty ? trimmedName : profiles.first { $0.id == targetProfile }?.name ?? trimmedName)
                        }
                        dismiss()
                    }.disabled((rememberVoice && (directory == nil || (targetProfile.isEmpty && trimmedName.isEmpty))) || trimmedName.count > 120)
                    if !rememberVoice {
                        Text(L10n.text("Leave blank to restore the letter label.")).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle(L10n.text("Assign speaker")).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() } } }
        }
    }
}

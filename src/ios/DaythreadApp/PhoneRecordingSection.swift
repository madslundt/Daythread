import SwiftUI
import OmiCore

struct PhoneRecordingSection: View {
    @Environment(AppModel.self) private var model
    @Environment(\.locale) private var interfaceLocale
    private var microphone: PhoneRecorder { model.microphone }

    var body: some View {
        let _ = interfaceLocale
        Section {
            if let recording = microphone.active {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Label(L10n.text("Recording · \(elapsed(context.date.timeIntervalSince(recording.startedAt)))"),
                          systemImage: "record.circle.fill")
                        .foregroundStyle(.red).monospacedDigit()
                }
                Button { microphone.stop() } label: {
                    Label(L10n.text("Stop recording"), systemImage: "stop.circle.fill")
                }.buttonStyle(PrimaryActionStyle()).accessibilityIdentifier("recording.stop")
            } else {
                Button {
                    model.player?.stop(); model.playbackStatus = ""
                    Task { await microphone.start(archiveIdentity: model.archiveIdentity) }
                } label: {
                    Label(microphone.requestingPermission ? L10n.text("Requesting microphone access…") : L10n.text("Start recording"),
                          systemImage: "mic.circle.fill")
                }.buttonStyle(PrimaryActionStyle()).accessibilityIdentifier("recording.start")
                    .disabled(microphone.requestingPermission || !model.hasSelectedProfile)
            }
            ErrorNote(message: microphone.error)
            ErrorNote(message: microphone.recoveryWarning)
        } header: { Text(L10n.text("Record with iPhone")) } footer: {
            Text(L10n.text("Record with this iPhone’s microphone. Audio stays on this iPhone until you upload it. Recordings stop automatically after two hours."))
        }
        let saved = microphone.recordings.filter { $0.id != microphone.active?.id }
        if !saved.isEmpty {
            Section {
                ForEach(saved) { recording in
                    VStack(alignment: .leading, spacing: 10) {
                        if microphone.decisionID == recording.id {
                            Text(L10n.text("Recording finished")).font(.headline)
                        }
                        Text(recording.startedAt.formatted(.dateTime.locale(L10n.locale).day().month().hour().minute()))
                            .font(.subheadline)
                        if let end = recording.endedAt {
                            Text(elapsed(end.timeIntervalSince(recording.startedAt))).font(.caption).foregroundStyle(.secondary)
                        } else {
                            Text(L10n.text("Recovered recording. Upload the audio captured before the interruption, or remove it."))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if microphone.uploadingID == recording.id, let importer = microphone.importer {
                            Text(L10n.key(importer.stage)).font(.caption)
                            if importer.totalBytes > 0 {
                                ProgressView(value: Double(importer.sentBytes), total: Double(importer.totalBytes))
                                    .accessibilityLabel(L10n.text("Recording upload progress"))
                            } else { ProgressView() }
                        }
                        VStack(alignment: .leading, spacing: 10) {
                            Button(L10n.text("Upload now")) { microphone.upload(recording, model: model) }
                                .disabled(microphone.uploadingID != nil)
                            HStack(spacing: 18) {
                                if microphone.decisionID == recording.id {
                                    Button(L10n.text("Upload later")) { microphone.decisionID = nil }
                                        .disabled(microphone.uploadingID == recording.id)
                                }
                                Button(L10n.text("Listen")) { microphone.play(recording, model: model) }
                                    .disabled(microphone.active != nil)
                                Button(L10n.text("Remove recording"), role: .destructive) { microphone.remove(recording) }
                                    .disabled(microphone.uploadingID == recording.id)
                            }
                        }.buttonStyle(.borderless).font(.callout)
                    }.padding(.vertical, 5)
                }
            } header: { Text(L10n.text("Saved recordings")) } footer: {
                Text(L10n.text("Saved recordings remain available here after closing the app. Upload failures keep the original recording for retry."))
            }
        }
    }

    private func elapsed(_ seconds: TimeInterval) -> String {
        let seconds = max(0, Int(seconds))
        return String(format: "%02d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
    }
}

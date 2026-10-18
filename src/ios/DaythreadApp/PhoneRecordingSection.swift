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
                Button { model.disableMicrophone() } label: {
                    Label(L10n.text("Stop recording"), systemImage: "stop.circle.fill")
                }.buttonStyle(PrimaryActionStyle()).accessibilityIdentifier("recording.stop")
            } else {
                Button {
                    model.startManualRecording()
                } label: {
                    Label(microphone.requestingPermission ? L10n.text("Requesting microphone access…") : L10n.text("Start recording"),
                          systemImage: "mic.circle.fill")
                }.buttonStyle(PrimaryActionStyle()).accessibilityIdentifier("recording.start")
                    .disabled(microphone.requestingPermission || !model.hasSelectedProfile)
            }
            ErrorNote(message: microphone.error)
            ErrorNote(message: microphone.recoveryWarning)
        } header: { Text(L10n.text("This iPhone")) } footer: {
            Text(L10n.text("Record with this iPhone’s microphone. Audio stays on this iPhone until you upload it. Recordings stop automatically after two hours."))
        }
        RecordingScheduleSection()
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

struct RecordingScheduleSection: View {
    @Environment(AppModel.self) private var model
    private var policy: RecordingSchedule { model.recordingSchedule }
    private func update(_ change: (inout RecordingSchedule) -> Void) {
        var value = policy; change(&value); model.setRecordingSchedule(value)
    }
    private func time(_ minute: Int) -> Date {
        Calendar.current.date(bySettingHour: minute / 60, minute: minute % 60, second: 0, of: Date()) ?? Date()
    }
    private func minute(_ date: Date) -> Int {
        Calendar.current.component(.hour, from: date) * 60 + Calendar.current.component(.minute, from: date)
    }
    var body: some View {
        Section {
            Toggle(L10n.text("Recording schedule"), isOn: Binding(get: { policy.scheduled }, set: { scheduled in
                update { $0.scheduled = scheduled; $0.manualOverride = false }
            })).accessibilityIdentifier("microphone.schedule")
            if policy.scheduled {
                Picker(L10n.text("Scheduled source"), selection: Binding(get: { policy.source }, set: { source in update { $0.source = source } })) {
                    Text(L10n.text("Record with iPhone")).tag("phone")
                    Text(L10n.text("Omi pendant")).tag("omi")
                }
                ForEach(1...7, id: \.self) { day in
                    Toggle(weekday(day), isOn: Binding(get: { policy.weekdays.contains(day) }, set: { selected in
                        update { if selected { $0.weekdays.insert(day) } else { $0.weekdays.remove(day) } }
                    }))
                }
                DatePicker(L10n.text("Enable at"), selection: Binding(get: { time(policy.startMinute) }, set: { date in update { $0.startMinute = minute(date) } }), displayedComponents: .hourAndMinute)
                DatePicker(L10n.text("Disable at"), selection: Binding(get: { time(policy.endMinute) }, set: { date in update { $0.endMinute = minute(date) } }), displayedComponents: .hourAndMinute)
                if policy.weekdays.isEmpty || policy.startMinute == policy.endMinute {
                    Text(L10n.text("Choose at least one day and different start and stop times.")).font(.caption).foregroundStyle(.orange)
                }
                if !policy.enabled || policy.manualOverride {
                    Text(L10n.text("Manual override is active. Follow schedule to resume automatic recording.")).font(.caption)
                    Button(L10n.text("Follow schedule")) { model.followRecordingSchedule() }
                }
                Text(L10n.text("Times follow this device’s time zone. Overnight windows belong to the day they start."))
                    .font(.caption).foregroundStyle(.secondary)
                Text(L10n.text("Recording starts automatically during scheduled hours while the app is available. iOS cannot wake a suspended or closed app to start the microphone. Open the app to resume. Pair Omi first for pendant capture."))
                    .font(.caption).foregroundStyle(.secondary)
            }
        } header: { Text(L10n.text("Recording schedule")) }
    }
    private func weekday(_ day: Int) -> String {
        let formatter = DateFormatter(); formatter.locale = L10n.locale
        return formatter.weekdaySymbols[day % 7]
    }
}

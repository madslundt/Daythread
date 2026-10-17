import SwiftUI
import UniformTypeIdentifiers
import OmiCore

struct AudioImportView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var importer = RecordingImporter()
    @State private var choosingFiles = false
    @State private var operation: Task<Void, Never>?
    var onImported: () -> Void = {}
    var conversationSourceID: String? = nil

    var body: some View {
        @Bindable var importer = importer
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 12) {
                        Image(systemName: "waveform.badge.plus").font(.largeTitle).foregroundStyle(Theme.accent).accessibilityHidden(true)
                        Text("Bring your recordings along.").font(.title2.weight(.semibold))
                        Text("Import Voice Memos or audio from another recorder. Keep the original moment in your diary.")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }.padding(.vertical, 8)
                    Button { choosingFiles = true } label: {
                        Label(importer.recordings.isEmpty ? "Choose audio files" : "Add more recordings", systemImage: "folder.badge.plus")
                            .frame(maxWidth: .infinity).padding(.vertical, 7)
                    }.buttonStyle(.borderedProminent).disabled(importer.locked)
                        .accessibilityIdentifier("import.chooseFiles")
                } footer: {
                    Text("WAV, M4A, MP3, and other audio formats supported by iPhone. Up to 100 MB and two hours per file.")
                }
                ForEach($importer.recordings) { $recording in
                    recordingSection($recording)
                }
                if !importer.recordings.isEmpty {
                    Section {
                        Toggle("Add to an existing conversation", isOn: $importer.linking)
                        if importer.linking {
                            NavigationLink {
                                ImportConversationPicker(selection: $importer.conversationSourceID)
                            } label: {
                                Label(importer.conversationSourceID.isEmpty ? "Choose a conversation" : "Conversation selected", systemImage: "bubble.left.and.bubble.right")
                            }
                        }
                    } header: {
                        Text("Conversation")
                    } footer: {
                        Text(importer.linking
                             ? "Each recording keeps its original capture time. Use this when you know the sources belong together."
                             : "Overlapping recordings with shared speech are matched after transcription. Every original source is kept.")
                    }.disabled(importer.locked)
                }
                if importer.busy { progressSection }
                if let error = importer.error { Section { ErrorNote(message: error) } }
                if importer.completed + importer.skipped > 0 {
                    Section {
                        Label("\(importer.completed) imported · \(importer.skipped) duplicates skipped", systemImage: "checkmark.circle")
                            .foregroundStyle(Theme.accent)
                        Text("Transcription continues on your home-core. Follow its progress in Capture.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }
            .scrollContentBackground(.hidden).background(Theme.canvas)
            .navigationTitle("Import audio").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }.disabled(importer.busy)
                }
            }
            .safeAreaInset(edge: .bottom) { bottomAction }
            .fileImporter(isPresented: $choosingFiles, allowedContentTypes: audioTypes, allowsMultipleSelection: true) { result in
                switch result {
                case .success(let urls):
                    run {
                        do { await importer.add(urls, client: try model.client()) }
                        catch { importer.error = error.localizedDescription }
                    }
                case .failure(let error): importer.error = error.localizedDescription
                }
            }
        }
        .interactiveDismissDisabled(importer.busy)
        .onAppear {
            if let conversationSourceID, importer.recordings.isEmpty {
                importer.linking = true
                importer.conversationSourceID = conversationSourceID
            }
        }
        .onDisappear { operation?.cancel() }
        .onChange(of: model.archiveIdentity) { _, _ in operation?.cancel(); dismiss() }
        .onChange(of: model.uploadWiFiOnly) { _, _ in operation?.cancel() }
    }

    @ViewBuilder
    private func recordingSection(_ recording: Binding<ImportRecording>) -> some View {
        let value = recording.wrappedValue
        Section {
            if value.completed {
                Label("Imported", systemImage: "checkmark.circle.fill").foregroundStyle(Theme.accent)
            } else if let skipped = value.skipped {
                Label(skipped, systemImage: "doc.on.doc").font(.subheadline).foregroundStyle(.secondary)
            } else {
                Picker("Time refers to", selection: recording.reference) {
                    ForEach(RecordingTimeReference.allCases) { reference in Text(reference.title).tag(reference) }
                }
                DatePicker(value.reference.title, selection: Binding(get: { value.recordedAt }, set: {
                    recording.wrappedValue.recordedAt = $0
                    recording.wrappedValue.timeConfirmed = true
                }), in: ...Date(), displayedComponents: [.date, .hourAndMinute])
                if !value.timeConfirmed {
                    Label("This file has no reliable date. Set when it was recorded.", systemImage: "clock.badge.exclamationmark")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Use the time shown") { recording.wrappedValue.timeConfirmed = true }
                }
                if let error = value.error { ErrorNote(message: error) }
                if !value.locked {
                    Button("Remove recording", role: .destructive) { importer.remove(value.id) }
                }
            }
        } header: {
            Text(value.name + " · " + ByteCountFormatter.string(fromByteCount: Int64(value.size), countStyle: .file))
                .textCase(nil)
        } footer: {
            if !value.handled {
                Text(value.plan != nil
                     ? "The original time and filename are saved for this upload. Retry resumes the same parts."
                     : "Time on this iPhone: \(TimeZone.current.identifier). \(value.suggestedEnd && value.reference == .end ? "The file’s last-modified time is suggested; correct it if needed. " : "")The other end is calculated from the audio duration.")
            }
        }.disabled(importer.busy || value.locked)
    }

    private var progressSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 12) {
                Text(importer.activeName).font(.headline).lineLimit(2)
                if importer.totalBytes > 0 {
                    ProgressView(value: Double(importer.sentBytes), total: Double(importer.totalBytes))
                        .accessibilityLabel("Recording upload progress")
                    Text(importer.confirming ? "Saving this part on your home-core…" : importer.stage)
                        .font(.subheadline)
                    Text("\(ByteCountFormatter.string(fromByteCount: importer.sentBytes, countStyle: .file)) of \(ByteCountFormatter.string(fromByteCount: importer.totalBytes, countStyle: .file)) · \(importer.confirmedParts) of \(importer.partCount) parts saved")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    ProgressView(importer.stage)
                }
                Text("Keep this screen open until the upload finishes. You can pause and retry without duplicating completed parts.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(.vertical, 6)
            Button("Pause import") { operation?.cancel() }
        }
    }

    private var bottomAction: some View {
        VStack(spacing: 8) {
            if model.uploadWiFiOnly && !importer.allHandled {
                Label("Uploads use Wi-Fi only", systemImage: "wifi").font(.caption).foregroundStyle(.secondary)
            }
            Button {
                if importer.allHandled { dismiss() }
                else {
                    run {
                        do { await importer.upload(client: try model.client(), wiFiOnly: model.uploadWiFiOnly, onImported: onImported) }
                        catch { importer.error = error.localizedDescription }
                    }
                }
            } label: {
                Text(importer.allHandled ? "Done" : importer.busy ? "Importing…" : importer.recordings.contains(where: { $0.error != nil }) ? "Retry recordings" : "Import recordings")
                    .fontWeight(.semibold).frame(maxWidth: .infinity).padding(.vertical, 9)
            }.buttonStyle(.borderedProminent)
                .disabled(!importer.allHandled && !importer.canUpload)
                .accessibilityIdentifier("import.upload")
        }.padding().background(.regularMaterial)
    }

    private var audioTypes: [UTType] {
        [.audio, .mpeg4Movie] + ["ogg", "opus", "flac", "webm"].compactMap { UTType(filenameExtension: $0) }
    }

    private func run(_ work: @escaping @MainActor () async -> Void) {
        guard operation == nil else { return }
        operation = Task { await work(); operation = nil }
    }
}

private struct ImportConversationPicker: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Binding var selection: String
    @State private var query = ""
    @State private var items: [JSONValue] = []
    @State private var error: String?
    @State private var loading = false
    @State private var revision = 0

    var body: some View {
        List {
            if loading { ProgressView("Finding conversations…") }
            if let error {
                ErrorNote(message: error)
                Button("Try again") { revision += 1 }
            }
            ForEach(items.filter { $0["state"].text != "expired" }, id: \.["id"].text) { source in
                Button {
                    selection = source["id"].text
                    dismiss()
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(source["title"].text.isEmpty ? source["source_id"].text.isEmpty ? "Recording" : source["source_id"].text : source["title"].text)
                                .font(.headline).foregroundStyle(.primary)
                            if let date = AppModel.date(source["started_at"].text) {
                                Text(date.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                            }
                            if !source["text"].text.isEmpty { Text(source["text"].text).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                        }
                        Spacer()
                        if selection == source["id"].text { Image(systemName: "checkmark").foregroundStyle(Theme.accent) }
                    }.padding(.vertical, 4)
                }
            }
            if !loading && items.isEmpty && error == nil {
                ContentUnavailableView.search(text: query)
            }
        }
        .navigationTitle("Choose conversation")
        .searchable(text: $query, prompt: "Search transcript text")
        .task(id: query + model.archiveIdentity + String(revision)) {
            loading = true; error = nil
            defer { if !Task.isCancelled { loading = false } }
            do {
                try await Task.sleep(for: .milliseconds(250))
                let response = try await model.client().request("v1/transcripts", query: ["limit": "50", "q": query])
                try Task.checkCancellation()
                items = response["items"].array
            } catch {
                if !Task.isCancelled { self.error = error.localizedDescription }
            }
        }
    }
}

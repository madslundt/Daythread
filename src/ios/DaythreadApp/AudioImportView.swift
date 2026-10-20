import SwiftUI
import UniformTypeIdentifiers
import OmiCore

struct AudioImportView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var importer = RecordingImporter()
    @State private var choosingFiles = false
    @State private var operation: Task<Void, Never>?
    @State private var vendors: [VendorDefinition] = [.manual]
    @State private var vendorError: String?
    @State private var vendorRevision = 0
    @State private var availableTags: [ManualTag] = []
    @State private var locationZones: [LocationZone] = []
    @State private var locationChoice = ""
    @State private var currentLocation: CaptureLocation?
    @State private var locations = PhoneLocation()
    @State private var optionsError: String?
    @State private var locationError: String?
    var onImported: () -> Void = {}
    var conversationSourceID: String? = nil
    var sharedURLs: [URL] = []

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        @Bindable var importer = importer
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 12) {
                        Image(systemName: "waveform.badge.plus").font(.largeTitle).foregroundStyle(Theme.accent).accessibilityHidden(true)
                        Text(L10n.text("Bring your recordings along.")).font(.title2.weight(.semibold))
                        Text(L10n.text("Import Voice Memos or audio from another recorder. Keep the original moment in your diary."))
                            .font(.subheadline).foregroundStyle(.secondary)
                    }.padding(.vertical, 8)
                    Picker(L10n.text("Recording brand"), selection: $importer.vendorID) {
                        ForEach(vendors) { vendor in
                            Text(L10n.key(vendor.label)).tag(vendor.id)
                        }
                    }.disabled(importer.locked).accessibilityIdentifier("import.vendor")
                    if let vendor = vendors.first(where: { $0.id == importer.vendorID }) {
                        Text(vendor.capabilities.contains(where: { $0.hasPrefix("transcript_") })
                             ? L10n.text("Export audio from your recorder, then choose the file and its original recording time.")
                             : L10n.key(vendor.importInstructions)).font(.caption).foregroundStyle(.secondary)
                        if let url = URL(string: vendor.setupURL), url.scheme == "https" {
                            Link(L10n.text("Export instructions"), destination: url).font(.caption)
                        }
                    }
                    if let vendorError {
                        Text(vendorError).font(.caption).foregroundStyle(.secondary)
                        Button(L10n.text("Reload recording brands")) { vendorRevision += 1 }
                    }
                    Button { choosingFiles = true } label: {
                        Label(importer.recordings.isEmpty ? L10n.text("Choose audio files") : L10n.text("Add more recordings"), systemImage: "folder.badge.plus")
                            .frame(maxWidth: .infinity).padding(.vertical, 7)
                    }.buttonStyle(.borderedProminent).disabled(importer.locked)
                        .accessibilityIdentifier("import.chooseFiles")
                } footer: {
                    Text(L10n.text("WAV, M4A, MP3, and other audio formats supported by iPhone. Up to twelve hours per file."))
                }
                ForEach($importer.recordings) { $recording in
                    recordingSection($recording)
                }
                if !importer.recordings.isEmpty {
                    Section {
                        DisclosureGroup(L10n.text("Tags")) {
                            if availableTags.isEmpty {
                                Text(L10n.text("No tags available yet.")).font(.caption).foregroundStyle(.secondary)
                            } else {
                                ForEach(availableTags) { tag in
                                    Toggle(tag.name, isOn: Binding(
                                        get: { importer.selectedTagIDs.contains(tag.id) },
                                        set: { selected in
                                            if selected { importer.selectedTagIDs.insert(tag.id) }
                                            else { importer.selectedTagIDs.remove(tag.id) }
                                        }
                                    ))
                                }
                            }
                        }
                        Picker(L10n.text("Location"), selection: $locationChoice) {
                            Text(L10n.text("No location")).tag("")
                            Text(L10n.text("Use current location")).tag("current")
                            ForEach(locationZones) { zone in
                                ForEach(zone.locations) { place in
                                    Text(zone.name + " · " + place.label).tag(zone.id + ":" + place.id)
                                }
                            }
                        }
                        if let locationError { Text(locationError).font(.caption).foregroundStyle(.secondary) }
                        if let optionsError { Text(optionsError).font(.caption).foregroundStyle(.secondary) }
                    } header: {
                        Text(L10n.text("Optional tags and location"))
                    } footer: {
                        Text(L10n.text("Leave these blank to import without tags or a location."))
                    }
                    .disabled(importer.locked)
                }
                if !importer.recordings.isEmpty {
                    Section {
                        Toggle(L10n.text("Add to an existing conversation"), isOn: $importer.linking)
                        if importer.linking {
                            NavigationLink {
                                ImportConversationPicker(selection: $importer.conversationSourceID)
                            } label: {
                                Label(importer.conversationSourceID.isEmpty ? L10n.text("Choose a conversation") : L10n.text("Conversation selected"), systemImage: "bubble.left.and.bubble.right")
                            }
                        }
                    } header: {
                        Text(L10n.text("Conversation"))
                    } footer: {
                        Text(importer.linking
                             ? L10n.text("Each recording keeps its original capture time. Use this when you know the sources belong together.")
                             : L10n.text("Overlapping recordings with shared speech are matched after transcription. Every original source is kept."))
                    }.disabled(importer.locked)
                }
                if importer.busy { progressSection }
                if let error = importer.error { Section { ErrorNote(message: error) } }
                if importer.completed + importer.skipped > 0 {
                    Section {
                        Label(L10n.text("\(importer.completed) imported · \(importer.skipped) duplicates skipped"), systemImage: "checkmark.circle")
                            .foregroundStyle(Theme.accent)
                        Text(L10n.text("Transcription continues on your home-core. Follow its progress in Capture."))
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }
            .scrollContentBackground(.hidden).background(Theme.canvas)
            .navigationTitle(L10n.text("Import audio")).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(L10n.text("Close")) { dismiss() }.disabled(importer.busy)
                }
            }
            .safeAreaInset(edge: .bottom) { bottomAction }
            .fileImporter(isPresented: $choosingFiles, allowedContentTypes: audioTypes, allowsMultipleSelection: true) { result in
                switch result {
                case .success(let urls):
                    run {
                        do { await importer.add(urls, client: try model.client()) }
                        catch { importer.error = L10n.error(error) }
                    }
                case .failure(let error): importer.error = L10n.error(error)
                }
            }
        }
        .interactiveDismissDisabled(importer.busy)
        .task(id: model.archiveIdentity + String(vendorRevision)) {
            vendorError = nil
            do {
                let catalog = try await model.client().vendorCatalog()
                try Task.checkCancellation()
                let available = catalog.fileUploadVendors
                vendors = available.contains(where: { $0.id == "manual" }) ? available : [.manual] + available
            } catch {
                if !Task.isCancelled {
                    vendorError = L10n.text("Recording brands could not be loaded. Other device uploads are still available.")
                }
            }
            await loadOptionalDetails()
        }
        .onAppear {
            if !sharedURLs.isEmpty, importer.recordings.isEmpty {
                run {
                    do {
                        await importer.add(sharedURLs, client: try model.client())
                        for url in sharedURLs { try? FileManager.default.removeItem(at: url) }
                    }
                    catch { importer.error = L10n.error(error) }
                }
            }
            if let conversationSourceID, importer.recordings.isEmpty {
                importer.linking = true
                importer.conversationSourceID = conversationSourceID
            }
        }
        .onDisappear { operation?.cancel() }
        .onChange(of: model.archiveIdentity) { _, _ in operation?.cancel(); dismiss() }
        .onChange(of: model.uploadWiFiOnly) { _, _ in operation?.cancel() }
        .onChange(of: locationChoice) { _, choice in
            if choice.isEmpty {
                currentLocation = nil; importer.currentLocation = nil; importer.selectedPlace = nil; locationError = nil
            } else if choice == "current" {
                importer.selectedPlace = nil
                Task {
                    locationError = nil
                    do {
                        let fix = try await locations.currentLocation()
                        currentLocation = fix; importer.currentLocation = fix
                    } catch {
                        locationChoice = ""; locationError = L10n.error(error)
                    }
                }
            } else if let separator = choice.firstIndex(of: ":") {
                currentLocation = nil; importer.currentLocation = nil
                importer.selectedPlace = (String(choice[..<separator]), String(choice[choice.index(after: separator)...]))
            }
        }
    }

    private func loadOptionalDetails() async {
        let client: BackendClient
        do { client = try model.client() } catch { return }
        do {
            let directory: TagDirectory = try await client.decisionRequest("v1/tags")
            try Task.checkCancellation()
            availableTags = directory.items
        } catch { if !Task.isCancelled { optionsError = L10n.error(error) } }
        do {
            let directory: ZoneDirectory = try await client.decisionRequest("v1/location-zones")
            try Task.checkCancellation()
            locationZones = directory.items
        } catch { if !Task.isCancelled { optionsError = L10n.error(error) } }
    }

    @ViewBuilder
    private func recordingSection(_ recording: Binding<ImportRecording>) -> some View {
        let value = recording.wrappedValue
        Section {
            if value.completed {
                Label(L10n.text("Imported"), systemImage: "checkmark.circle.fill").foregroundStyle(Theme.accent)
            } else if let skipped = value.skipped {
                Label(L10n.key(skipped), systemImage: "doc.on.doc").font(.subheadline).foregroundStyle(.secondary)
            } else {
                Picker(L10n.text("Time refers to"), selection: recording.reference) {
                    ForEach(RecordingTimeReference.allCases) { reference in Text(reference.title).tag(reference) }
                }
                DatePicker(value.reference.title, selection: Binding(get: { value.recordedAt }, set: {
                    recording.wrappedValue.recordedAt = $0
                    recording.wrappedValue.timeConfirmed = true
                }), in: ...Date(), displayedComponents: [.date, .hourAndMinute])
                if !value.timeConfirmed {
                    Label(L10n.text("This file has no reliable date. Set when it was recorded."), systemImage: "clock.badge.exclamationmark")
                        .font(.caption).foregroundStyle(.secondary)
                    Button(L10n.text("Use the time shown")) { recording.wrappedValue.timeConfirmed = true }
                }
                if let error = value.error { ErrorNote(message: error) }
                if !value.locked {
                    Button(L10n.text("Remove recording"), role: .destructive) { importer.remove(value.id) }
                }
            }
        } header: {
            Text(value.name + " · " + ByteCountFormatter.string(fromByteCount: Int64(value.size), countStyle: .file))
                .textCase(nil)
        } footer: {
            if !value.handled {
                Text(value.plan != nil
                     ? L10n.text("The original time and filename are saved for this upload. Retry resumes the same parts.")
                     : L10n.text("Time on this iPhone: \(TimeZone.current.identifier). \(value.suggestedEnd && value.reference == .end ? L10n.text("The file’s last-modified time is suggested; correct it if needed. ") : "")The other end is calculated from the audio duration."))
            }
        }.disabled(importer.busy || value.locked)
    }

    private var progressSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 12) {
                Text(importer.activeName).font(.headline).lineLimit(2)
                if importer.totalBytes > 0 {
                    ProgressView(value: Double(importer.sentBytes), total: Double(importer.totalBytes))
                        .accessibilityLabel(L10n.text("Recording upload progress"))
                    Text(importer.confirming ? L10n.text("Saving this part on your home-core…") : L10n.key(importer.stage))
                        .font(.subheadline)
                    Text(L10n.text("\(ByteCountFormatter.string(fromByteCount: importer.sentBytes, countStyle: .file)) of \(ByteCountFormatter.string(fromByteCount: importer.totalBytes, countStyle: .file)) · \(importer.confirmedParts) of \(importer.partCount) parts saved"))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    ProgressView(importer.stage)
                }
                Text(L10n.text("Keep this screen open until the upload finishes. You can pause and retry without duplicating completed parts."))
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(.vertical, 6)
            Button(L10n.text("Pause import")) { operation?.cancel() }
        }
    }

    private var bottomAction: some View {
        VStack(spacing: 8) {
            if model.uploadWiFiOnly && !importer.allHandled {
                Label(L10n.text("Uploads use Wi-Fi only"), systemImage: "wifi").font(.caption).foregroundStyle(.secondary)
            }
            Button {
                if importer.allHandled { dismiss() }
                else {
                    run {
                        do { await importer.upload(client: try model.client(), wiFiOnly: model.uploadWiFiOnly, onImported: onImported) }
                        catch { importer.error = L10n.error(error) }
                    }
                }
            } label: {
                Text(importer.allHandled ? L10n.text("Done") : importer.busy ? L10n.text("Importing…") : importer.recordings.contains(where: { $0.error != nil }) ? L10n.text("Retry recordings") : L10n.text("Import recordings"))
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

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            if loading { ProgressView(L10n.text("Finding conversations…")) }
            if let error {
                ErrorNote(message: error)
                Button(L10n.text("Try again")) { revision += 1 }
            }
            ForEach(items.filter { $0["state"].text != "expired" }, id: \.["id"].text) { source in
                Button {
                    selection = source["id"].text
                    dismiss()
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(source["title"].text.isEmpty ? source["source_id"].text.isEmpty ? L10n.text("Recording") : source["source_id"].text : source["title"].text)
                                .font(.headline).foregroundStyle(.primary)
                            if let date = AppModel.date(source["started_at"].text) {
                                Text(date.formatted(.dateTime.locale(L10n.locale).day().month(.abbreviated).year().hour().minute())).font(.caption).foregroundStyle(.secondary)
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
        .navigationTitle(L10n.text("Choose conversation"))
        .searchable(text: $query, prompt: L10n.text("Search transcript text"))
        .task(id: query + model.archiveIdentity + String(revision)) {
            loading = true; error = nil
            defer { if !Task.isCancelled { loading = false } }
            do {
                try await Task.sleep(for: .milliseconds(250))
                let response = try await model.client().request("v1/transcripts", query: ["limit": "50", "q": query])
                try Task.checkCancellation()
                items = response["items"].array
            } catch {
                if !Task.isCancelled { self.error = L10n.error(error) }
            }
        }
    }
}

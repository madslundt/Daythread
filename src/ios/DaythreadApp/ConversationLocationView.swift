import SwiftUI

struct ConversationLocationView: View {
    let location: JSONValue
    let manualLocation: JSONValue
    let sourceID: String
    var editable = true
    var onChanged: (() -> Void)? = nil
    @State private var saved: RecordingLocationSelection?
    @Environment(AppModel.self) private var model

    @Environment(\.locale) private var interfaceLocale
    private var place: RecordingPlace? {
        if let saved { return saved.location }
        return RecordingPlace(manualLocation) ?? RecordingPlace(location)
    }
    private var manual: Bool { saved?.manual ?? (manualLocation != .null) }
    var body: some View {
        let _ = interfaceLocale
        Section(L10n.text("Conversation location")) {
            if let place {
                RecordingPlaceSummary(place: place, manual: manual)
                if let url = URL(string: "https://maps.apple.com/?ll=\(place.latitude),\(place.longitude)") {
                    Link(destination: url) { Label(L10n.text("Open in Maps"), systemImage: "map") }
                }
                if !manual {
                    NavigationLink(L10n.text("Save as zone location")) {
                        LocationZonesView(seed: ZonePoint(label: L10n.text("Recording location"), latitude: place.latitude, longitude: place.longitude))
                    }
                    Text(L10n.text("Phone location near the start of this recording. It does not identify places mentioned in the conversation."))
                        .font(.caption).foregroundStyle(.secondary)
                }
            } else { Label(L10n.text("No location"), systemImage: "mappin").foregroundStyle(.secondary) }
            NavigationLink(place == nil ? L10n.text("Add location") : L10n.text("Edit location")) {
                RecordingLocationEditor(sourceID: sourceID) { result in saved = result; onChanged?() }
            }.disabled(!editable)
        }
        .onChange(of: sourceID) { _, _ in saved = nil }
        .onChange(of: model.archiveIdentity) { _, _ in saved = nil }
        .onChange(of: manualLocation) { _, _ in saved = nil }
        .onChange(of: location) { _, _ in saved = nil }
    }
}

private struct RecordingPlaceSummary: View {
    let place: RecordingPlace
    let manual: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "mappin.and.ellipse")
                .font(.title3).foregroundStyle(Theme.accent)
                .frame(width: 30).padding(.top, 3).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) {
                Text(place.label ?? L10n.text("Captured location")).font(.headline)
                if let zone = place.zone_name { Text(zone).font(.subheadline).foregroundStyle(.secondary) }
                if manual {
                    Text(place.zone_id == nil ? L10n.text("Manually set location") : L10n.text("Saved place"))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(String(format: "%.5f, %.5f", place.latitude, place.longitude))
                        .font(.subheadline).foregroundStyle(.secondary).textSelection(.enabled)
                    HStack(spacing: 8) {
                        if let accuracy = place.horizontal_accuracy { Text(L10n.text("\(Int(accuracy)) m")) }
                        if let captured = place.captured_at, let date = AppModel.date(captured) { Text(date, style: .time) }
                    }.font(.caption).foregroundStyle(.secondary)
                }
            }
        }.padding(.vertical, 4).accessibilityElement(children: .combine)
    }
}

struct RecordingPlace: Codable {
    var label: String?
    var latitude: Double
    var longitude: Double
    var horizontal_accuracy: Double? = nil
    var captured_at: String? = nil
    var zone_id: String? = nil
    var location_id: String? = nil
    var zone_name: String? = nil

    init?(_ value: JSONValue) {
        guard case .number(let latitude) = value["latitude"],
              case .number(let longitude) = value["longitude"] else { return nil }
        self.latitude = latitude; self.longitude = longitude
        label = value["label"] == .null ? nil : value["label"].text
        zone_id = value["zone_id"] == .null ? nil : value["zone_id"].text
        location_id = value["location_id"] == .null ? nil : value["location_id"].text
        zone_name = value["zone_name"] == .null ? nil : value["zone_name"].text
        if case .number(let accuracy) = value["horizontal_accuracy"] { horizontal_accuracy = accuracy }
        if value["captured_at"] != .null { captured_at = value["captured_at"].text }
    }
}
struct RecordingLocationSelection: Decodable {
    let version: Int
    let location: RecordingPlace?
    let original_location: RecordingPlace?
    let manual: Bool
}
private struct SavedPlaceReference: Encodable {
    let zone_id: String
    let location_id: String
}
private struct RecordingLocationWrite: Encodable {
    let expected_version: Int
    let location: SavedPlaceReference?
    private enum CodingKeys: String, CodingKey { case expected_version, location }
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(expected_version, forKey: .expected_version)
        try container.encode(location, forKey: .location)
    }
}

private struct RecordingLocationEditor: View {
    let sourceID: String
    let onSaved: (RecordingLocationSelection) -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var selection: RecordingLocationSelection?
    @State private var zones: [LocationZone] = []
    @State private var search = ""
    @State private var busy = false
    @State private var needsReload = false
    @State private var error: String?
    @State private var active = true
    @State private var requestID = UUID()
    @State private var savingPlaceID: String?
    private var path: String { "v1/sources/\(sourceID)/location" }
    private var hasPlaces: Bool { zones.contains { !$0.locations.isEmpty } }
    private var visibleZones: [LocationZone] {
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines)
        return zones.compactMap { zone in
            var result = zone
            result.locations = zone.locations.filter { query.isEmpty ||
                "\(zone.name) \($0.label)".localizedStandardContains(query) }
            return result.locations.isEmpty ? nil : result
        }
    }

    var body: some View {
        List {
            if let selection {
                Section {
                    if let place = selection.location { RecordingPlaceSummary(place: place, manual: selection.manual) }
                    else { Label(L10n.text("No location"), systemImage: "mappin").foregroundStyle(.secondary) }
                } header: { Text(L10n.text("Current location")) }
                footer: {
                    if hasPlaces { Text(L10n.text("Choose a place below. Changes apply immediately.")) }
                }
            }
            if busy && selection == nil { ProgressView(L10n.text("Loading…")) }
            if !busy && error == nil {
                if !hasPlaces {
                    Section {
                        ContentUnavailableView {
                            Label(L10n.text("No saved places yet"), systemImage: "mappin.and.ellipse")
                        } description: {
                            Text(L10n.text("Save a place in Location zones, then return here to choose it."))
                        }
                    }
                } else if visibleZones.isEmpty {
                    Section {
                        ContentUnavailableView {
                            Label(L10n.text("No matching saved places"), systemImage: "magnifyingglass")
                        } actions: {
                            Button(L10n.text("Clear search")) { search = "" }
                        }
                    }
                }
            }
            ForEach(visibleZones) { zone in
                Section(zone.name) {
                    ForEach(zone.locations) { point in
                        let current = selection?.manual == true && selection?.location?.zone_id == zone.id && selection?.location?.location_id == point.id
                        Button {
                            if current, let selection { onSaved(selection); dismiss() }
                            else { Task { await save(SavedPlaceReference(zone_id: zone.id, location_id: point.id)) } }
                        } label: {
                            HStack(spacing: 12) {
                                Text(point.label).foregroundStyle(.primary).multilineTextAlignment(.leading)
                                Spacer(minLength: 4)
                                if busy && savingPlaceID == point.id { ProgressView() }
                                else if current {
                                    Label(L10n.text("Current"), systemImage: "checkmark.circle.fill")
                                        .font(.caption.weight(.semibold)).foregroundStyle(Theme.accent).fixedSize()
                                } else {
                                    Image(systemName: "circle").foregroundStyle(.tertiary).accessibilityHidden(true)
                                }
                            }.padding(.vertical, 5).contentShape(Rectangle())
                        }
                        .accessibilityAddTraits(current ? .isSelected : [])
                        .listRowBackground(current ? Theme.accent.opacity(0.1) : Theme.surface)
                    }
                }.disabled(busy || selection == nil || needsReload)
            }
            Section {
                NavigationLink { LocationZonesView() } label: {
                    Label(L10n.text("Manage saved places"), systemImage: "plus")
                }
            }.disabled(busy)
            if let selection, selection.manual {
                Section {
                    Button {
                        Task { await save(nil) }
                    } label: {
                        Label(selection.original_location == nil ? L10n.text("Remove location") : L10n.text("Restore captured location"),
                              systemImage: selection.original_location == nil ? "xmark.circle" : "arrow.uturn.backward")
                    }.disabled(needsReload || busy)
                } footer: {
                    if selection.original_location != nil { Text(L10n.text("The original captured location is kept.")) }
                }
            }
            if busy && selection != nil { ProgressView(L10n.text("Saving…")) }
            if let error {
                Section {
                    Text(error).foregroundStyle(.red)
                    Button(L10n.text("Retry")) { Task { await load() } }.disabled(busy)
                }
            }
        }
        .searchable(text: $search, prompt: L10n.text("Search saved places"))
        .navigationTitle(L10n.text("Choose a saved place")).navigationBarTitleDisplayMode(.inline)
        .task(id: model.archiveIdentity + sourceID) { active = true; selection = nil; zones = []; await load() }
        .onDisappear { active = false; requestID = UUID() }
    }

    private func load() async {
        let token = UUID(), identity = model.archiveIdentity
        requestID = token; busy = true; error = nil; needsReload = false; savingPlaceID = nil
        defer { if requestID == token { busy = false } }
        do {
            let client = try model.client()
            async let recording: RecordingLocationSelection = client.decisionRequest(path)
            async let directory: ZoneDirectory = client.decisionRequest("v1/location-zones")
            let (result, available) = try await (recording, directory)
            guard active, !Task.isCancelled, requestID == token, identity == model.archiveIdentity else { return }
            selection = result; zones = available.items
        } catch {
            if active, requestID == token, identity == model.archiveIdentity {
                selection = nil; self.error = L10n.error(error)
            }
        }
    }

    private func save(_ location: SavedPlaceReference?) async {
        guard let selection, !busy, !needsReload else { return }
        let token = UUID(), identity = model.archiveIdentity
        requestID = token; busy = true; error = nil; savingPlaceID = location?.location_id
        defer { if requestID == token { busy = false; savingPlaceID = nil } }
        do {
            let result: RecordingLocationSelection = try await model.client().decisionRequest(path, method: "PUT",
                body: RecordingLocationWrite(expected_version: selection.version, location: location))
            guard active, !Task.isCancelled, requestID == token, identity == model.archiveIdentity else { return }
            onSaved(result); dismiss()
        } catch {
            if active, requestID == token, identity == model.archiveIdentity {
                self.error = L10n.error(error)
                let status = (error as? BackendFailure)?.status
                needsReload = status == 409 || status == 404
            }
        }
    }
}

import SwiftUI
import MapKit
import OmiCore

struct ZonePoint: Codable, Identifiable {
    var id = UUID().uuidString
    var label: String
    var latitude: Double
    var longitude: Double
    var radius_m: Double = 200
    var valid: Bool {
        !label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && label.count <= 100 &&
        latitude.isFinite && longitude.isFinite && radius_m.isFinite &&
        (-90...90).contains(latitude) && (-180...180).contains(longitude) && (10...100000).contains(radius_m)
    }
}
struct LocationZone: Codable, Identifiable {
    var id = UUID().uuidString
    var name: String
    var version = 0
    var locations: [ZonePoint] = []
}
struct ZoneDirectory: Decodable { let items: [LocationZone] }
private struct ZoneWrite: Encodable {
    let name: String
    let locations: [ZonePoint]
    let expected_version: Int
}

struct LocationZonesView: View {
    var seed: ZonePoint? = nil
    @Environment(AppModel.self) private var model
    @State private var zones: [LocationZone] = []
    @State private var editor: LocationZone?
    @State private var deleting: LocationZone?
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        List {
            Section {
                Text(L10n.text("Group places as Home, Work, Gym, or any name you choose. Each zone can contain several locations, each with its own radius."))
                Text(L10n.text("Zones belong to this profile. Changes apply to past recordings without changing their original GPS observations. Overlapping zones can both match. Recordings without GPS stay unclassified."))
                    .font(.caption).foregroundStyle(.secondary)
                Button(L10n.text("Create zone")) { editor = LocationZone(name: "", locations: seed.map { [$0] } ?? []) }
            }
            ForEach(zones) { zone in
                Section(zone.name) {
                    ForEach(zone.locations) { point in
                        LabeledContent(point.label, value: L10n.text("\(Int(point.radius_m)) m"))
                    }
                    Button(L10n.text("Edit zone")) { editor = zone }
                    if let seed {
                        Button(L10n.text("Add recording location")) {
                            var updated = zone, added = seed
                            added.id = UUID().uuidString
                            updated.locations.append(added); editor = updated
                        }.disabled(zone.locations.count >= 30)
                    }
                    Button(L10n.text("Delete zone"), role: .destructive) { deleting = zone }
                }
            }
            if busy { ProgressView() }
            if let error {
                Text(error).foregroundStyle(.secondary)
                Button(L10n.text("Reload zones")) { Task { await load() } }
            }
        }
        .disabled(busy)
        .navigationTitle(L10n.text("Location zones")).navigationBarTitleDisplayMode(.inline)
        .task(id: model.archiveIdentity) { editor = nil; zones = []; await load() }
        .sheet(item: $editor) { zone in LocationZoneEditor(zone: zone) { Task { await load() } } }
        .confirmationDialog(L10n.text("Delete this zone? Original recording locations are kept."), isPresented: Binding(
            get: { deleting != nil }, set: { if !$0 { deleting = nil } }
        ), titleVisibility: .visible) {
            if let deleting { Button(L10n.text("Delete zone"), role: .destructive) { Task { await remove(deleting) } } }
        }
    }

    private func load() async {
        busy = true; error = nil
        let identity = model.archiveIdentity
        defer { if identity == model.archiveIdentity { busy = false } }
        do {
            let result: ZoneDirectory = try await model.client().decisionRequest("v1/location-zones")
            guard !Task.isCancelled, identity == model.archiveIdentity else { return }
            zones = result.items
        } catch { if !Task.isCancelled, identity == model.archiveIdentity { self.error = L10n.error(error) } }
    }
    private func remove(_ zone: LocationZone) async {
        busy = true; error = nil; deleting = nil
        let identity = model.archiveIdentity
        defer { if identity == model.archiveIdentity { busy = false } }
        do {
            _ = try await model.client().data(path: "v1/location-zones/\(zone.id)", method: "DELETE", query: ["expected_version": "\(zone.version)"], profileHeader: model.selectedProfileID)
            guard !Task.isCancelled, identity == model.archiveIdentity else { return }
            zones.removeAll { $0.id == zone.id }
        } catch { if identity == model.archiveIdentity { self.error = L10n.error(error) } }
    }
}

private struct LocationZoneEditor: View {
    @State var zone: LocationZone
    let onSaved: () -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var pointEditor: ZonePoint?
    @State private var busy = false
    @State private var error: String?
    @State private var active = true

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField(L10n.text("Zone name"), text: $zone.name)
                    ForEach(zone.locations) { point in
                        HStack {
                            Button { pointEditor = point } label: {
                                VStack(alignment: .leading) {
                                    Text(point.label)
                                    Text(L10n.text("\(Int(point.radius_m)) m")).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            Button(role: .destructive) { zone.locations.removeAll { $0.id == point.id } } label: {
                                Image(systemName: "trash").accessibilityLabel(L10n.text("Remove location"))
                            }.buttonStyle(.borderless)
                        }
                    }
                    Button(L10n.text("Add location")) {
                        pointEditor = ZonePoint(label: "", latitude: 0, longitude: 0)
                    }.disabled(zone.locations.count >= 30)
                }
                Text(L10n.text("Matching uses the recorded GPS point and is approximate. Check its accuracy when choosing a small radius."))
                    .font(.caption).foregroundStyle(.secondary)
                if let error { Text(error).foregroundStyle(.red) }
            }
            .disabled(busy)
            .navigationTitle(L10n.text("Edit zone")).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() }.disabled(busy) }
                ToolbarItem(placement: .confirmationAction) {
                    Button(L10n.text("Save zone")) { Task { await save() } }
                        .disabled(busy || zone.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || zone.name.count > 100 || zone.locations.isEmpty || !zone.locations.allSatisfy(\.valid))
                }
            }
            .sheet(item: $pointEditor) { point in ZonePointEditor(point: point) { updated in
                if let index = zone.locations.firstIndex(where: { $0.id == updated.id }) { zone.locations[index] = updated }
                else { zone.locations.append(updated) }
            } }
        }
        .interactiveDismissDisabled(busy)
        .onDisappear { active = false }
    }
    private func save() async {
        busy = true; error = nil
        let identity = model.archiveIdentity
        defer { busy = false }
        do {
            let _: LocationZone = try await model.client().decisionRequest("v1/location-zones/\(zone.id)", method: "PUT",
                body: ZoneWrite(name: zone.name, locations: zone.locations, expected_version: zone.version))
            guard active, !Task.isCancelled, identity == model.archiveIdentity else { return }
            onSaved(); dismiss()
        } catch { if active, identity == model.archiveIdentity { self.error = L10n.error(error) } }
    }
}

private struct ZonePointEditor: View {
    @State var point: ZonePoint
    let onSave: (ZonePoint) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var results: [MKMapItem] = []
    @State private var locations = PhoneLocation()
    @State private var search: MKLocalSearch?
    @State private var busy = false
    @State private var error: String?
    @State private var active = true
    @State private var latitude = ""
    @State private var longitude = ""
    private var valid: Bool {
        guard let lat = coordinate(latitude), let lon = coordinate(longitude) else { return false }
        var candidate = point; candidate.latitude = lat; candidate.longitude = lon
        return candidate.valid
    }
    private func coordinate(_ text: String) -> Double? { Double(text.replacingOccurrences(of: ",", with: ".")) }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField(L10n.text("Location name"), text: $point.label)
                    TextField(L10n.text("Place or address"), text: $query).submitLabel(.search)
                        .onSubmit { Task { await find() } }
                    Button(L10n.text("Search places")) { Task { await find() } }.disabled(busy || query.isEmpty)
                    Button(L10n.text("Use current location")) { Task { await locate() } }.disabled(busy)
                    ForEach(Array(results.enumerated()), id: \.offset) { _, item in
                        Button {
                            point.label = item.name ?? query
                            latitude = String(item.placemark.coordinate.latitude); longitude = String(item.placemark.coordinate.longitude)
                            results = []
                        } label: { VStack(alignment: .leading) {
                            Text(item.name ?? query)
                            Text(item.placemark.title ?? "").font(.caption).foregroundStyle(.secondary)
                        } }
                    }
                    TextField(L10n.text("Latitude"), text: $latitude).keyboardType(.numbersAndPunctuation)
                    TextField(L10n.text("Longitude"), text: $longitude).keyboardType(.numbersAndPunctuation)
                    TextField(L10n.text("Radius in metres"), value: $point.radius_m, format: .number).keyboardType(.decimalPad)
                } footer: { Text(L10n.text("Searches and map previews use Apple Maps. Coordinates can also come from a recording or current GPS.")) }
                if valid, let lat = coordinate(latitude), let lon = coordinate(longitude) {
                    let center = CLLocationCoordinate2D(latitude: lat, longitude: lon)
                    Map(initialPosition: .region(MKCoordinateRegion(center: center, latitudinalMeters: point.radius_m * 5, longitudinalMeters: point.radius_m * 5))) {
                        Marker(point.label, coordinate: center)
                        MapCircle(center: center, radius: point.radius_m).foregroundStyle(.blue.opacity(0.2))
                    }.frame(height: 200).id("\(lat):\(lon):\(point.radius_m)")
                }
                if busy { ProgressView() }
                if let error { Text(error).foregroundStyle(.red) }
            }
            .navigationTitle(L10n.text("Zone location")).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(L10n.text("Done")) {
                        guard let lat = coordinate(latitude), let lon = coordinate(longitude), valid else { return }
                        point.latitude = lat; point.longitude = lon; onSave(point); dismiss()
                    }.disabled(!valid || busy)
                }
            }
        }
        .onAppear { if !point.label.isEmpty { latitude = String(point.latitude); longitude = String(point.longitude) } }
        .onDisappear { active = false; search?.cancel(); locations.cancelRequest() }
    }
    private func find() async {
        busy = true; error = nil; results = []
        defer { busy = false }
        let request = MKLocalSearch.Request(); request.naturalLanguageQuery = query
        let operation = MKLocalSearch(request: request); search = operation
        do {
            let response = try await operation.start()
            guard active, !Task.isCancelled else { return }
            results = Array(response.mapItems.prefix(10))
            if results.isEmpty { error = L10n.text("No places found. Try a more specific address.") }
        } catch { if active { self.error = L10n.text("Place search failed. Try again.") } }
    }
    private func locate() async {
        busy = true; error = nil
        defer { busy = false }
        do {
            let fix = try await locations.currentLocation()
            guard active, !Task.isCancelled else { return }
            latitude = String(fix.latitude); longitude = String(fix.longitude)
            if point.label.isEmpty { point.label = L10n.text("Current location") }
        } catch { if active { self.error = L10n.error(error) } }
    }
}

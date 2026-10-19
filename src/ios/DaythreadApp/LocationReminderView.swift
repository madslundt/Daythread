import SwiftUI
import MapKit
import EventKit
import OmiCore

private struct ReminderPlace {
    let title: String
    let address: String
    let coordinate: CLLocationCoordinate2D
}

/// Resolve ambiguous spoken places explicitly; coordinates never come from the language model.
struct LocationReminderView: View {
    let origin: FollowUpOrigin
    let suggestion: DecisionFollowUp
    let timezone: String
    let onSaved: () -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var query: String
    @State private var results: [MKMapItem] = []
    @State private var selected: ReminderPlace?
    @State private var radius = 200.0
    @State private var locations = PhoneLocation()
    @State private var search: MKLocalSearch?
    @State private var searching = false
    @State private var saving = false
    @State private var error: String?
    @State private var active = true
    @State private var searchID = UUID()
    @State private var store = EKEventStore()

    init(record: DecisionRecord, suggestion: DecisionFollowUp, timezone: String, onSaved: @escaping () -> Void) {
        self.origin = .decision(record); self.suggestion = suggestion; self.timezone = timezone; self.onSaved = onSaved
        _query = State(initialValue: suggestion.location)
    }

    init(item: InboxItem, suggestion: DecisionFollowUp, timezone: String, onSaved: @escaping () -> Void) {
        self.origin = .inbox(item); self.suggestion = suggestion; self.timezone = timezone; self.onSaved = onSaved
        _query = State(initialValue: suggestion.location)
    }

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            Form {
                Section {
                    Text(suggestion.title).font(.headline)
                    Text(suggestion.location).foregroundStyle(.secondary)
                    if !suggestion.reason.isEmpty { Text(suggestion.reason).font(.caption) }
                } header: { Text(L10n.text("From your conversation")) }
                Section {
                    TextField(L10n.text("Place or address"), text: $query).submitLabel(.search)
                        .onSubmit { Task { await find() } }
                        .onChange(of: query) { _, _ in
                            searchID = UUID(); search?.cancel(); locations.cancelRequest(); searching = false; results = []; selected = nil
                        }
                    Button(L10n.text("Search places")) { Task { await find() } }
                        .disabled(searching || query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    Button(L10n.text("Use current location")) { Task { await useCurrentLocation() } }.disabled(searching)
                    if searching { ProgressView(L10n.text("Finding location…")) }
                    ForEach(Array(results.enumerated()), id: \.offset) { _, item in
                        Button {
                            selected = ReminderPlace(title: item.name ?? query, address: item.placemark.title ?? "",
                                                     coordinate: item.placemark.coordinate)
                        } label: {
                            VStack(alignment: .leading) {
                                Text(item.name ?? query)
                                Text(item.placemark.title ?? "").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                } header: { Text(L10n.text("Choose the exact place")) } footer: {
                    Text(L10n.text("Place searches and map previews use Apple Maps. Your current GPS location is used only when you choose it. The confirmed place is saved in Apple Reminders."))
                }
                if let selected {
                    Section {
                        Text(selected.title).font(.headline)
                        Text(selected.address).font(.caption)
                        Map(initialPosition: .region(MKCoordinateRegion(center: selected.coordinate,
                            latitudinalMeters: radius * 5, longitudinalMeters: radius * 5))) {
                            Marker(selected.title, coordinate: selected.coordinate)
                            MapCircle(center: selected.coordinate, radius: radius).foregroundStyle(.blue.opacity(0.15))
                        }.frame(height: 180).id("\(selected.coordinate.latitude):\(selected.coordinate.longitude):\(radius)")
                        Picker(L10n.text("Notify within"), selection: $radius) {
                            Text(L10n.text("100 m")).tag(100.0)
                            Text(L10n.text("200 m")).tag(200.0)
                            Text(L10n.text("500 m")).tag(500.0)
                            Text(L10n.text("1 km")).tag(1000.0)
                        }
                        Text(LocalizedStringKey(suggestion.location_trigger == .leave ? L10n.text("Remind me when I leave this area") : L10n.text("Remind me when I arrive near this place")))
                    } header: { Text(L10n.text("Location alert")) } footer: {
                        Text(L10n.text("Apple Reminders handles the alert while Daythread is closed. Enable location alerts and notifications for Reminders in iPhone Settings. Arrival timing and distance are approximate."))
                    }
                }
                if let error { Section { Text(L10n.key(error)).foregroundStyle(.red) } }
            }
            .disabled(saving)
            .navigationTitle(L10n.text("Nearby reminder")).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() }.disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    Button(L10n.text("Save to Reminders")) { Task { await save() } }.disabled(selected == nil || saving || searching)
                }
            }
        }
        .interactiveDismissDisabled(saving)
        .onDisappear { active = false; searchID = UUID(); search?.cancel(); locations.cancelRequest() }
    }

    private func find() async {
        locations.cancelRequest()
        search?.cancel()
        let token = UUID(); searchID = token; searching = true; selected = nil; results = []; error = nil
        defer { if searchID == token { searching = false } }
        let request = MKLocalSearch.Request(); request.naturalLanguageQuery = query
        let operation = MKLocalSearch(request: request); search = operation
        do {
            let response = try await operation.start()
            guard active, searchID == token, !Task.isCancelled else { return }
            results = Array(response.mapItems.prefix(10))
            if results.isEmpty { error = String(localized: "No places found. Try a more specific address.") }
        } catch { if active, searchID == token { self.error = String(localized: "Place search failed. Try again.") } }
    }

    private func useCurrentLocation() async {
        search?.cancel()
        let token = UUID(); searchID = token; searching = true; selected = nil; error = nil; results = []
        defer { if searchID == token { searching = false } }
        do {
            let fix = try await locations.currentLocation()
            guard active, searchID == token, !Task.isCancelled else { return }
            selected = ReminderPlace(title: String(localized: "Current location"),
                address: String(format: L10n.text("%.5f, %.5f · ±%.0f m"), fix.latitude, fix.longitude, fix.horizontalAccuracy),
                coordinate: CLLocationCoordinate2D(latitude: fix.latitude, longitude: fix.longitude))
        } catch { if active, searchID == token { self.error = L10n.error(error) } }
    }

    private func save() async {
        guard let selected, !saving, active else { return }
        saving = true; error = nil
        let identity = model.archiveIdentity, revision = model.archiveRevision
        defer { saving = false }
        do {
            let client = try model.client()
            try await validate(client)
            guard active, !Task.isCancelled, identity == model.archiveIdentity, revision == model.archiveRevision else { return }
            let status = EKEventStore.authorizationStatus(for: .reminder)
            if status == .notDetermined {
                guard try await store.requestFullAccessToReminders() else {
                    throw BackendFailure(message: String(localized: "Reminders access is unavailable. Enable it in iPhone Settings."))
                }
            } else if status != .fullAccess {
                throw BackendFailure(message: String(localized: "Reminders access is unavailable. Enable it in iPhone Settings."))
            }
            try await validate(client)
            guard active, !Task.isCancelled, identity == model.archiveIdentity, revision == model.archiveRevision else { return }
            guard let list = store.defaultCalendarForNewReminders(), list.allowsContentModifications else {
                throw BackendFailure(message: String(localized: "No writable default Reminders list is available. Set up a list in Reminders, then try again."))
            }
            let reminder = EKReminder(eventStore: store)
            reminder.calendar = list; reminder.title = suggestion.title
            reminder.notes = [suggestion.notes, suggestion.reason, selected.address,
                origin.provenance].filter { !$0.isEmpty }.joined(separator: "\n\n")
            if let due = DecisionFollowUp.date(suggestion.due_at, allDay: suggestion.all_day,
                                               timeZone: TimeZone(identifier: timezone) ?? .current) {
                var calendar = Calendar(identifier: .gregorian)
                calendar.timeZone = TimeZone(identifier: timezone) ?? .current
                var components = calendar.dateComponents(suggestion.all_day ? [.year, .month, .day] : [.year, .month, .day, .hour, .minute], from: due)
                components.timeZone = calendar.timeZone; reminder.dueDateComponents = components
                if !suggestion.all_day { reminder.addAlarm(EKAlarm(absoluteDate: due)) }
            }
            let place = EKStructuredLocation(title: selected.title)
            place.geoLocation = CLLocation(latitude: selected.coordinate.latitude, longitude: selected.coordinate.longitude)
            place.radius = radius
            let alarm = EKAlarm(); alarm.structuredLocation = place
            alarm.proximity = suggestion.location_trigger == .leave ? .leave : .enter
            reminder.addAlarm(alarm)
            try store.save(reminder, commit: true)
            onSaved(); dismiss()
        } catch { if active, identity == model.archiveIdentity, revision == model.archiveRevision { self.error = L10n.error(error) } }
    }

    private func validate(_ client: BackendClient) async throws {
        try await origin.validate(client)
    }
}

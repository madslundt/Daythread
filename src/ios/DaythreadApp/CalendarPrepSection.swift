import EventKit
import SwiftUI

/// Calendar metadata is local draft context. Selection never verifies attendance.
struct CalendarPrepSection: View {
    let onChoose: (String, String, String) -> Void
    @Environment(\.locale) private var interfaceLocale
    @Environment(\.scenePhase) private var phase
    @State private var store = EKEventStore()
    @State private var events: [Candidate] = []
    @State private var busy = false
    @State private var loaded = false
    @State private var error: String?
    @State private var operation: Task<Void, Never>?

    private struct Candidate: Identifiable {
        let id: String
        let title: String
        let start: Date
        let end: Date
        let people: [String]
        var context: String {
            let formatter = ISO8601DateFormatter()
            return title + "\n" + formatter.string(from: start) + " – " + formatter.string(from: end)
        }
    }

    var body: some View {
        let _ = interfaceLocale
        Section {
            Button(L10n.text("Choose an upcoming calendar event")) {
                operation?.cancel()
                operation = Task { await load() }
            }.disabled(busy)
            Text(L10n.text("Calendar access is optional. Choose a topic or attendee, then review recordings before preparing. Calendar details do not verify attendance."))
                .font(.caption).foregroundStyle(.secondary)
            if busy { ProgressView() }
            if let error { Text(error).font(.caption).foregroundStyle(.red) }
            if loaded && events.isEmpty { Text(L10n.text("No upcoming events in the next seven days.")) }
            ForEach(events) { event in
                DisclosureGroup {
                    Button(L10n.text("Use meeting topic")) { onChoose(event.title, "topic", event.context) }
                    ForEach(event.people, id: \.self) { name in
                        Button(name) { onChoose(name, "person", event.context) }
                    }
                } label: {
                    VStack(alignment: .leading) {
                        Text(event.title)
                        Text(event.start, style: .date).font(.caption).foregroundStyle(.secondary)
                        Text(event.start, style: .time).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        } header: { Text(L10n.text("Prepare from a calendar event")) }
            .onDisappear { operation?.cancel() }
            .onChange(of: phase) { _, value in
                if value == .active && EKEventStore.authorizationStatus(for: .event) != .fullAccess {
                    operation?.cancel(); events = []; loaded = false
                }
            }
    }

    @MainActor private func load() async {
        busy = true; error = nil; events = []; loaded = false
        defer { busy = false }
        do {
            let status = EKEventStore.authorizationStatus(for: .event)
            if status != .fullAccess {
                guard try await store.requestFullAccessToEvents() else {
                    error = L10n.text("Calendar access is unavailable. You can still prepare by entering a name or topic.")
                    return
                }
            }
            guard !Task.isCancelled else { return }
            let start = Date()
            guard let end = Calendar.current.date(byAdding: .day, value: 7, to: start) else { return }
            let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
            events = store.events(matching: predicate)
                .filter { event in
                    event.status != .canceled && !event.isAllDay &&
                    !(event.attendees ?? []).contains { $0.isCurrentUser && $0.participantStatus == .declined }
                }
                .sorted { $0.startDate < $1.startDate }
                .prefix(50)
                .compactMap { event in
                    let title = (event.title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !title.isEmpty else { return nil }
                    var names: [String] = []
                    for person in event.attendees ?? [] where person.participantStatus != .declined && !person.isCurrentUser {
                        let name = (person.name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                        if !name.isEmpty && !names.contains(name) { names.append(String(name.prefix(120))) }
                    }
                    return Candidate(id: event.calendarItemIdentifier + String(event.startDate.timeIntervalSince1970),
                                     title: String(title.prefix(200)), start: event.startDate,
                                     end: event.endDate, people: Array(names.prefix(30)))
                }
            loaded = true
        } catch {
            if !Task.isCancelled { self.error = L10n.error(error) }
        }
    }
}

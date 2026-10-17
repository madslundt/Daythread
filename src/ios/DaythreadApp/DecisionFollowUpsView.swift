import SwiftUI
import EventKit
import EventKitUI
import OmiCore

extension DecisionRecord {
    var canSuggestFollowUps: Bool {
        content != nil && evidence_condition == "current" && review_status != "dismissed" && successor_id == nil
    }
}

struct DecisionFollowUpLinks: View {
    let record: DecisionRecord
    @Binding var saved: Set<String>
    @Binding var nativeActionActive: Bool
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @State private var response: DecisionFollowUps?
    @State private var error: String?
    @State private var loading = false
    @State private var exporting: String?
    @State private var invalid = false
    @State private var requestID = UUID()
    @State private var exportID = UUID()
    @State private var store = EKEventStore()
    @State private var composer: CalendarComposition?
    @State private var nearby: NearbyComposition?

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 8) {
            if let response {
                ForEach(Array(response.suggestions.enumerated()), id: \.offset) { _, suggestion in
                    if suggestion.hasLocationTrigger {
                        action((suggestion.location_trigger == .leave ? String(localized: "When leaving") : String(localized: "When nearby")) + " · " + suggestion.location,
                               suggestion: suggestion, timezone: response.timezone)
                    } else if let date = suggestion.actionDate(timeZone: TimeZone(identifier: response.timezone) ?? .current) {
                        action(linkTitle(date, allDay: suggestion.all_day, timezone: response.timezone), suggestion: suggestion, timezone: response.timezone)
                    }
                }
            }
            if loading { ProgressView().controlSize(.mini).accessibilityLabel(L10n.text("Finding follow-ups")) }
            if let error {
                Text(L10n.key(error)).font(.caption).foregroundStyle(.secondary)
                if !invalid {
                    Button(L10n.text("Retry")) { Task { await load(force: true) } }
                        .buttonStyle(.plain).foregroundStyle(.tint).underline()
                        .disabled(loading || exporting != nil)
                }
            }
        }
        .font(.subheadline)
        .task(id: model.archiveIdentity + model.archiveRevision + record.id + "\(record.version)") { await load() }
        .sheet(item: $composer, onDismiss: { nativeActionActive = false }) { composition in
            NativeEventComposer(composition: composition) { action in
                if action == .saved, composition.identity == model.archiveIdentity, composition.revision == model.archiveRevision {
                    saved.insert(composition.saveKey)
                }
                composer = nil; nativeActionActive = false
            }
        }
        .sheet(item: $nearby, onDismiss: { nativeActionActive = false }) { selection in
            LocationReminderView(record: record, suggestion: selection.suggestion, timezone: selection.timezone) {
                saved.insert(selection.suggestion.saveKey(record))
            }
        }
        .onChange(of: model.archiveIdentity) { _, _ in invalidate() }
        .onChange(of: model.archiveRevision) { _, _ in invalidate() }
        .onChange(of: phase) { _, value in
            if value == .background { invalidate() }
            if value == .active, response == nil { Task { await load(force: true) } }
        }
        .onDisappear { requestID = UUID(); exportID = UUID(); loading = false; exporting = nil; nativeActionActive = false }
    }

    private func linkTitle(_ date: Date, allDay: Bool, timezone: String) -> String {
        let formatter = DateFormatter(); formatter.locale = L10n.locale
        formatter.timeZone = TimeZone(identifier: timezone) ?? .current
        var calendar = Calendar.current; calendar.timeZone = formatter.timeZone
        let sameYear = calendar.component(.year, from: date) == calendar.component(.year, from: Date())
        formatter.setLocalizedDateFormatFromTemplate(sameYear ? "d MMM" : "d MMM y")
        var label = formatter.string(from: date)
        if !allDay {
            formatter.setLocalizedDateFormatFromTemplate("j:mm")
            label += " · " + formatter.string(from: date)
            if formatter.timeZone.identifier != TimeZone.current.identifier {
                label += " " + (formatter.timeZone.abbreviation(for: date) ?? timezone)
            }
        }
        return label
    }

    private func action(_ title: String, suggestion: DecisionFollowUp, timezone: String) -> some View {
        let key = suggestion.saveKey(record)
        return HStack(spacing: 5) {
            if saved.contains(key) {
                Label(title, systemImage: "checkmark.circle").foregroundStyle(.secondary)
                    .accessibilityLabel(L10n.text("Added \(suggestion.title) to \(suggestion.kind == .event ? L10n.text("Calendar") : L10n.text("Reminders"))"))
            } else {
                Button {
                    if suggestion.hasLocationTrigger {
                        nativeActionActive = true
                        nearby = NearbyComposition(suggestion: suggestion, timezone: timezone)
                    } else { Task { await export(suggestion, timezone: timezone) } }
                } label: {
                    Text(title).underline()
                }
                .buttonStyle(.plain).foregroundStyle(.tint)
                .accessibilityLabel(L10n.text("Add \(suggestion.title) to \(suggestion.kind == .event ? L10n.text("Calendar") : L10n.text("Reminders"))"))
                .accessibilityValue(title + " · " + timezone)
                .disabled(exporting != nil || invalid)
                if exporting == key { ProgressView().controlSize(.mini) }
            }
        }
    }

    private func invalidate() {
        requestID = UUID(); exportID = UUID(); response = nil; composer = nil; nearby = nil
        loading = false; exporting = nil; invalid = true; nativeActionActive = false
    }

    private func currentDecision(client: BackendClient) async throws {
        let latest: DecisionRecord = try await client.decisionRequest("v1/decisions/\(record.id)")
        guard latest.version == record.version, latest.canSuggestFollowUps else {
            invalid = true; response = nil
            throw BackendFailure(message: L10n.text("This decision changed or its evidence is no longer current. Refresh the decision."))
        }
    }

    private func load(force: Bool = false) async {
        guard force || response == nil else { return }
        let token = UUID(); requestID = token; loading = true; response = nil; error = nil; invalid = false
        let identity = model.archiveIdentity, revision = model.archiveRevision
        defer { if requestID == token { loading = false } }
        do {
            let client = try model.client()
            try await currentDecision(client: client)
            guard !Task.isCancelled, !invalid, requestID == token, model.archiveIdentity == identity, model.archiveRevision == revision else { return }
            let result: DecisionFollowUps = try await client.decisionRequest("v1/decisions/\(record.id)/suggestions", method: "POST", body: DecisionSuggestionRequest(expected_version: record.version))
            guard !Task.isCancelled, !invalid, requestID == token, model.archiveIdentity == identity, model.archiveRevision == revision else { return }
            guard result.decision_id == record.id, result.decision_version == record.version, TimeZone(identifier: result.timezone) != nil else {
                throw BackendFailure(message: L10n.text("The suggestions no longer match this decision. Refresh and try again."))
            }
            response = result
        } catch {
            if !Task.isCancelled, requestID == token, model.archiveIdentity == identity, model.archiveRevision == revision { self.error = L10n.error(error) }
        }
    }

    private func export(_ suggestion: DecisionFollowUp, timezone: String) async {
        let key = suggestion.saveKey(record)
        guard exporting == nil, !invalid, !saved.contains(key), suggestion.actionDate(timeZone: TimeZone(identifier: timezone) ?? .current) != nil else { return }
        let token = UUID(); exportID = token; exporting = key; nativeActionActive = true; error = nil
        let identity = model.archiveIdentity, revision = model.archiveRevision
        defer { if exportID == token { exporting = nil; nativeActionActive = composer != nil } }
        do {
            let client = try model.client()
            // Check before requesting permission and again after the asynchronous prompt.
            try await currentDecision(client: client)
            guard !Task.isCancelled, exportID == token, model.archiveIdentity == identity, model.archiveRevision == revision else { return }
            if suggestion.kind == .reminder {
                let status = EKEventStore.authorizationStatus(for: .reminder)
                if status == .notDetermined {
                    guard try await store.requestFullAccessToReminders() else {
                        throw BackendFailure(message: L10n.text("Reminders access was declined. Enable access for Daythread in Settings to save a reminder."))
                    }
                } else if status != .fullAccess {
                    throw BackendFailure(message: L10n.text("Reminders access is unavailable. Check Daythread's Reminders access in Settings."))
                }
                try await currentDecision(client: client)
            }
            guard !Task.isCancelled, exportID == token, model.archiveIdentity == identity, model.archiveRevision == revision else { return }
            let timeZone = TimeZone(identifier: timezone) ?? .current
            var calendar = Calendar(identifier: .gregorian); calendar.timeZone = timeZone
            let provenance = L10n.text("Daythread decision \(record.id) · version \(record.version)")
            let notes = [suggestion.notes, provenance].filter { !$0.isEmpty }.joined(separator: "\n\n")
            if suggestion.kind == .event {
                let event = EKEvent(eventStore: store)
                event.title = suggestion.title; event.notes = notes; event.location = suggestion.location
                event.isAllDay = suggestion.all_day; event.timeZone = timeZone
                // Only a missing end gets a temporary composer default. The link always
                // comes from the proposed start. Apple's editor owns all editing and Add.
                guard let dates = suggestion.calendarComposerDates(timeZone: timeZone) else { return }
                event.startDate = dates.start; event.endDate = dates.end
                composer = CalendarComposition(store: store, event: event, saveKey: key, identity: identity, revision: revision)
            } else {
                guard let list = store.defaultCalendarForNewReminders(), list.allowsContentModifications else {
                    throw BackendFailure(message: L10n.text("No writable default Reminders list is available. Set up a list in Reminders, then try again."))
                }
                let reminder = EKReminder(eventStore: store)
                reminder.calendar = list; reminder.title = suggestion.title
                reminder.notes = [notes, suggestion.location.isEmpty ? "" : L10n.text("Location: \(suggestion.location)")].filter { !$0.isEmpty }.joined(separator: "\n\n")
                if let due = DecisionFollowUp.date(suggestion.due_at, allDay: suggestion.all_day, timeZone: timeZone) {
                    var components = calendar.dateComponents(suggestion.all_day ? [.year, .month, .day] : [.year, .month, .day, .hour, .minute], from: due)
                    components.timeZone = timeZone; reminder.dueDateComponents = components
                    if !suggestion.all_day { reminder.addAlarm(EKAlarm(absoluteDate: due)) }
                }
                try store.save(reminder, commit: true)
                saved.insert(key)
            }
        } catch {
            if exportID == token, model.archiveIdentity == identity, model.archiveRevision == revision { self.error = L10n.error(error) }
        }
    }
}

private struct DecisionSuggestionRequest: Encodable { let expected_version: Int }

private extension DecisionFollowUp {
    func saveKey(_ record: DecisionRecord) -> String {
        let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys
        return record.id + ".\(record.version)." + (String(data: (try? encoder.encode(self)) ?? Data(), encoding: .utf8) ?? title)
    }
}

private struct CalendarComposition: Identifiable {
    let id = UUID()
    let store: EKEventStore
    let event: EKEvent
    let saveKey: String
    let identity: String
    let revision: String
}

private struct NativeEventComposer: UIViewControllerRepresentable {
    let composition: CalendarComposition
    let completed: (EKEventEditViewAction) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(completed: completed) }
    func makeUIViewController(context: Context) -> EKEventEditViewController {
        let controller = EKEventEditViewController()
        controller.eventStore = composition.store; controller.event = composition.event
        controller.editViewDelegate = context.coordinator
        return controller
    }
    func updateUIViewController(_ controller: EKEventEditViewController, context: Context) {}
    final class Coordinator: NSObject, EKEventEditViewDelegate {
        let completed: (EKEventEditViewAction) -> Void
        init(completed: @escaping (EKEventEditViewAction) -> Void) { self.completed = completed }
        func eventEditViewController(_ controller: EKEventEditViewController, didCompleteWith action: EKEventEditViewAction) { completed(action) }
    }
}

private struct NearbyComposition: Identifiable {
    let id = UUID()
    let suggestion: DecisionFollowUp
    let timezone: String
}

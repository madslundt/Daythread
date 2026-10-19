import SwiftUI
import EventKit
import EventKitUI
import OmiCore

struct InboxFollowUpLinks: View {
    let item: InboxItem
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @Environment(\.locale) private var interfaceLocale
    @State private var response: InboxFollowUps?
    @State private var loading = false
    @State private var error: String?
    @State private var requestID = UUID()
    @State private var store = EKEventStore()
    @State private var composer: CalendarComposition?
    @State private var reminder: ReminderComposition?
    @State private var undatedEvent: ReminderComposition?
    @State private var nearby: NearbyComposition?
    @State private var saved: Set<String> = []
    @State private var operation: Task<Void, Never>?
    private var context: String { model.archiveIdentity + model.archiveRevision + item.id + String(item.version) }

    var body: some View {
        let _ = interfaceLocale
        VStack(alignment: .leading, spacing: 14) {
            Text(L10n.text("Review each suggestion before saving it to Calendar or Reminders. Inbox keeps its own completion status."))
                .font(.caption).foregroundStyle(.secondary)
            if loading { ProgressView(L10n.text("Finding follow-ups")) }
            ErrorNote(message: error)
            if let response {
                if response.suggestions.isEmpty { Text(L10n.text("No calendar or reminder suggestions found.")).foregroundStyle(.secondary) }
                ForEach(Array(response.suggestions.enumerated()), id: \.offset) { _, suggestion in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(suggestion.title).font(.headline)
                        if !suggestion.reason.isEmpty { Text(suggestion.reason).font(.caption).foregroundStyle(.secondary) }
                        if let date = suggestion.actionDate(timeZone: TimeZone(identifier: response.timezone) ?? .current) {
                            Text(followUpDateLabel(date, allDay: suggestion.all_day, timezone: response.timezone))
                                .font(.caption).foregroundStyle(.secondary)
                        } else if !suggestion.hasLocationTrigger { Text(L10n.text("Deadline unknown")).font(.caption).foregroundStyle(.secondary) }
                        if saved.contains(saveKey(suggestion)) {
                            Label(suggestion.kind == .event ? L10n.text("Saved to Calendar") : L10n.text("Saved to Reminders"), systemImage: "checkmark.circle")
                                .font(.subheadline).foregroundStyle(.secondary)
                        } else {
                            Button {
                                operation = Task { await review(suggestion, timezone: response.timezone) }
                            } label: {
                                Label(suggestion.kind == .event ? L10n.text("Review calendar event") : L10n.text("Review reminder"), systemImage: suggestion.kind == .event ? "calendar.badge.plus" : "list.bullet")
                            }.disabled(loading)
                        }
                    }.padding(.vertical, 5)
                }
            }
            if response == nil || error != nil {
                Button(L10n.text("Find calendar & reminder options")) { operation = Task { await load() } }.disabled(loading)
            }
        }
        .sheet(item: $composer) { composition in
            NativeEventComposer(composition: composition) { action in
                if action == .saved, composition.identity == model.archiveIdentity, composition.revision == model.archiveRevision { saved.insert(composition.saveKey) }
                composer = nil
            }
        }
        .sheet(item: $reminder) { selection in
            InboxReminderReview(item: item, suggestion: selection.suggestion, timezone: selection.timezone) { saved.insert(saveKey(selection.suggestion)) }
        }
        .sheet(item: $nearby) { selection in
            LocationReminderView(item: item, suggestion: selection.suggestion, timezone: selection.timezone) { saved.insert(saveKey(selection.suggestion)) }
        }
        .sheet(item: $undatedEvent) { selection in
            InboxEventDateReview(item: item, suggestion: selection.suggestion, timezone: selection.timezone) { saved.insert(saveKey(selection.suggestion)) }
        }
        .onChange(of: context) { _, _ in invalidate() }
        .onChange(of: phase) { _, value in if value == .background { invalidate() } }
        .onDisappear { invalidate() }
    }

    private func saveKey(_ suggestion: DecisionFollowUp) -> String {
        let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys
        return item.id + ".\(item.version)." + (String(data: (try? encoder.encode(suggestion)) ?? Data(), encoding: .utf8) ?? suggestion.title)
    }
    private func invalidate() {
        operation?.cancel(); operation = nil; requestID = UUID(); loading = false
        response = nil; composer = nil; reminder = nil; nearby = nil; undatedEvent = nil; saved = []
    }
    private func load() async {
        let key = context, token = UUID(); requestID = token; loading = true; error = nil; response = nil
        defer { if token == requestID { loading = false } }
        do {
            let result: InboxFollowUps = try await model.client().decisionRequest("v1/inbox/\(item.id)/suggestions", method: "POST", body: FollowUpSuggestionRequest(expected_version: item.version))
            guard !Task.isCancelled, key == context, requestID == token else { return }
            guard result.item_id == item.id, result.item_version == item.version, TimeZone(identifier: result.timezone) != nil else {
                throw BackendFailure(message: L10n.text("This action point changed or its evidence is no longer current. Refresh and review it again."))
            }
            response = result
        } catch { if !Task.isCancelled, key == context, requestID == token { self.error = L10n.error(error) } }
    }
    private func review(_ suggestion: DecisionFollowUp, timezone: String) async {
        let key = context, token = UUID(); requestID = token; loading = true; error = nil
        defer { if requestID == token { loading = false } }
        do {
            try await FollowUpOrigin.inbox(item).validate(model.client())
            guard !Task.isCancelled, context == key, token == requestID else { return }
            if suggestion.hasLocationTrigger {
                nearby = NearbyComposition(suggestion: suggestion, timezone: timezone)
            } else if suggestion.kind == .reminder {
                reminder = ReminderComposition(suggestion: suggestion, timezone: timezone)
            } else {
                guard let dates = suggestion.calendarComposerDates(timeZone: TimeZone(identifier: timezone) ?? .current) else {
                    undatedEvent = ReminderComposition(suggestion: suggestion, timezone: timezone)
                    return
                }
                let event = EKEvent(eventStore: store)
                event.title = suggestion.title; event.notes = [suggestion.notes, FollowUpOrigin.inbox(item).provenance].filter { !$0.isEmpty }.joined(separator: "\n\n")
                event.location = suggestion.location; event.isAllDay = suggestion.all_day
                event.timeZone = TimeZone(identifier: timezone); event.startDate = dates.start; event.endDate = dates.end
                composer = CalendarComposition(store: store, event: event, saveKey: saveKey(suggestion), identity: model.archiveIdentity, revision: model.archiveRevision)
            }
        } catch { if !Task.isCancelled, key == context, requestID == token { self.error = L10n.error(error) } }
    }
}

private struct ReminderComposition: Identifiable {
    let id = UUID()
    let suggestion: DecisionFollowUp
    let timezone: String
}

private struct InboxReminderReview: View {
    let item: InboxItem
    let suggestion: DecisionFollowUp
    let timezone: String
    let onSaved: () -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.locale) private var interfaceLocale
    @State private var title: String
    @State private var notes: String
    @State private var busy = false
    @State private var error: String?
    @State private var store = EKEventStore()
    @State private var active = true

    init(item: InboxItem, suggestion: DecisionFollowUp, timezone: String, onSaved: @escaping () -> Void) {
        self.item = item; self.suggestion = suggestion; self.timezone = timezone; self.onSaved = onSaved
        _title = State(initialValue: suggestion.title); _notes = State(initialValue: suggestion.notes)
    }

    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            Form {
                Section(L10n.text("Review reminder")) {
                    TextField(L10n.text("Title"), text: $title, axis: .vertical)
                    TextField(L10n.text("Notes"), text: $notes, axis: .vertical)
                    if let date = suggestion.actionDate(timeZone: TimeZone(identifier: timezone) ?? .current) {
                        Text(followUpDateLabel(date, allDay: suggestion.all_day, timezone: timezone))
                    } else { Text(L10n.text("Deadline unknown")) }
                    if !suggestion.location.isEmpty { Text(suggestion.location) }
                    Text(L10n.text("Saving this reminder does not mark the action complete in Inbox.")).font(.caption).foregroundStyle(.secondary)
                    ErrorNote(message: error)
                }
            }.disabled(busy).navigationTitle(L10n.text("Review reminder")).navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() }.disabled(busy) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(L10n.text("Save to Reminders")) { Task { await save() } }.disabled(busy || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
        }.interactiveDismissDisabled(busy).onDisappear { active = false }
    }

    private func save() async {
        let identity = model.archiveIdentity, revision = model.archiveRevision
        busy = true; error = nil
        defer { busy = false }
        do {
            let client = try model.client()
            try await FollowUpOrigin.inbox(item).validate(client)
            let status = EKEventStore.authorizationStatus(for: .reminder)
            if status == .notDetermined {
                guard try await store.requestFullAccessToReminders() else {
                    throw BackendFailure(message: L10n.text("Reminders access was declined. Enable access for Daythread in Settings to save a reminder."))
                }
            } else if status != .fullAccess {
                throw BackendFailure(message: L10n.text("Reminders access is unavailable. Check Daythread's Reminders access in Settings."))
            }
            try await FollowUpOrigin.inbox(item).validate(client)
            guard active, !Task.isCancelled, identity == model.archiveIdentity, revision == model.archiveRevision else { return }
            guard let list = store.defaultCalendarForNewReminders(), list.allowsContentModifications else {
                throw BackendFailure(message: L10n.text("No writable default Reminders list is available. Set up a list in Reminders, then try again."))
            }
            let reminder = EKReminder(eventStore: store)
            reminder.calendar = list; reminder.title = title.trimmingCharacters(in: .whitespacesAndNewlines)
            reminder.notes = [notes, suggestion.location, FollowUpOrigin.inbox(item).provenance].filter { !$0.isEmpty }.joined(separator: "\n\n")
            if let due = suggestion.actionDate(timeZone: TimeZone(identifier: timezone) ?? .current) {
                var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(identifier: timezone) ?? .current
                var parts = calendar.dateComponents(suggestion.all_day ? [.year, .month, .day] : [.year, .month, .day, .hour, .minute], from: due)
                parts.timeZone = calendar.timeZone; reminder.dueDateComponents = parts
                if !suggestion.all_day { reminder.addAlarm(EKAlarm(absoluteDate: due)) }
            }
            try store.save(reminder, commit: true)
            onSaved(); dismiss()
        } catch { if active, identity == model.archiveIdentity, revision == model.archiveRevision { self.error = L10n.error(error) } }
    }
}

private func followUpDateLabel(_ date: Date, allDay: Bool, timezone: String) -> String {
    let formatter = DateFormatter(); formatter.locale = L10n.locale
    formatter.timeZone = TimeZone(identifier: timezone) ?? .current
    formatter.setLocalizedDateFormatFromTemplate(allDay ? "dMMMy" : "dMMMyjmm")
    return formatter.string(from: date) + " · " + timezone
}

/// An unknown event date is chosen explicitly before Apple's final calendar editor.
private struct InboxEventDateReview: View {
    let item: InboxItem
    let suggestion: DecisionFollowUp
    let timezone: String
    let onSaved: () -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.locale) private var interfaceLocale
    @State private var start = Date()
    @State private var end = Date().addingTimeInterval(3600)
    @State private var store = EKEventStore()
    @State private var composer: CalendarComposition?
    @State private var busy = false
    @State private var error: String?
    @State private var active = true
    private var calendar: Calendar {
        var value = Calendar(identifier: .gregorian); value.timeZone = TimeZone(identifier: timezone) ?? .current
        return value
    }

    var body: some View {
        let _ = interfaceLocale
        NavigationStack {
            Form {
                Section {
                    Text(suggestion.title).font(.headline)
                    Text(L10n.text("No event date was recorded. Choose a date before continuing.")).font(.subheadline).foregroundStyle(.secondary)
                    DatePicker(L10n.text("Start date"), selection: $start, displayedComponents: suggestion.all_day ? [.date] : [.date, .hourAndMinute])
                    DatePicker(L10n.text("End date"), selection: $end, displayedComponents: suggestion.all_day ? [.date] : [.date, .hourAndMinute])
                    Text(timezone).font(.caption).foregroundStyle(.secondary)
                    ErrorNote(message: error)
                    Button(L10n.text("Review calendar event")) { Task { await review() } }.disabled(busy || end <= start)
                }
            }.environment(\.calendar, calendar).environment(\.timeZone, calendar.timeZone)
                .navigationTitle(L10n.text("Review calendar event")).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button(L10n.text("Cancel")) { dismiss() }.disabled(busy) } }
        }
        .onAppear {
            if suggestion.all_day { start = calendar.startOfDay(for: start); end = calendar.date(byAdding: .day, value: 1, to: start)! }
        }
        .onChange(of: start) { _, value in
            if end <= value { end = suggestion.all_day ? calendar.date(byAdding: .day, value: 1, to: value)! : value.addingTimeInterval(3600) }
        }
        .sheet(item: $composer) { composition in
            NativeEventComposer(composition: composition) { action in
                composer = nil
                if action == .saved, composition.identity == model.archiveIdentity, composition.revision == model.archiveRevision { onSaved(); dismiss() }
            }
        }
        .onDisappear { active = false }
    }

    private func review() async {
        let identity = model.archiveIdentity, revision = model.archiveRevision
        busy = true; error = nil
        defer { busy = false }
        do {
            try await FollowUpOrigin.inbox(item).validate(model.client())
            guard active, !Task.isCancelled, identity == model.archiveIdentity, revision == model.archiveRevision else { return }
            let event = EKEvent(eventStore: store)
            event.title = suggestion.title; event.notes = [suggestion.notes, FollowUpOrigin.inbox(item).provenance].filter { !$0.isEmpty }.joined(separator: "\n\n")
            event.location = suggestion.location; event.isAllDay = suggestion.all_day; event.timeZone = calendar.timeZone
            event.startDate = start; event.endDate = end
            composer = CalendarComposition(store: store, event: event, saveKey: "", identity: identity, revision: revision)
        } catch { if active, identity == model.archiveIdentity, revision == model.archiveRevision { self.error = L10n.error(error) } }
    }
}

import SwiftUI
import OmiCore

extension DayFilterScope {
    func validateSources(_ ids: Set<String>, day: String, fingerprint: String, client: BackendClient) async throws {
        guard isFiltered else { return }
        let current = try await client.request("v1/recaps/\(day)", query: query)
        let scope = current["scope"] == .null ? nil : ["tag_id": current["scope"]["tag_id"].text, "zone_id": current["scope"]["zone_id"].text]
        guard matchesSnapshot(scope, fingerprint: current["scope_fingerprint"].text, expectedFingerprint: fingerprint),
              ids.isSubset(of: Set(current["sources"].array.map { $0["id"].text })) else {
            throw BackendFailure(message: L10n.text("The recap does not match the selected filters. Refresh and try again."))
        }
    }
}

struct DayFilterSection: View {
    let scope: DayFilterScope
    let tags: [ManualTag]
    let zones: [LocationZone]
    let open: () -> Void
    let clear: () -> Void
    private var names: String {
        [scope.tagID.isEmpty ? nil : tags.first { $0.id == scope.tagID }?.name ?? L10n.text("Tag unavailable"),
         scope.zoneID.isEmpty ? nil : zones.first { $0.id == scope.zoneID }?.name ?? L10n.text("Zone unavailable")]
            .compactMap { $0 }.joined(separator: " · ")
    }
    var body: some View {
        Section {
            Button(action: open) {
                HStack {
                    Label(L10n.text("Filters"), systemImage: scope.isFiltered ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
                    Spacer()
                    Text(scope.isFiltered ? names : L10n.text("All recordings")).foregroundStyle(.secondary)
                }
            }.accessibilityIdentifier("day.filters")
            if scope.isFiltered {
                Button(L10n.text("Clear filters"), action: clear)
            }
        } footer: {
            if scope.isFiltered { Text(L10n.text("Only recordings matching every selected filter are included.")) }
        }
    }
}

struct DayRecordingFilters: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var tagID: String
    @Binding var zoneID: String
    let tags: [ManualTag]
    let zones: [LocationZone]
    let tagError: String?
    let zoneError: String?
    var onDirectoriesChanged: (() -> Void)? = nil

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker(L10n.text("Tag"), selection: $tagID) {
                        Text(L10n.text("All tags")).tag("")
                        if !tagID.isEmpty && !tags.contains(where: { $0.id == tagID }) {
                            Text(L10n.text("Tag unavailable")).tag(tagID)
                        }
                        ForEach(tags) { tag in Text(tag.name).tag(tag.id) }
                    }.accessibilityIdentifier("day.tagFilter")
                    if let tagError { Text(tagError).font(.caption).foregroundStyle(.secondary) }
                    Picker(L10n.text("Location zone"), selection: $zoneID) {
                        Text(L10n.text("All locations")).tag("")
                        if !zoneID.isEmpty && !zones.contains(where: { $0.id == zoneID }) {
                            Text(L10n.text("Zone unavailable")).tag(zoneID)
                        }
                        ForEach(zones) { zone in Text(zone.name).tag(zone.id) }
                    }.accessibilityIdentifier("day.zoneFilter")
                    if let zoneError { Text(zoneError).font(.caption).foregroundStyle(.secondary) }
                    NavigationLink(L10n.text("Manage zones")) { LocationZonesView().onDisappear { onDirectoriesChanged?() } }
                } footer: {
                    Text(L10n.text("Filters apply to the recap, highlights, topics, and timeline."))
                }
                if !tagID.isEmpty || !zoneID.isEmpty {
                    Button(L10n.text("Clear filters")) { tagID = ""; zoneID = "" }
                        .accessibilityIdentifier("day.clearFilters")
                }
            }
            .navigationTitle(L10n.text("Filters")).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L10n.text("Done")) { dismiss() } } }
        }
    }
}

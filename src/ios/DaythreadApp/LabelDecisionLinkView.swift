import SwiftUI

struct LabelDecisionLinkView: View {
    let entityID: String
    let revision: String
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var page = 0
    @State private var response: DecisionList?
    @State private var writer = LabelWriter()
    @State private var error: String?
    @State private var id = UUID().uuidString.lowercased()
    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        List {
            Text(L10n.text("Associate a register record without changing its confirmation or decision state. Personal decision notes remain separate annotations.")).font(.caption)
            ForEach(response?.items ?? []) { decision in
                VStack(alignment: .leading, spacing: 10) {
                    DecisionRow(record: decision)
                    NavigationLink(L10n.text("Inspect exact evidence and register status")) { DecisionDetail(id: decision.id) }
                    Button(L10n.text("Associate this register record")) { Task { await save(decision) } }.disabled(writer.busy)
                }
            }
            HStack {
                Button(L10n.text("Previous decisions")) { page -= 1 }.disabled(page == 0)
                Button(L10n.text("Next decisions")) { page += 1 }.disabled(response == nil || (page + 1) * 30 >= (response?.total ?? 0))
            }
            ErrorNote(message: error ?? writer.error)
        }.navigationTitle(L10n.text("Link decision")).searchable(text: $query)
            .onChange(of: query) { _, _ in page = 0 }
            .task(id: model.archiveIdentity + query + String(page)) {
                response = nil
                do {
                    let result: DecisionList = try await model.client().decisionRequest("v1/decisions", query: ["q": query, "limit": "30", "offset": String(page * 30)])
                    guard !Task.isCancelled else { return }
                    response = result; error = nil
                } catch { if !Task.isCancelled { self.error = L10n.error(error) } }
            }
    }
    private func save(_ decision: DecisionRecord) async {
        var command = LabelCommand(action: "decision_link.save", id: id, expected_revision: revision)
        command.entity_id = entityID; command.decision_id = decision.id; command.decision_version = decision.version
        do { if await writer.save(command, client: try model.client()) { dismiss(); await model.refreshStatus() } }
        catch { self.error = L10n.error(error) }
    }
}

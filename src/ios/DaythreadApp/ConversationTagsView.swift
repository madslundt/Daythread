import SwiftUI

struct ManualTag: Decodable, Identifiable {
    let id: String
    let name: String
    let source_count: Int?
}
struct TagDirectory: Decodable { let revision: String; let items: [ManualTag] }
private struct TagSelection: Decodable {
    let revision: String
    let membership: String
    let source_count: Int
    let tags: [ManualTag]
}
private struct TagChange: Encodable {
    let expected_revision: String
    let expected_membership: String
    let action: String
    let tag_id: String?
    let name: String?
}

struct ConversationTagsView: View {
    var sourceID: String? = nil
    var groupID: String? = nil
    var day: String? = nil
    var onChanged: (() -> Void)? = nil
    @Environment(AppModel.self) private var model
    @State private var selection: TagSelection?
    @State private var tags: [ManualTag] = []
    @State private var selected = ""
    @State private var name = ""
    @State private var busy = false
    @State private var error: String?
    @State private var active = true
    @State private var requestID = UUID()
    private var path: String {
        groupID.map { "v1/recording-groups/\($0)/tags" } ?? "v1/sources/\(sourceID ?? "")/tags"
    }
    private var query: [String: String] { groupID != nil ? ["day": day ?? ""] : [:] }

    var body: some View {
        Form {
            Section {
                Text(L10n.text("Tags are your own categories. Adding or removing a tag keeps the recorded location and transcript intact."))
                    .font(.caption).foregroundStyle(.secondary)
                if groupID != nil {
                    Text(L10n.text("Changes apply to all currently saved recordings in this conversation, including those hidden by filters."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let selection { Text(L10n.text("\(selection.source_count) recordings selected")) }
            }
            if let selection {
                Section(L10n.text("Conversation tags")) {
                    if selection.tags.isEmpty { Text(L10n.text("No tags yet")).foregroundStyle(.secondary) }
                    ForEach(selection.tags) { tag in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(tag.name).font(.headline)
                            if (tag.source_count ?? 0) < selection.source_count {
                                Text(L10n.text("\(tag.source_count ?? 0) of \(selection.source_count) recordings"))
                                    .font(.caption).foregroundStyle(.secondary)
                                Button(L10n.text("Apply to all")) { Task { await change("add", tagID: tag.id) } }
                            }
                            Button(L10n.text("Remove tag"), role: .destructive) { Task { await change("remove", tagID: tag.id) } }
                        }
                    }
                }
                Section {
                    Picker(L10n.text("Existing tag"), selection: $selected) {
                        Text(L10n.text("Choose a tag")).tag("")
                        ForEach(tags) { tag in Text(tag.name).tag(tag.id) }
                    }
                    Button(L10n.text("Add tag")) { Task { await change("add", tagID: selected) } }.disabled(selected.isEmpty)
                    TextField(L10n.text("New tag name"), text: $name)
                    Button(L10n.text("Create and add tag")) { Task { await change("add", name: name) } }
                        .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || name.count > 80)
                }
            }
            if busy { ProgressView() }
            if let error { Text(error).foregroundStyle(.secondary) }
            Button(L10n.text("Reload tags")) { Task { await load() } }
        }
        .disabled(busy)
        .navigationTitle(L10n.text("Conversation tags")).navigationBarTitleDisplayMode(.inline)
        .task(id: model.archiveIdentity + path) { active = true; selection = nil; await load() }
        .onDisappear { active = false; requestID = UUID() }
    }

    private func load() async {
        let token = UUID(); requestID = token; busy = true; error = nil
        let identity = model.archiveIdentity
        defer { if requestID == token { busy = false } }
        do {
            let client = try model.client()
            async let response: TagSelection = client.decisionRequest(path, query: query)
            async let directory: TagDirectory = client.decisionRequest("v1/tags")
            let (value, available) = try await (response, directory)
            guard active, !Task.isCancelled, requestID == token, identity == model.archiveIdentity else { return }
            selection = value; tags = available.items
        } catch { if active, requestID == token, identity == model.archiveIdentity { self.error = L10n.error(error) } }
    }

    private func change(_ action: String, tagID: String? = nil, name: String? = nil) async {
        guard let selection, !busy else { return }
        busy = true; error = nil
        let identity = model.archiveIdentity, token = UUID(); requestID = token
        defer { if requestID == token { busy = false } }
        do {
            let client = try model.client()
            let result: TagSelection = try await client.decisionRequest(path, method: "PUT", query: query,
                body: TagChange(expected_revision: selection.revision, expected_membership: selection.membership,
                                action: action, tag_id: tagID, name: name))
            guard active, !Task.isCancelled, requestID == token, identity == model.archiveIdentity else { return }
            self.selection = result; self.name = ""; selected = ""
            onChanged?()
            let available: TagDirectory = try await client.decisionRequest("v1/tags")
            guard active, !Task.isCancelled, requestID == token, identity == model.archiveIdentity else { return }
            tags = available.items
        } catch { if active, requestID == token, identity == model.archiveIdentity { self.error = L10n.error(error) } }
    }
}

import SwiftUI

struct ArchiveMarkdownExport: Identifiable {
    let id = UUID()
    let url: URL

    static func prepare(client: BackendClient, path: String, query: [String: String] = [:], name: String) async throws -> Self {
        let data = try await client.data(path: path, query: query, profileHeader: client.profileID)
        try Task.checkCancellation()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent(name).appendingPathExtension("md")
        try data.write(to: url, options: [.atomic, .completeFileProtection])
        return Self(url: url)
    }
    func remove() { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
}

struct ArchiveMarkdownShareSheet: UIViewControllerRepresentable {
    let export: ArchiveMarkdownExport
    func makeUIViewController(context: Context) -> UIActivityViewController {
        let controller = UIActivityViewController(activityItems: [export.url], applicationActivities: nil)
        controller.completionWithItemsHandler = { _, _, _, _ in export.remove() }
        return controller
    }
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

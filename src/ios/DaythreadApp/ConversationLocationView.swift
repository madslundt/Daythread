import SwiftUI

struct ConversationLocationView: View {
    let location: JSONValue

    @Environment(\.locale) private var interfaceLocale
    var body: some View {
        let _ = interfaceLocale
        if case .number(let latitude) = location["latitude"],
           case .number(let longitude) = location["longitude"],
           case .number(let accuracy) = location["horizontal_accuracy"] {
            Section(L10n.text("Conversation location")) {
                Text(String(format: L10n.text("%.5f, %.5f · ±%.0f m"), latitude, longitude, accuracy))
                    .font(.callout).textSelection(.enabled)
                Text(location["captured_at"].text).font(.caption).foregroundStyle(.secondary)
                if let url = URL(string: "https://maps.apple.com/?ll=\(latitude),\(longitude)") {
                    Link(L10n.text("Open in Maps"), destination: url)
                }
                NavigationLink(L10n.text("Save as zone location")) {
                    LocationZonesView(seed: ZonePoint(label: L10n.text("Recording location"), latitude: latitude, longitude: longitude))
                }
                Text(L10n.text("Phone location near the start of this recording. It does not identify places mentioned in the conversation."))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

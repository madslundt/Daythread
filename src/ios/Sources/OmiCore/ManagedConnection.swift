import Foundation

/// The managed origin is supplied by the application build, never by an account or URL editor.
public struct ManagedServiceOrigin: Equatable, Sendable {
    public let url: URL

    public init?(_ address: String) {
        guard let value = URLComponents(string: address), value.scheme?.lowercased() == "https",
              let host = value.host, !host.isEmpty, value.user == nil, value.password == nil,
              value.query == nil, value.fragment == nil, ["", "/"].contains(value.path),
              let url = value.url else { return nil }
        self.url = url
    }

    public func contains(_ candidate: URL) -> Bool {
        candidate.scheme?.lowercased() == "https" && candidate.host?.lowercased() == url.host?.lowercased()
            && (candidate.port ?? 443) == (url.port ?? 443) && candidate.user == nil && candidate.password == nil
    }

    public func destination(accountID: String) -> String {
        "managed:" + url.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + ":" + accountID
    }

    public static func ownDestination(archiveIdentity: String) -> String { "own:" + archiveIdentity }
}

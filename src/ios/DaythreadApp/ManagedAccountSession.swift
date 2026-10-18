import Foundation
import Observation
import Security
import OmiCore

extension Notification.Name { static let managedSessionExpired = Notification.Name("daythread.managedSessionExpired") }

struct ManagedAccount: Codable {
    struct Credits: Codable { let available_units: Int64; let reserved_units: Int64 }
    let id: String
    let email: String
    let name: String
    let hosted_until: TimeInterval
    let hosting_active: Bool
    let archive_access: Bool?
    var canAccessArchive: Bool { archive_access ?? (hosted_until > 0) }
    let credits: Credits
}

struct ManagedCredential: Codable {
    let token: String
    let accountID: String
    let sessionID: UUID
    let origin: String
    var account: ManagedAccount?
}

enum ManagedKeychain {
    private static var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "com.daythread.native-session", kSecAttrAccount as String: "session-v1"] }
    static func read() throws -> ManagedCredential? {
        var query = query; query[kSecReturnData as String] = true; query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else {
            throw BackendFailure(message: L10n.source("Unlock your iPhone to access your Daythread session."))
        }
        return try JSONDecoder().decode(ManagedCredential.self, from: data)
    }
    static func save(_ credential: ManagedCredential) throws {
        let values: [String: Any] = [kSecValueData as String: try JSONEncoder().encode(credential),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        var status = SecItemUpdate(query as CFDictionary, values as CFDictionary)
        if status == errSecItemNotFound { status = SecItemAdd(query.merging(values) { _, new in new } as CFDictionary, nil) }
        guard status == errSecSuccess else { throw BackendFailure(message: L10n.source("Your session could not be saved securely. Please try again.")) }
    }
    static func remove() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw BackendFailure(message: L10n.source("Your session could not be removed. Unlock your iPhone and try again."))
        }
    }
}

/// Refuse every redirect: a 307/308 could otherwise resend a password, prompt or audio body.
final class ManagedRedirectPolicy: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    static let shared = ManagedRedirectPolicy()
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

struct ManagedAuthorization {
    let origin: ManagedServiceOrigin
    let accountID: String
    let sessionID: UUID
    var destinationIdentity: String { origin.destination(accountID: accountID) }
    func authorize(_ request: inout URLRequest) throws {
        guard let url = request.url, origin.contains(url) else {
            throw BackendFailure(message: L10n.source("The managed service address is fixed by this app."))
        }
        guard let credential = try ManagedKeychain.read(), credential.accountID == accountID,
              credential.sessionID == sessionID, credential.origin == origin.url.absoluteString else {
            throw BackendFailure(message: L10n.source("Sign in to your Daythread account to continue."), status: 401)
        }
        request.setValue("Bearer " + credential.token, forHTTPHeaderField: "Authorization")
    }
    func expireIfNeeded(_ response: URLResponse) {
        guard (response as? HTTPURLResponse)?.statusCode == 401 else { return }
        NotificationCenter.default.post(name: .managedSessionExpired, object: nil, userInfo: ["sessionID": sessionID])
    }
}

@MainActor @Observable
final class ManagedAccountSession {
    let origin: ManagedServiceOrigin?
    private(set) var account: ManagedAccount?
    private(set) var hostingVerified = false
    private(set) var accountID: String?
    private(set) var sessionID: UUID?
    var loading = false
    var error: String?
    var onChange: (() -> Void)?
    var signedIn: Bool { accountID != nil && sessionID != nil }
    var authorization: ManagedAuthorization? {
        guard let origin, let accountID, let sessionID else { return nil }
        return .init(origin: origin, accountID: accountID, sessionID: sessionID)
    }
    var accountURL: URL? { origin?.url.appendingPathComponent("account") }
    init() {
        origin = ManagedServiceOrigin(Bundle.main.object(forInfoDictionaryKey: "DaythreadManagedServiceURL") as? String ?? "")
        if let credential = try? ManagedKeychain.read(), credential.origin == origin?.url.absoluteString {
            accountID = credential.accountID; sessionID = credential.sessionID
            account = credential.account?.id == credential.accountID ? credential.account : nil
        }
    }
    func signIn(email: String, password: String) async throws {
        guard !loading else { return }
        loading = true; error = nil; defer { loading = false }
        struct Login: Decodable { let token: String; let account: ManagedAccount }
        let body = try JSONSerialization.data(withJSONObject: ["email": email.trimmingCharacters(in: .whitespacesAndNewlines), "password": password])
        let data = try await request("service/v1/auth/native/login", method: "POST", body: body)
        let result = try JSONDecoder().decode(Login.self, from: data)
        guard let origin, !result.token.isEmpty, !result.account.id.isEmpty else {
            throw BackendFailure(message: L10n.source("The sign-in response was invalid. Please try again."))
        }
        let credential = ManagedCredential(token: result.token, accountID: result.account.id, sessionID: UUID(), origin: origin.url.absoluteString, account: result.account)
        try ManagedKeychain.save(credential)
        accountID = credential.accountID; sessionID = credential.sessionID; account = result.account; hostingVerified = true
        onChange?()
    }
    func refresh() async {
        guard signedIn, !loading else { return }
        let expected = sessionID
        loading = true; error = nil; defer { loading = false }
        do {
            let data = try await request("service/v1/account", authenticated: true)
            let updated = try JSONDecoder().decode(ManagedAccount.self, from: data)
            guard expected == sessionID, updated.id == accountID else { return }
            if var credential = try ManagedKeychain.read(), credential.sessionID == sessionID {
                credential.account = updated; try ManagedKeychain.save(credential)
            }
            account = updated; hostingVerified = true; onChange?()
        } catch {
            if (error as? BackendFailure)?.status == 401 { expire(expected: expected) }
            else if !Task.isCancelled { self.error = L10n.error(error) }
        }
    }
    func signOut() async throws {
        try ManagedKeychain.remove()
        account = nil; accountID = nil; sessionID = nil; hostingVerified = false; error = nil; onChange?()
    }
    func revokeAndSignOut() async throws {
        var revocation: URLRequest?
        if let origin, let authorization {
            var request = URLRequest(url: origin.url.appendingPathComponent("service/v1/auth/native/session"))
            request.httpMethod = "DELETE"; request.timeoutInterval = 30
            try authorization.authorize(&request); revocation = request
        }
        try await signOut()
        if let revocation {
            Task {
                do { _ = try await send(revocation) }
                catch { self.error = L10n.source("Signed out on this iPhone. The remote session could not be revoked.") }
            }
        }
    }
    func expire(expected: UUID?) {
        guard expected == sessionID, sessionID != nil else { return }
        do { try ManagedKeychain.remove() }
        catch { self.error = L10n.error(error) }
        account = nil; accountID = nil; sessionID = nil; hostingVerified = false
        error = L10n.source("Your session has expired. Sign in again to continue."); onChange?()
    }
    private func request(_ path: String, method: String = "GET", body: Data? = nil, authenticated: Bool = false) async throws -> Data {
        guard let origin else { throw BackendFailure(message: L10n.source("Managed sign-in is not configured in this app build.")) }
        var request = URLRequest(url: origin.url.appendingPathComponent(path))
        request.httpMethod = method; request.httpBody = body; request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if authenticated {
            guard let authorization else { throw BackendFailure(message: L10n.source("Sign in to your Daythread account to continue."), status: 401) }
            try authorization.authorize(&request)
        }
        return try await send(request)
    }
    private func send(_ request: URLRequest) async throws -> Data {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil; configuration.urlCache = nil
        let session = URLSession(configuration: configuration, delegate: ManagedRedirectPolicy.shared, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONDecoder().decode(JSONValue.self, from: data))?["detail"].text
            throw BackendFailure(message: detail?.isEmpty == false ? detail! : L10n.source("The account service could not complete the request."), status: (response as? HTTPURLResponse)?.statusCode)
        }
        return data
    }
}

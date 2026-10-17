import Foundation

enum JSONValue: Codable, Equatable {
    case string(String), number(Double), bool(Bool), object([String: JSONValue]), array([JSONValue]), null
    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let object = try? value.decode([String: JSONValue].self) { self = .object(object) }
        else if let array = try? value.decode([JSONValue].self) { self = .array(array) }
        else if let text = try? value.decode(String.self) { self = .string(text) }
        else if let bool = try? value.decode(Bool.self) { self = .bool(bool) }
        else { self = .number(try value.decode(Double.self)) }
    }
    func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .string(let text): try value.encode(text)
        case .number(let number): try value.encode(number)
        case .bool(let bool): try value.encode(bool)
        case .object(let object): try value.encode(object)
        case .array(let array): try value.encode(array)
        case .null: try value.encodeNil()
        }
    }
    subscript(_ key: String) -> JSONValue { if case .object(let object) = self { object[key] ?? .null } else { .null } }
    var text: String { if case .string(let text) = self { text } else { "" } }
    var array: [JSONValue] { if case .array(let value) = self { value } else { [] } }
    var integer: Int { if case .number(let value) = self { Int(value) } else { 0 } }
}

struct BackendFailure: LocalizedError {
    let message: String
    var status: Int? = nil
    var errorDescription: String? { message }
}

struct BackendClient {
    let base: URL
    let profileID: String
    init(address: String, profileID: String = "default") throws {
        self.profileID = profileID
        guard let url = URL(string: address.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(url.scheme?.lowercased()), url.host != nil,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else {
            throw BackendFailure(message: "Enter an http:// or https:// home-core address without credentials or a query.")
        }
        base = url
    }
    func url(_ path: String, query: [String: String] = [:]) -> URL {
        let endpoint = base.appendingPathComponent(path)
        var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false)!
        if !query.isEmpty { components.queryItems = query.keys.sorted().map { URLQueryItem(name: $0, value: query[$0]) } }
        return components.url!
    }
    func request(_ path: String, method: String = "GET", query: [String: String] = [:], body: [String: Any]? = nil) async throws -> JSONValue {
        let global = ["v1/settings", "v1/configuration", "v1/profiles"].contains { path == $0 || path.hasPrefix($0 + "/") }
        if !global { try requireProfile() }
        var request = URLRequest(url: url(path, query: query)); request.httpMethod = method
        if !global { request.setValue(profileID, forHTTPHeaderField: "X-Omi-Profile") }
        // A day recap makes multiple sequential model calls, each with its own server
        // timeout. Keep ordinary controls responsive while allowing that reduction to finish.
        let inference = method == "POST" && (path == "v1/chat" || path == "v1/rewind" || path.hasPrefix("v1/recaps/") || path.hasPrefix("v1/meetings/") || path.hasPrefix("v1/speakers/") || (path.hasPrefix("v1/transcripts/") && path.hasSuffix("/translation")))
        request.timeoutInterval = inference ? 3600 : 180
        if let body { request.httpBody = try JSONSerialization.data(withJSONObject: body); request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, response) = try await URLSession.shared.data(for: request)
        try Self.validate(response, data: data)
        return try JSONDecoder().decode(JSONValue.self, from: data)
    }
    func decoded<T: Decodable>(_ path: String, method: String = "GET", query: [String: String] = [:], body: [String: Any]? = nil, as type: T.Type) async throws -> T {
        let value = try await request(path, method: method, query: query, body: body)
        return try JSONDecoder().decode(type, from: JSONEncoder().encode(value))
    }
    private func requireProfile() throws {
        guard !profileID.isEmpty else { throw BackendFailure(message: "Choose a profile in Settings first.") }
    }
    @MainActor
    func chat(body: [String: Any], onEvent: @MainActor (JSONValue) -> Void) async throws -> JSONValue {
        try requireProfile()
        var request = URLRequest(url: url("v1/chat/stream"), cachePolicy: .reloadIgnoringLocalAndRemoteCacheData)
        request.setValue(profileID, forHTTPHeaderField: "X-Omi-Profile")
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/x-ndjson", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForResource = 3600
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        return try await withTaskCancellationHandler {
            let (bytes, response) = try await session.bytes(for: request)
            try Self.validate(response, data: Data())
            var size = 0, textSize = 0
            for try await line in bytes.lines {
                try Task.checkCancellation()
                size += line.utf8.count
                guard size <= 16_000_000 else { throw BackendFailure(message: "The answer exceeded its response limit.") }
                if line.isEmpty { continue }
                let event = try JSONDecoder().decode(JSONValue.self, from: Data(line.utf8))
                switch event["type"].text {
                case "done":
                    let result = event["result"]
                    guard !result["session_id"].text.isEmpty, !result["message_id"].text.isEmpty,
                          !result["answer"].text.isEmpty else { throw BackendFailure(message: "The answer response was invalid.") }
                    return result
                case "error":
                    throw BackendFailure(message: event["message"].text.isEmpty ? "The answer could not be completed." : event["message"].text, status: event["status"].integer)
                case "delta":
                    textSize += event["text"].text.count
                    guard textSize <= 30_000 else { throw BackendFailure(message: "The answer exceeded its text limit.") }
                    onEvent(event)
                case "status": onEvent(event)
                case "heartbeat": break
                default: throw BackendFailure(message: "The answer response was invalid.")
                }
            }
            throw BackendFailure(message: "The answer was interrupted. Please try again.")
        } onCancel: {
            session.invalidateAndCancel()
        }
    }
    func prep<Body: Encodable, Result: Decodable>(_ action: String, body: Body) async throws -> Result {
        try requireProfile()
        var request = URLRequest(url: url("v1/conversation-prep/" + action), cachePolicy: .reloadIgnoringLocalAndRemoteCacheData)
        request.setValue(profileID, forHTTPHeaderField: "X-Omi-Profile")
        request.httpMethod = "POST"
        request.timeoutInterval = action == "generate" ? 3600 : 180
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        request.httpBody = try JSONEncoder().encode(body)
        // An ephemeral session prevents persistent URL caches from retaining prep evidence.
        let session = URLSession(configuration: .ephemeral)
        defer { session.finishTasksAndInvalidate() }
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try JSONDecoder().decode(Result.self, from: data)
    }
    func download(_ path: String, query: [String: String]) async throws -> URL {
        try requireProfile()
        var request = URLRequest(url: url(path, query: query), cachePolicy: .reloadIgnoringLocalAndRemoteCacheData)
        request.setValue(profileID, forHTTPHeaderField: "X-Omi-Profile")
        let (temporary, response) = try await URLSession.shared.download(for: request)
        try Self.validate(response, data: (try? Data(contentsOf: temporary)) ?? Data())
        let destination = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathExtension("wav")
        try FileManager.default.moveItem(at: temporary, to: destination)
        return destination
    }
    static func validate(_ response: URLResponse, data: Data) throws {
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            let detail = (try? JSONDecoder().decode(JSONValue.self, from: data))?["detail"].text
            throw BackendFailure(message: "Home-core HTTP \(status): \(detail?.isEmpty == false ? detail! : String(data: data.prefix(500), encoding: .utf8) ?? "No response detail")", status: status)
        }
    }
}

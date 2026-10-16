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
    var statusCode: Int? = nil
    var errorDescription: String? { message }
}

struct BackendClient {
    let base: URL
    init(address: String) throws {
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
        var request = URLRequest(url: url(path, query: query)); request.httpMethod = method
        // A day recap makes multiple sequential model calls, each with its own server
        // timeout. Keep ordinary controls responsive while allowing that reduction to finish.
        let inference = method == "POST" && (path == "v1/chat" || path == "v1/rewind" || path.hasPrefix("v1/recaps/") || path.hasPrefix("v1/meetings/"))
        request.timeoutInterval = inference ? 3600 : 180
        if let body { request.httpBody = try JSONSerialization.data(withJSONObject: body); request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, response) = try await URLSession.shared.data(for: request)
        try Self.validate(response, data: data)
        return try JSONDecoder().decode(JSONValue.self, from: data)
    }
    func download(_ path: String, query: [String: String]) async throws -> URL {
        let (temporary, response) = try await URLSession.shared.download(from: url(path, query: query))
        try Self.validate(response, data: (try? Data(contentsOf: temporary)) ?? Data())
        let destination = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathExtension("wav")
        try FileManager.default.moveItem(at: temporary, to: destination)
        return destination
    }
    static func validate(_ response: URLResponse, data: Data) throws {
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            let detail = (try? JSONDecoder().decode(JSONValue.self, from: data))?["detail"].text
            throw BackendFailure(message: "Home-core HTTP \(status): \(detail?.isEmpty == false ? detail! : String(data: data.prefix(500), encoding: .utf8) ?? "No response detail")", statusCode: status)
        }
    }
}

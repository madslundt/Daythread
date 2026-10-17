import Foundation

/// Interface copy is resolved separately from transcripts, model output and protocol values.
public enum AppLocalization {
    public static let preferenceKey = "interfaceLanguagePreference"
    public static let languages = ["en", "da", "de", "es", "fr", "nb", "sv"]

    public static var language: String {
        resolveLanguage(preference: UserDefaults.standard.string(forKey: preferenceKey) ?? "system",
                        preferredLanguages: Locale.preferredLanguages)
    }

    public static func resolveLanguage(preference: String, preferredLanguages: [String]) -> String {
        if languages.contains(preference) { return preference }
        return preferredLanguages.lazy.map {
            let code = $0.lowercased().components(separatedBy: CharacterSet(charactersIn: "-_"))[0]
            return code == "no" ? "nb" : code
        }
            .first(where: { languages.contains($0) }) ?? "en"
    }
    public static var locale: Locale { Locale(identifier: language) }

    public static func error(_ error: Error) -> String {
        if let network = error as? URLError {
            switch network.code {
            case .notConnectedToInternet: return AppLocalization.text("No network connection. Connect to Wi-Fi or cellular and try again.")
            case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
                return AppLocalization.text("The local backend could not be reached. Check its address and your connection.")
            case .timedOut: return AppLocalization.text("The request timed out. Please try again.")
            case .networkConnectionLost: return AppLocalization.text("The connection was interrupted. Please try again.")
            case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate,
                 .serverCertificateHasUnknownRoot, .serverCertificateNotYetValid:
                return AppLocalization.text("The secure connection could not be verified. Check your server certificate.")
            case .appTransportSecurityRequiresSecureConnection:
                return AppLocalization.text("This connection requires HTTPS. Check your backend connection settings.")
            case .badURL: return AppLocalization.text("The backend address is invalid. Check it in Settings.")
            case .cancelled: return AppLocalization.text("The request was cancelled.")
            default: break
            }
        }
        return key(error.localizedDescription)
    }

    public struct Message: ExpressibleByStringLiteral, ExpressibleByStringInterpolation {
        public let key: String
        public let values: [String: String]
        public init(stringLiteral value: String) { key = value; values = [:] }
        public init(stringInterpolation value: StringInterpolation) { key = value.key; values = value.values }
        public struct StringInterpolation: StringInterpolationProtocol {
            var key = ""
            var values: [String: String] = [:]
            public init(literalCapacity: Int, interpolationCount: Int) {}
            public mutating func appendLiteral(_ literal: String) { key += literal }
            public mutating func appendInterpolation<T>(_ value: T) {
                let name = "value\(values.count)"
                key += "{\(name)}"
                values[name] = String(describing: value)
            }
        }
        public var source: String { AppLocalization.interpolate(key, values: values) }
    }

    private struct Pattern {
        let regex: NSRegularExpression
        let fields: [String]
        let translated: String
        let specificity: Int
    }
    private struct Catalog {
        let messages: [String: String]
        let patterns: [Pattern]
        init(_ messages: [String: String]) {
            self.messages = messages
            let token = try! NSRegularExpression(pattern: #"\{([A-Za-z][A-Za-z0-9_]*)\}"#)
            patterns = messages.compactMap { template, translated in
                let names = token.matches(in: template, range: NSRange(template.startIndex..., in: template))
                guard !names.isEmpty else { return nil }
                var pattern = "", cursor = template.startIndex, fields: [String] = []
                var specificity = 0
                for name in names {
                    let bounds = Range(name.range, in: template)!
                    let literal = String(template[cursor..<bounds.lowerBound])
                    specificity += literal.count
                    pattern += NSRegularExpression.escapedPattern(for: literal) + "([\\s\\S]*?)"
                    fields.append(String(template[Range(name.range(at: 1), in: template)!]))
                    cursor = bounds.upperBound
                }
                let suffix = String(template[cursor...])
                specificity += suffix.count
                pattern += NSRegularExpression.escapedPattern(for: suffix)
                guard let regex = try? NSRegularExpression(pattern: "^" + pattern + "$") else { return nil }
                return Pattern(regex: regex, fields: fields, translated: translated, specificity: specificity)
            }.sorted { $0.specificity > $1.specificity }
        }
        func resolve(_ english: String, values: [String: String] = [:]) -> String {
            if let translated = messages[english] { return AppLocalization.interpolate(translated, values: values) }
            guard values.isEmpty, english.count <= 8192 else { return AppLocalization.interpolate(english, values: values) }
            for pattern in patterns {
                guard let match = pattern.regex.firstMatch(in: english, range: NSRange(english.startIndex..., in: english)) else { continue }
                var replacements: [String: String] = [:]
                for (index, field) in pattern.fields.enumerated() {
                    replacements[field] = String(english[Range(match.range(at: index + 1), in: english)!])
                }
                return AppLocalization.interpolate(pattern.translated, values: replacements)
            }
            return english
        }
    }
    private static let lock = NSLock()
    private static var cached: [String: Catalog] = [:]
    private static func catalog(_ language: String) -> Catalog {
        lock.lock(); defer { lock.unlock() }
        if let value = cached[language] { return value }
        let url = Bundle.main.url(forResource: "Localizable", withExtension: "strings", subdirectory: nil, localization: language)
        let messages = url.flatMap { try? Data(contentsOf: $0) }
            .flatMap { try? PropertyListSerialization.propertyList(from: $0, format: nil) as? [String: String] } ?? [:]
        let value = Catalog(messages)
        cached[language] = value
        return value
    }
    public static func text(_ message: Message) -> String {
        catalog(language).resolve(message.key, values: message.values)
    }
    /// Messages kept in model state or provenance retain English until the display boundary.
    public static func source(_ message: Message) -> String { message.source }
    public static func key(_ english: String) -> String { catalog(language).resolve(english) }
    public static func status(_ value: String) -> String {
        let identifier = "status." + value
        let translated = key(identifier)
        return translated == identifier ? value.replacingOccurrences(of: "_", with: " ") : translated
    }
    /// Pure lookup used by the app and regression tests, including server-authored captions.
    public static func resolve(_ english: String, values: [String: String] = [:], translations: [String: String]) -> String {
        Catalog(translations).resolve(english, values: values)
    }

    private static func interpolate(_ template: String, values: [String: String]) -> String {
        let regex = try! NSRegularExpression(pattern: #"\{([A-Za-z][A-Za-z0-9_]*)\}"#)
        var result = template
        for match in regex.matches(in: template, range: NSRange(template.startIndex..., in: template)).reversed() {
            let name = String(template[Range(match.range(at: 1), in: template)!])
            if let value = values[name], let range = Range(match.range, in: result) { result.replaceSubrange(range, with: value) }
        }
        return result
    }
}

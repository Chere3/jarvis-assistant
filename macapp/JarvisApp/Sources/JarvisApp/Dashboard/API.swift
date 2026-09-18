import Foundation

/// Cliente HTTP asíncrono del backend local para el panel nativo. Reutiliza el token y el puerto de `Backend`.
struct API {
    let backend: Backend

    enum APIError: Error, LocalizedError {
        case http(Int, String)
        case badURL
        var errorDescription: String? {
            switch self {
            case .http(let code, let msg): return "HTTP \(code): \(msg)"
            case .badURL: return "URL inválida"
            }
        }
    }

    private func request(_ path: String, method: String, body: Any?) async throws -> Data {
        backend.readTokenFile()
        guard let url = URL(string: backend.baseURL + path) else { throw APIError.badURL }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = 20
        req.setValue(backend.token, forHTTPHeaderField: "X-Jarvis-Token")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(backend.baseURL, forHTTPHeaderField: "Origin")
        if let body { req.httpBody = try JSONSerialization.data(withJSONObject: body) }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code >= 400 {
            let msg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String ?? String(decoding: data, as: UTF8.self)
            throw APIError.http(code, msg)
        }
        return data
    }

    func get(_ path: String, query: [String: String] = [:]) async throws -> JSON {
        var p = path
        if !query.isEmpty {
            var comps = URLComponents()
            comps.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
            p += "?" + (comps.percentEncodedQuery ?? "")
        }
        return JSON(try JSONSerialization.jsonObject(with: try await request(p, method: "GET", body: nil)))
    }

    func post(_ path: String, _ body: [String: Any] = [:]) async throws -> JSON {
        JSON(try JSONSerialization.jsonObject(with: try await request(path, method: "POST", body: body)))
    }
}

/// Acceso tolerante a JSON dinámico: `json["a"]["b"].string`, `json.array`, etc.
struct JSON {
    let raw: Any?
    init(_ raw: Any?) { self.raw = raw }
    subscript(key: String) -> JSON { JSON((raw as? [String: Any])?[key]) }
    subscript(index: Int) -> JSON {
        guard let a = raw as? [Any], index >= 0, index < a.count else { return JSON(nil) }
        return JSON(a[index])
    }
    var string: String? { raw as? String }
    var str: String { string ?? "" }
    var int: Int? { (raw as? Int) ?? (raw as? Double).map { Int($0) } }
    var double: Double? { (raw as? Double) ?? (raw as? Int).map { Double($0) } }
    var bool: Bool { (raw as? Bool) ?? false }
    var array: [JSON] { (raw as? [Any])?.map { JSON($0) } ?? [] }
    var dict: [String: Any] { raw as? [String: Any] ?? [:] }
    var isNull: Bool { raw == nil || raw is NSNull }
    var strings: [String] { (raw as? [String]) ?? [] }
}

func relativeAge(_ epoch: Double?) -> String {
    guard let epoch, epoch > 0 else { return "—" }
    let s = max(0, Date().timeIntervalSince1970 - epoch)
    if s < 60 { return "hace menos de 1 min" }
    if s < 3600 { return "hace \(Int(s / 60)) min" }
    if s < 86400 { return "hace \(Int(s / 3600)) h" }
    return "hace \(Int(s / 86400)) d"
}

func clockTime(_ epoch: Double?) -> String {
    guard let epoch, epoch > 0 else { return "—" }
    let f = DateFormatter()
    f.locale = Locale(identifier: "es_ES")
    f.dateFormat = "EEE HH:mm"
    return f.string(from: Date(timeIntervalSince1970: epoch))
}

func duration(_ from: Double?, _ to: Double?) -> String {
    guard let from, from > 0 else { return "—" }
    let end = (to ?? 0) > 0 ? to! : Date().timeIntervalSince1970
    let s = max(0, end - from)
    if s < 60 { return "\(Int(s)) s" }
    if s < 3600 { return "\(Int(s / 60)) min" }
    return String(format: "%.1f h", s / 3600)
}

func tokens(_ n: Int?) -> String {
    guard let n, n > 0 else { return "—" }
    if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
    if n >= 1000 { return String(format: "%.1fk", Double(n) / 1000) }
    return "\(n)"
}

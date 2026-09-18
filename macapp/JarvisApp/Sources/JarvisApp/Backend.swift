import Foundation

/// Lanza `jarvis serve --listen` (el backend Python) como proceso hijo de la app y habla con su API local.
/// Al ser hijo de Jarvis.app, macOS atribuye a la app el permiso de micrófono.
final class Backend {
    struct Launch: Codable { var python: String; var home: String; var port: Int }

    var process: Process?
    var token: String = ""
    var port: Int = 8765
    var home: String = ""
    private(set) var log: [String] = []
    var onLog: ((String) -> Void)?

    var attached = false  // true si se conectó a un backend que ya estaba en marcha (no lo lanzó la app)
    private var logFile: FileHandle?

    static func launchConfig() -> Launch? {
        // app.json vive SIEMPRE en ~/.jarvis (lo escribe `jarvis app install`); dentro indica el JARVIS_HOME real.
        let url = URL(fileURLWithPath: NSHomeDirectory() + "/.jarvis/app.json")
        guard let data = try? Data(contentsOf: url), let cfg = try? JSONDecoder().decode(Launch.self, from: data) else { return nil }
        return cfg
    }

    func start() -> Bool {
        guard let cfg = Backend.launchConfig() else {
            append("Falta ~/.jarvis/app.json: ejecuta `jarvis app install` desde el entorno del proyecto")
            return false
        }
        home = cfg.home; port = cfg.port
        readTokenFile()
        if portInUse() {
            attached = true
            append("conectado a un backend ya en marcha en el puerto \(port)")
            return true
        }
        let logsDir = home + "/data/runtime/logs"
        try? FileManager.default.createDirectory(atPath: logsDir, withIntermediateDirectories: true)
        let logPath = logsDir + "/app-backend.log"
        if !FileManager.default.fileExists(atPath: logPath) { FileManager.default.createFile(atPath: logPath, contents: nil) }
        logFile = FileHandle(forWritingAtPath: logPath)
        logFile?.seekToEndOfFile()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: cfg.python)
        p.arguments = ["-m", "jarvis.cli", "serve", "--listen", "--port", String(cfg.port),
                       "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        var env = ProcessInfo.processInfo.environment
        env["JARVIS_HOME"] = cfg.home
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            if d.isEmpty { return }
            if let s = String(data: d, encoding: .utf8) { self?.append(s.trimmingCharacters(in: .whitespacesAndNewlines)) }
        }
        p.terminationHandler = { [weak self] proc in self?.append("backend terminado (código \(proc.terminationStatus))") }
        do { try p.run() } catch { append("no se pudo lanzar el backend: \(error)"); return false }
        process = p
        return true
    }

    /// Comprueba si algo escucha ya en 127.0.0.1:port (conexión TCP directa, sin depender del token).
    private func portInUse() -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        if fd < 0 { return false }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(UInt16(port).bigEndian)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        var tv = timeval(tv_sec: 1, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        let r = withUnsafePointer(to: &addr) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) } }
        return r == 0
    }

    /// Comprueba de forma síncrona si /api/state responde con el token actual.
    private func probeRunning() -> Bool {
        guard let url = URL(string: baseURL + "/api/state") else { return false }
        var req = URLRequest(url: url)
        req.timeoutInterval = 1.5
        req.setValue(token, forHTTPHeaderField: "X-Jarvis-Token")
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            ok = (resp as? HTTPURLResponse)?.statusCode == 200
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 2)
        return ok
    }

    private func append(_ line: String) {
        log.append(line)
        logFile?.write((line + "\n").data(using: .utf8) ?? Data())
        if log.count > 200 { log.removeFirst(log.count - 200) }
        if let r = line.range(of: "token=") { token = String(line[r.upperBound...]).components(separatedBy: CharacterSet.alphanumerics.union(["_", "-"]).inverted).first ?? token }
        DispatchQueue.main.async { self.onLog?(line) }
    }

    func stop() {
        if attached { return }  // no era nuestro: se deja en marcha
        process?.terminate()
        process = nil
    }

    func readTokenFile() {
        if token.isEmpty, let t = try? String(contentsOfFile: home + "/data/runtime/ui_token", encoding: .utf8) { token = t.trimmingCharacters(in: .whitespacesAndNewlines) }
    }

    var baseURL: String { "http://127.0.0.1:\(port)" }
    var panelURL: URL? { URL(string: "\(baseURL)/?token=\(token)") }

    func request(_ path: String, method: String = "GET", body: [String: Any]? = nil, completion: ((Data?) -> Void)? = nil) {
        readTokenFile()
        guard let url = URL(string: baseURL + path) else { return }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(token, forHTTPHeaderField: "X-Jarvis-Token")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(baseURL, forHTTPHeaderField: "Origin")
        if let body = body { req.httpBody = try? JSONSerialization.data(withJSONObject: body) }
        URLSession.shared.dataTask(with: req) { data, _, _ in DispatchQueue.main.async { completion?(data) } }.resume()
    }
}

/// Cliente SSE mínimo para /api/events.
final class EventStream: NSObject, URLSessionDataDelegate {
    private var session: URLSession?
    private var buffer = ""
    private let backend: Backend
    var onEvent: (([String: Any]) -> Void)?
    private var retry: DispatchWorkItem?

    init(backend: Backend) { self.backend = backend }

    func connect() {
        backend.readTokenFile()
        guard !backend.token.isEmpty, let url = URL(string: backend.baseURL + "/api/events?token=" + backend.token) else { scheduleRetry(); return }
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 3600
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
        var req = URLRequest(url: url)
        req.setValue(backend.baseURL, forHTTPHeaderField: "Origin")
        session?.dataTask(with: req).resume()
    }

    private func scheduleRetry() {
        retry?.cancel()
        let item = DispatchWorkItem { [weak self] in self?.connect() }
        retry = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 2, execute: item)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer += String(decoding: data, as: UTF8.self)
        while let range = buffer.range(of: "\n\n") {
            let chunk = String(buffer[..<range.lowerBound])
            buffer = String(buffer[range.upperBound...])
            for line in chunk.split(separator: "\n") where line.hasPrefix("data: ") {
                let json = line.dropFirst(6)
                if let d = json.data(using: .utf8), let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
                    DispatchQueue.main.async { self.onEvent?(obj) }
                }
            }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        session.invalidateAndCancel()
        scheduleRetry()
    }
}

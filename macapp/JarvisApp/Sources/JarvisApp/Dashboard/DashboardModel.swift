import Foundation
import SwiftUI

enum DashboardTab: String, CaseIterable, Identifiable {
    case sessions, runs, specs, projects, usage, memory, notices
    var id: String { rawValue }
    var title: String {
        switch self {
        case .sessions: return "Sesiones"
        case .runs: return "Runs"
        case .specs: return "Specs"
        case .projects: return "Proyectos"
        case .usage: return "Uso"
        case .memory: return "Memoria"
        case .notices: return "Avisos"
        }
    }
    var symbol: String {
        switch self {
        case .sessions: return "terminal"
        case .runs: return "play.circle"
        case .specs: return "doc.text"
        case .projects: return "folder"
        case .usage: return "gauge.with.needle"
        case .memory: return "brain"
        case .notices: return "bell"
        }
    }
}

/// Estado del panel nativo. Carga por HTTP y se refresca por eventos SSE (vía `handle`) y por sondeo suave.
@MainActor
final class DashboardModel: ObservableObject {
    let api: API
    @Published var tab: DashboardTab = .sessions
    @Published var sessions: [JSON] = []
    @Published var needsYou: [String] = []
    @Published var selectedSession: String?
    @Published var sessionDetail: JSON = JSON(nil)
    @Published var runs: [JSON] = []
    @Published var runStats: JSON = JSON(nil)
    @Published var selectedRun: String?
    @Published var runDetail: JSON = JSON(nil)
    @Published var runEvents: [JSON] = []
    @Published var projects: [JSON] = []
    @Published var projectsRoot = ""
    @Published var selectedProject: String?
    @Published var projectDetail: JSON = JSON(nil)
    @Published var documents: [JSON] = []
    @Published var selectedDocument: String?
    @Published var document: JSON = JSON(nil)
    @Published var usage: JSON = JSON(nil)
    @Published var announcements: [JSON] = []
    @Published var steers: [JSON] = []
    @Published var memoryQuery = ""
    @Published var memoryItems: [JSON] = []
    @Published var memoryProjects: [JSON] = []
    @Published var selectedMemory: String?
    @Published var memoryDetail: JSON = JSON(nil)
    @Published var foreman: JSON = JSON(nil)
    @Published var lastError: String?
    @Published var busy = false
    private var timer: Timer?
    var visible = false { didSet { if visible { Task { await refresh(all: true) } } } }

    init(api: API) {
        self.api = api
        timer = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.visible else { return }
                await self.refresh(all: false)
            }
        }
    }

    // MARK: - Eventos SSE
    func handle(_ ev: [String: Any]) {
        guard visible, let kind = ev["kind"] as? String else { return }
        switch kind {
        case "sessions.event": Task { await loadSessions() }
        case "runs.run_started", "runs.run_finished", "runs.run_updated":
            Task { await loadRuns(); if let id = (ev["run"] as? [String: Any])?["id"] as? String, id == selectedRun { await loadRunDetail() } }
        case "runs.event":
            if let id = ev["run_id"] as? String, id == selectedRun { Task { await loadRunEvents() } }
        case "announce": Task { await loadAnnouncements() }
        case "usage.updated": usage = JSON(ev)
        case "documents.changed": Task { await loadDocuments(); await loadDocument() }
        case "memory.changed", "memory.created", "memory.updated": Task { await loadMemory() }
        default: break
        }
    }

    func show(view: String, target: String?, path: String?) {
        switch view {
        case "sessions": tab = .sessions
        case "runs": tab = .runs
        case "projects": tab = .projects
        case "usage": tab = .usage
        case "specs":
            tab = .specs
            if let target { selectedProject = target }
            if let path { selectedDocument = path }
            Task { await loadDocuments(); await loadDocument() }
        default:
            tab = .memory
            if let target, view == "memory" || view == "local" { selectedMemory = target; Task { await loadMemoryDetail() } }
        }
    }

    // MARK: - Carga
    func refresh(all: Bool) async {
        if all || tab == .sessions { await loadSessions() }
        if all || tab == .runs { await loadRuns(); if selectedRun != nil { await loadRunDetail() } }
        if all || tab == .projects { await loadProjects() }
        if all || tab == .specs { await loadDocuments() }
        if all || tab == .usage { await loadUsage() }
        if all || tab == .notices { await loadAnnouncements() }
        if all || tab == .memory { if memoryItems.isEmpty { await loadMemory() } }
        if let f = try? await api.get("/api/foreman") { foreman = f }
    }

    private func run<T>(_ op: () async throws -> T) async -> T? {
        do { let v = try await op(); lastError = nil; return v } catch { lastError = error.localizedDescription; return nil }
    }

    func loadSessions() async {
        guard let j = await run({ try await api.get("/api/sessions") }) else { return }
        sessions = j["sessions"].array.filter { $0["state"].str != "fresh" }
        needsYou = j["needs_you"].strings
        if let sel = selectedSession, sessions.contains(where: { $0["session_id"].str == sel }) { await loadSessionDetail() } else { selectedSession = nil }
    }

    func loadSessionDetail() async {
        guard let id = selectedSession else { return }
        if let j = await run({ try await api.get("/api/sessions/\(id)") }) { sessionDetail = j }
    }

    func steer(_ text: String) async -> String {
        guard let id = selectedSession else { return "sin sesión" }
        return (await run({ try await api.post("/api/sessions/\(id)/steer", ["text": text]) }))?["outcome"].str ?? (lastError ?? "error")
    }

    func answer(_ key: String) async -> String {
        guard let id = selectedSession else { return "sin sesión" }
        return (await run({ try await api.post("/api/sessions/\(id)/answer", ["key": key]) }))?["outcome"].str ?? (lastError ?? "error")
    }

    func loadRuns() async {
        guard let j = await run({ try await api.get("/api/runs", query: ["limit": "100"]) }) else { return }
        runs = j["runs"].array
        runStats = j["stats"]
    }

    func loadRunDetail() async {
        guard let id = selectedRun else { return }
        if let j = await run({ try await api.get("/api/runs/\(id)") }) { runDetail = j }
        await loadRunEvents()
    }

    func loadRunEvents() async {
        guard let id = selectedRun else { return }
        if let j = await run({ try await api.get("/api/runs/\(id)/events", query: ["limit": "400"]) }) { runEvents = j["events"].array }
    }

    func cancelRun(_ id: String) async { _ = await run({ try await api.post("/api/runs/\(id)/cancel") }); await loadRuns() }

    func createRun(project: String, prompt: String) async -> Bool {
        let ok = await run({ try await api.post("/api/runs", ["project": project, "prompt": prompt]) }) != nil
        await loadRuns()
        return ok
    }

    func loadProjects() async {
        guard let j = await run({ try await api.get("/api/projects") }) else { return }
        projects = j["projects"].array
        projectsRoot = j["root"].str
        if selectedProject != nil { await loadProjectDetail() }
    }

    func loadProjectDetail() async {
        guard let p = selectedProject else { return }
        if let j = await run({ try await api.get("/api/projects/detail", query: ["path": p]) }) { projectDetail = j }
    }

    func loadDocuments() async {
        if projects.isEmpty { await loadProjects() }
        guard let p = selectedProject ?? projects.first?["path"].string else { documents = []; return }
        if selectedProject == nil { selectedProject = p }
        guard let j = await run({ try await api.get("/api/documents", query: ["project": p]) }) else { return }
        documents = j["documents"].array
        if selectedDocument == nil || !documents.contains(where: { $0["path"].str == selectedDocument }) {
            selectedDocument = documents.first?["path"].string
        }
        await loadDocument()
    }

    func loadDocument() async {
        guard let p = selectedProject, let d = selectedDocument else { document = JSON(nil); return }
        if let j = await run({ try await api.get("/api/documents/read", query: ["project": p, "path": d]) }) { document = j }
    }

    func approveDocument() async {
        guard let p = selectedProject, let d = selectedDocument else { return }
        _ = await run({ try await api.post("/api/documents/approve", ["project": p, "path": d]) })
        await loadDocuments()
    }

    func saveSection(_ number: Int, body: String, title: String) async {
        guard let p = selectedProject, let d = selectedDocument else { return }
        _ = await run({ try await api.post("/api/documents/section", ["project": p, "path": d, "number": number, "body": body, "title": title]) })
        await loadDocuments()
    }

    func startBuild() async -> Bool {
        guard let p = selectedProject, let d = selectedDocument else { return false }
        let ok = await run({ try await api.post("/api/documents/build", ["project": p, "path": d]) }) != nil
        await loadRuns()
        return ok
    }

    func loadUsage() async { if let j = await run({ try await api.get("/api/usage") }) { usage = j } }

    func loadAnnouncements() async {
        guard let j = await run({ try await api.get("/api/announcements") }) else { return }
        announcements = j["items"].array
        steers = j["steers"].array
    }

    func loadMemory() async {
        let q = memoryQuery.trimmingCharacters(in: .whitespaces)
        if q.isEmpty {
            guard let j = await run({ try await api.get("/api/memory/list") }) else { return }
            memoryItems = j["items"].array
            memoryProjects = j["projects"].array
        } else {
            guard let j = await run({ try await api.get("/api/memory/search", query: ["q": q, "limit": "40"]) }) else { return }
            memoryItems = j["hits"].array
        }
    }

    func loadMemoryDetail() async {
        guard let id = selectedMemory else { return }
        if let j = await run({ try await api.get("/api/memory/\(id)") }) { memoryDetail = j }
    }

    func askAbout(_ id: String) async { _ = await run({ try await api.post("/api/memory/ask/\(id)") }) }
    func sendChat(_ text: String) async { _ = await run({ try await api.post("/api/chat", ["text": text]) }) }
}

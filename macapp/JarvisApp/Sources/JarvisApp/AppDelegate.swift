import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let backend = Backend()
    var events: EventStream!
    var orbWindow: NSPanel!
    var orb: OrbView!
    var bubble: BubbleWindow!
    var statusItem: NSStatusItem!
    var hotkey: HotKey?
    var state = "DISABLED"
    var listening = false
    var assistantName = "Jarvis"
    var pendingApproval: [String: Any]?
    let positionKey = "orbPosition"
    var dashboard: DashboardWindowController!
    var dashboardModel: DashboardModel!

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupOrb()
        setupStatusItem()
        dashboardModel = DashboardModel(api: API(backend: backend))
        dashboard = DashboardWindowController(model: dashboardModel)
        hotkey = HotKey()
        hotkey?.action = { [weak self] in self?.primaryAction() }
        backend.onLog = { [weak self] line in
            guard let s = self else { return }
            if line.contains("Panel:") { s.events.connect() }
            if line.contains("Micrófono no disponible") || line.contains("no se pudo") { s.bubble.show(line, near: s.orbWindow) }
        }
        events = EventStream(backend: backend)
        events.onEvent = { [weak self] ev in self?.handle(ev) }
        if !backend.start() {
            bubble.show("No se pudo iniciar el backend. Ejecuta `jarvis app install` en el proyecto.", near: orbWindow, seconds: 30)
        } else if backend.attached {
            events.connect()
            bubble.show("Conectado al backend que ya estaba en marcha.", near: orbWindow, seconds: 5)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        backend.stop()
        UserDefaults.standard.set(NSStringFromPoint(orbWindow.frame.origin), forKey: positionKey)
    }

    // MARK: - UI
    func setupOrb() {
        let size: CGFloat = 72
        orbWindow = NSPanel(contentRect: NSRect(x: 0, y: 0, width: size, height: size), styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
        orbWindow.isOpaque = false
        orbWindow.backgroundColor = .clear
        orbWindow.hasShadow = false
        orbWindow.level = .floating
        orbWindow.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        orbWindow.isMovableByWindowBackground = false
        orbWindow.hidesOnDeactivate = false
        orb = OrbView(frame: NSRect(x: 0, y: 0, width: size, height: size))
        orb.onClick = { [weak self] in self?.primaryAction() }
        orb.onRightClick = { [weak self] ev in self?.showMenu(ev) }
        orbWindow.contentView = orb
        if let saved = UserDefaults.standard.string(forKey: positionKey), NSScreen.main != nil {
            orbWindow.setFrameOrigin(NSPointFromString(saved))
        } else if let screen = NSScreen.main {
            let f = screen.visibleFrame
            orbWindow.setFrameOrigin(NSPoint(x: f.maxX - size - 24, y: f.minY + 24))
        }
        orbWindow.orderFrontRegardless()
        bubble = BubbleWindow()
        bubble.onPrimary = { [weak self] in self?.primaryAction() }
        bubble.onReject = { [weak self] in self?.reject() }
        bubble.onWake = { [weak self] in self?.toggleListening() }
        orb.onMove = { [weak self] in
            guard let self else { return }
            self.bubble.reposition(near: self.orbWindow)
            UserDefaults.standard.set(NSStringFromPoint(self.orbWindow.frame.origin), forKey: self.positionKey)
        }
    }

    func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "◉"
        statusItem.menu = buildMenu()
    }

    func buildMenu() -> NSMenu {
        let m = NSMenu()
        m.delegate = self
        m.addItem(withTitle: "\(assistantName) — \(state)", action: nil, keyEquivalent: "")
        m.addItem(.separator())
        m.addItem(withTitle: "Hablar (pulsar-para-hablar)   ⌥Espacio", action: #selector(ptt), keyEquivalent: "")
        m.addItem(withTitle: "Detener respuesta", action: #selector(stop), keyEquivalent: "")
        let l = m.addItem(withTitle: listening ? "Desactivar escucha «hey jarvis»" : "Activar escucha «hey jarvis»", action: #selector(toggleListening), keyEquivalent: "")
        l.state = listening ? .on : .off
        if let ap = pendingApproval, let id = ap["id"] as? String {
            m.addItem(.separator())
            m.addItem(withTitle: "Aprobar: \(String(describing: ap["description"] ?? ""))".prefix(80) + "", action: #selector(approve), keyEquivalent: "")
            m.addItem(withTitle: "Rechazar (\(id))", action: #selector(reject), keyEquivalent: "")
        }
        m.addItem(.separator())
        m.addItem(withTitle: "Abrir panel (sesiones, runs, specs, memoria)", action: #selector(openPanel), keyEquivalent: "p")
        m.addItem(withTitle: "Panel web (grafo de memoria)", action: #selector(openWebPanel), keyEquivalent: "")
        m.addItem(withTitle: "Ocultar/mostrar orbe", action: #selector(toggleOrb), keyEquivalent: "")
        m.addItem(withTitle: "Reiniciar backend", action: #selector(restartBackend), keyEquivalent: "")
        m.addItem(withTitle: "Ver registro del backend", action: #selector(showLog), keyEquivalent: "")
        m.addItem(.separator())
        m.addItem(withTitle: "Salir de \(assistantName)", action: #selector(quit), keyEquivalent: "q")
        return m
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        for item in buildMenu().items { menu.addItem(item.copy() as! NSMenuItem) }
    }

    func showMenu(_ event: NSEvent) { NSMenu.popUpContextMenu(buildMenu(), with: event, for: orb) }

    // MARK: - Acciones
    func primaryAction() {
        bubble.reveal(near: orbWindow)
        switch state {
        case "IDLE", "DISABLED", "ERROR": ptt()
        case "AWAITING_APPROVAL": if pendingApproval?["kind"] as? String == "question" { ptt() } else { approve() }
        default: stop()
        }
    }

    @objc func ptt() { backend.request("/api/ptt", method: "POST") { [weak self] data in
        if let d = data, let s = String(data: d, encoding: .utf8), s.contains("detail") { self?.bubble.show("El modo voz no está activo: " + s, near: self!.orbWindow) } } }
    @objc func stop() { backend.request("/api/cancel", method: "POST") }
    @objc func toggleListening() { backend.request("/api/listening", method: "POST", body: ["enabled": !listening]) }
    @objc func approve() { guard let ap = pendingApproval, let id = ap["id"] as? String else { return }
        backend.request("/api/approvals/\(id)/approve", method: "POST", body: ["args_hash": ap["args_hash"] ?? ""]) }
    @objc func reject() { guard let ap = pendingApproval, let id = ap["id"] as? String else { return }
        backend.request("/api/approvals/\(id)/reject", method: "POST") }
    @objc func openPanel() { dashboard.show() }
    @objc func openWebPanel() { if let u = backend.panelURL { NSWorkspace.shared.open(u) } }
    @objc func toggleOrb() { if orbWindow.isVisible { orbWindow.orderOut(nil); bubble.orderOut(nil) } else { orbWindow.orderFrontRegardless() } }
    @objc func restartBackend() { backend.stop(); DispatchQueue.main.asyncAfter(deadline: .now() + 1) { _ = self.backend.start() } }
    @objc func showLog() {
        let alert = NSAlert()
        alert.messageText = "Registro del backend"
        alert.informativeText = backend.log.suffix(25).joined(separator: "\n")
        alert.runModal()
    }
    @objc func quit() { NSApp.terminate(nil) }

    // MARK: - Eventos del backend
    func handle(_ ev: [String: Any]) {
        guard let kind = ev["kind"] as? String else { return }
        defer { refreshConversation() }
        MainActor.assumeIsolated { dashboardModel.handle(ev) }  // los eventos llegan ya en el hilo principal (EventStream)
        switch kind {
        case "announce":
            if let t = ev["text"] as? String { bubble.show("\(assistantName): " + t, near: orbWindow, seconds: 25) }
        case "ui.show":
            let view = ev["view"] as? String ?? "memory"
            MainActor.assumeIsolated { dashboardModel.show(view: view, target: ev["target"] as? String, path: ev["path"] as? String) }
            dashboard.show()
        case "hello":
            if let st = ev["state"] as? [String: Any] {
                setState(st["state"] as? String ?? "IDLE")
                listening = st["listening"] as? Bool ?? false
                if let n = st["assistant"] as? String { assistantName = n; orb.name = n }
                if let p = st["pending_approvals"] as? [[String: Any]] { pendingApproval = p.first }
            }
        case "state": setState(ev["state"] as? String ?? state)
        case "voice.transcript": if let t = ev["text"] as? String { bubble.show("Tú: " + t, near: orbWindow, seconds: 8) }
        case "chat.user": if let t = ev["text"] as? String, (ev["source"] as? String) != "voice" { bubble.show("Tú: " + t, near: orbWindow, seconds: 6) }
        case "chat.message":
            if let t = ev["text"] as? String { bubble.show(t, near: orbWindow, seconds: 20) }
            if let p = ev["pending_approvals"] as? [[String: Any]] { pendingApproval = p.first }
        case "approval.requested":
            pendingApproval = ev["approval"] as? [String: Any]
            let d = pendingApproval?["description"] as? String ?? ""
            if (pendingApproval?["kind"] as? String) == "question" {
                let opts = ((pendingApproval?["args"] as? [String: Any])?["options"] as? [String]) ?? []
                bubble.show("\((pendingApproval?["args"] as? [String: Any])?["question"] as? String ?? d)\n→ \(opts.joined(separator: " · "))\n(responde hablando o escribiendo)", near: orbWindow, seconds: 90)
            } else {
                bubble.show("¿Permiso para \(d)?\n(clic en el orbe o di «sí» · clic derecho para rechazar)", near: orbWindow, seconds: 60)
            }
        case "approvals":
            pendingApproval = (ev["pending"] as? [[String: Any]])?.first
        case "chat.system": if let t = ev["text"] as? String { bubble.show(t, near: orbWindow, seconds: 10) }
        case "voice.discarded": bubble.show("No te oí bien; vuelve a intentarlo.", near: orbWindow, seconds: 4)
        case "config.changed": if let n = ev["display_name"] as? String { assistantName = n; orb.name = n }
        case "voice.status":
            if let e = ev["mic_error"] as? String { bubble.show("Micrófono: " + e, near: orbWindow, seconds: 20) }
            if let e = ev["wake_error"] as? String { bubble.show("Detector: " + e, near: orbWindow, seconds: 20) }
            listening = ev["listening_enabled"] as? Bool ?? listening
        default: break
        }
    }

    func refreshConversation() {
        bubble.update(state: state, name: assistantName, listening: listening,
                      approval: pendingApproval != nil && pendingApproval?["kind"] as? String != "question")
    }

    func setState(_ s: String) {
        state = s
        orb.state = s
        refreshConversation()
        if !["IDLE", "DISABLED"].contains(s) { bubble.reveal(near: orbWindow) }
        statusItem.button?.title = ["IDLE": "◉", "LISTENING": "●", "THINKING": "◐", "TRANSCRIBING": "◑", "SPEAKING": "◔", "AWAITING_APPROVAL": "⚠", "ERROR": "✕"][s] ?? "○"
    }
}

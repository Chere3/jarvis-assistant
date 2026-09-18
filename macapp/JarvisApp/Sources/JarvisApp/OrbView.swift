import AppKit

/// El logo flotante: un orbe con anillo que cambia de color y animación según el estado del asistente.
final class OrbView: NSView {
    var state: String = "DISABLED" { didSet { needsDisplay = true; setAccessibilityLabel("Jarvis, " + voiceStateTitle(state)) } }
    var name: String = "J" { didSet { needsDisplay = true } }
    var level: CGFloat = 0
    private var phase: CGFloat = 0
    private var timer: Timer?
    var onClick: (() -> Void)?
    var onMove: (() -> Void)?
    var onRightClick: ((NSEvent) -> Void)?
    private var dragStart: NSPoint?
    private var moved = false
    private let reducedMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            guard let s = self else { return }
            s.phase += 0.08
            if !s.reducedMotion && ["LISTENING", "THINKING", "TRANSCRIBING", "SPEAKING"].contains(s.state) { s.needsDisplay = true }
        }
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityLabel("Jarvis, iniciar conversación")
        toolTip = "Clic: hablar · Clic mientras responde: detener · Clic derecho: menú · ⌥Espacio: atajo global"
    }

    required init?(coder: NSCoder) { fatalError() }

    private func colors() -> (NSColor, NSColor) {
        switch state {
        case "LISTENING": return (NSColor(calibratedRed: 0.36, green: 0.82, blue: 0.45, alpha: 1), NSColor(calibratedRed: 0.1, green: 0.5, blue: 0.3, alpha: 1))
        case "TRANSCRIBING", "THINKING": return (NSColor(calibratedRed: 0.48, green: 0.64, blue: 0.98, alpha: 1), NSColor(calibratedRed: 0.2, green: 0.3, blue: 0.7, alpha: 1))
        case "SPEAKING": return (NSColor(calibratedRed: 0.95, green: 0.72, blue: 0.35, alpha: 1), NSColor(calibratedRed: 0.7, green: 0.4, blue: 0.1, alpha: 1))
        case "AWAITING_APPROVAL": return (NSColor(calibratedRed: 0.98, green: 0.55, blue: 0.35, alpha: 1), NSColor(calibratedRed: 0.6, green: 0.2, blue: 0.1, alpha: 1))
        case "ERROR", "DISABLED": return (NSColor(calibratedWhite: 0.45, alpha: 1), NSColor(calibratedWhite: 0.2, alpha: 1))
        default: return (NSColor(calibratedRed: 0.55, green: 0.75, blue: 1.0, alpha: 1), NSColor(calibratedRed: 0.15, green: 0.25, blue: 0.55, alpha: 1))
        }
    }

    override func draw(_ dirtyRect: NSRect) {
        let r = bounds.insetBy(dx: 6, dy: 6)
        let (c1, _) = colors()
        let pulse: CGFloat = reducedMotion ? 0 : (state == "LISTENING" ? 0.10 * sin(phase * 2) : (state == "THINKING" ? 0.06 * sin(phase * 3) : (state == "SPEAKING" ? 0.08 * sin(phase * 4) : 0.02 * sin(phase))))
        // halo
        let haloRect = bounds.insetBy(dx: 1 - pulse * 10, dy: 1 - pulse * 10)
        let halo = NSBezierPath(ovalIn: haloRect)
        c1.withAlphaComponent(0.18 + pulse).setFill()
        halo.fill()
        // orbe
        let orb = NSBezierPath(ovalIn: r.insetBy(dx: pulse * 4, dy: pulse * 4))
        NSGradient(colors: [NSColor(calibratedWhite: 0.22, alpha: 1), NSColor(calibratedWhite: 0.06, alpha: 1)])?.draw(in: orb, relativeCenterPosition: NSPoint(x: -0.3, y: 0.4))
        orb.lineWidth = 1.5
        c1.withAlphaComponent(0.7).setStroke()
        orb.stroke()
        // anillo giratorio cuando piensa/transcribe
        if state == "THINKING" || state == "TRANSCRIBING" {
            let ring = NSBezierPath()
            let start = (phase * 60).truncatingRemainder(dividingBy: 360)
            ring.appendArc(withCenter: NSPoint(x: r.midX, y: r.midY), radius: r.width / 2 - 1, startAngle: start, endAngle: start + 110)
            ring.lineWidth = 2.5
            NSColor.white.withAlphaComponent(0.85).setStroke()
            ring.stroke()
        }
        // Voice activity is a state animation, not a measured microphone level.
        if state == "LISTENING" || state == "SPEAKING" {
            for index in 0..<5 {
                let h: CGFloat = reducedMotion ? CGFloat([10, 20, 28, 20, 10][index]) : 8 + 22 * abs(sin(phase * 1.8 + CGFloat(index) * 0.7))
                let bar = NSBezierPath(roundedRect: NSRect(x: r.midX - 17 + CGFloat(index) * 7, y: r.midY - h / 2, width: 4, height: h), xRadius: 2, yRadius: 2)
                NSColor.white.withAlphaComponent(0.95).setFill()
                bar.fill()
            }
            return
        }
        // letra
        let letter = String(name.prefix(1)).uppercased()
        let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: r.height * 0.5, weight: .semibold), .foregroundColor: NSColor.white.withAlphaComponent(0.95)]
        let size = letter.size(withAttributes: attrs)
        letter.draw(at: NSPoint(x: r.midX - size.width / 2, y: r.midY - size.height / 2), withAttributes: attrs)
    }

    override func accessibilityPerformPress() -> Bool { onClick?(); return true }

    override func mouseDown(with event: NSEvent) { dragStart = event.locationInWindow; moved = false }
    override func mouseDragged(with event: NSEvent) {
        guard let start = dragStart, let win = window else { return }
        let loc = event.locationInWindow
        let dx = loc.x - start.x, dy = loc.y - start.y
        if abs(dx) + abs(dy) > 2 { moved = true }
        var origin = NSPoint(x: win.frame.origin.x + dx, y: win.frame.origin.y + dy)
        if let screen = NSScreen.screens.first(where: { $0.frame.contains(NSEvent.mouseLocation) }) {
            origin.x = min(max(origin.x, screen.visibleFrame.minX), screen.visibleFrame.maxX - win.frame.width)
            origin.y = min(max(origin.y, screen.visibleFrame.minY), screen.visibleFrame.maxY - win.frame.height)
        }
        win.setFrameOrigin(origin)
        onMove?()
    }
    override func mouseUp(with event: NSEvent) {
        if !moved { onClick?() }
        dragStart = nil
    }
    override func rightMouseDown(with event: NSEvent) { onRightClick?(event) }
}

func voiceStateTitle(_ state: String) -> String {
    ["IDLE": "Listo para escucharte", "DISABLED": "Voz en pausa",
     "LISTENING": "Te escucho", "TRANSCRIBING": "Transcribiendo",
     "THINKING": "Pensando", "SPEAKING": "Hablando",
     "AWAITING_APPROVAL": "Necesito tu respuesta", "ERROR": "Necesita atención"][state] ?? "Conectando"
}

/// Native conversation card. Messages remain selectable and scrollable, including long permissions.
final class BubbleWindow: NSPanel {
    private let heading = NSTextField(labelWithString: "JARVIS")
    private let status = NSTextField(labelWithString: "Listo para escucharte")
    private let textView = NSTextView()
    private let scroll = NSScrollView()
    private let primary = NSButton(title: "Hablar", target: nil, action: nil)
    private let deny = NSButton(title: "Rechazar", target: nil, action: nil)
    private let wake = NSButton(title: "", target: nil, action: nil)
    private let closeButton = NSButton(title: "", target: nil, action: nil)
    private var hideTimer: Timer?
    private var currentState = "DISABLED"
    private var hasApproval = false
    var onPrimary: (() -> Void)?
    var onReject: (() -> Void)?
    var onWake: (() -> Void)?

    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 360, height: 252), styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
        isOpaque = false
        backgroundColor = .clear
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        hasShadow = true
        hidesOnDeactivate = false
        let box = NSVisualEffectView(frame: NSRect(x: 0, y: 0, width: 360, height: 252))
        box.material = .hudWindow
        box.state = .active
        box.wantsLayer = true
        box.layer?.cornerRadius = 22
        box.layer?.borderWidth = 1
        box.layer?.borderColor = NSColor.white.withAlphaComponent(0.16).cgColor
        box.layer?.masksToBounds = true
        heading.font = .systemFont(ofSize: 10, weight: .bold)
        heading.textColor = .secondaryLabelColor
        heading.frame = NSRect(x: 20, y: 220, width: 270, height: 14)
        status.font = .systemFont(ofSize: 19, weight: .semibold)
        status.frame = NSRect(x: 20, y: 190, width: 305, height: 26)
        closeButton.image = NSImage(systemSymbolName: "xmark", accessibilityDescription: "Ocultar conversación")
        closeButton.isBordered = false
        closeButton.frame = NSRect(x: 322, y: 216, width: 24, height: 24)
        closeButton.target = self; closeButton.action = #selector(dismissCard)
        closeButton.toolTip = "Ocultar tarjeta; Jarvis sigue disponible"
        scroll.frame = NSRect(x: 20, y: 68, width: 320, height: 108)
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        textView.frame = NSRect(x: 0, y: 0, width: 300, height: 108)
        textView.isEditable = false
        textView.isSelectable = true
        textView.drawsBackground = false
        textView.font = .systemFont(ofSize: 14)
        textView.textColor = .labelColor
        textView.textContainerInset = NSSize(width: 0, height: 4)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.autoresizingMask = [.width]
        textView.textContainer?.widthTracksTextView = true
        textView.textContainer?.containerSize = NSSize(width: 300, height: CGFloat.greatestFiniteMagnitude)
        scroll.documentView = textView
        primary.frame = NSRect(x: 18, y: 18, width: 140, height: 34)
        deny.frame = NSRect(x: 162, y: 18, width: 100, height: 34)
        wake.frame = NSRect(x: 298, y: 18, width: 44, height: 34)
        for button in [primary, deny, wake] { button.bezelStyle = .rounded; button.target = self }
        primary.action = #selector(performPrimary)
        deny.action = #selector(performReject)
        wake.action = #selector(performWake)
        primary.contentTintColor = .controlAccentColor
        deny.isHidden = true
        for view in [heading, status, closeButton, scroll, primary, deny, wake] { box.addSubview(view) }
        contentView = box
        update(state: "DISABLED", name: "Jarvis", listening: false, approval: false)
    }

    func update(state: String, name: String, listening: Bool, approval: Bool) {
        currentState = state
        hasApproval = approval
        heading.stringValue = name.uppercased() + "  ·  ⌥ ESPACIO"
        status.stringValue = voiceStateTitle(state)
        status.textColor = state == "ERROR" ? .systemRed : .labelColor
        primary.title = approval ? "Aprobar" : (["IDLE", "DISABLED", "ERROR", "AWAITING_APPROVAL"].contains(state) ? "Hablar" : "Detener")
        primary.image = NSImage(systemSymbolName: approval ? "checkmark.shield" : (primary.title == "Hablar" ? "mic.fill" : "stop.fill"), accessibilityDescription: primary.title)
        primary.imagePosition = .imageLeading
        primary.toolTip = primary.title + " · ⌥Espacio"
        deny.isHidden = !approval
        wake.image = NSImage(systemSymbolName: listening ? "ear.badge.waveform" : "ear", accessibilityDescription: listening ? "Desactivar palabra de activación" : "Activar palabra de activación")
        wake.toolTip = listening ? "Escucha «Jarvis» activada; clic para pausar" : "Activar escucha «Jarvis»"
        if approval || !["IDLE", "DISABLED", "ERROR"].contains(state) { hideTimer?.invalidate() }
    }

    func reposition(near orb: NSWindow) {
        guard let screen = orb.screen ?? NSScreen.main else { return }
        let safe = screen.visibleFrame.insetBy(dx: 8, dy: 8)
        var x = orb.frame.minX - frame.width - 12
        if x < safe.minX { x = orb.frame.maxX + 12 }
        x = min(max(x, safe.minX), safe.maxX - frame.width)
        let y = min(max(orb.frame.midY - frame.height / 2, safe.minY), safe.maxY - frame.height)
        setFrameOrigin(NSPoint(x: x, y: y))
    }

    func reveal(near orb: NSWindow) {
        reposition(near: orb)
        if textView.string.isEmpty { textView.string = "Habla con naturalidad. Puedes detener la respuesta en cualquier momento." }
        orderFrontRegardless()
    }

    func show(_ text: String, near orb: NSWindow, seconds: TimeInterval = 12) {
        textView.string = text
        textView.scrollToBeginningOfDocument(nil)
        reveal(near: orb)
        hideTimer?.invalidate()
        hideTimer = Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) { [weak self] _ in
            guard let self, !self.hasApproval, ["IDLE", "DISABLED", "ERROR"].contains(self.currentState) else { return }
            self.orderOut(nil)
        }
    }
    @objc private func dismissCard() { hideTimer?.invalidate(); orderOut(nil) }
    @objc private func performPrimary() { onPrimary?() }
    @objc private func performReject() { onReject?() }
    @objc private func performWake() { onWake?() }
}

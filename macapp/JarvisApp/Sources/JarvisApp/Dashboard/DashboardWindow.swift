import AppKit
import SwiftUI

/// Ventana nativa del panel: una sola instancia, se muestra/oculta desde el menú del orbe o por `ui.show`.
final class DashboardWindowController: NSWindowController, NSWindowDelegate {
    let model: DashboardModel

    init(model: DashboardModel) {
        self.model = model
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 720),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                              backing: .buffered, defer: false)
        window.title = "Panel"
        window.titlebarAppearsTransparent = true
        window.toolbarStyle = .unified
        window.minSize = NSSize(width: 880, height: 520)
        window.setFrameAutosaveName("JarvisDashboard")
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: DashboardView(model: model))
        super.init(window: window)
        window.delegate = self
    }

    required init?(coder: NSCoder) { fatalError() }

    func show(tab: DashboardTab? = nil) {
        if let tab { model.tab = tab }
        model.visible = true
        NSApp.activate(ignoringOtherApps: true)
        if window?.isVisible != true { window?.center() }
        window?.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) { model.visible = false }
}

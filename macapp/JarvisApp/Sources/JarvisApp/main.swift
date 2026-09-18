import AppKit

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory) // sin icono en el Dock: vive como logo flotante + barra de menús
app.run()

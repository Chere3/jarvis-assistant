import Carbon
import AppKit

/// Atajo global (por defecto ⌥Space) sin permiso de Accesibilidad, vía Carbon RegisterEventHotKey.
final class HotKey {
    private var ref: EventHotKeyRef?
    private var handler: EventHandlerRef?
    var action: (() -> Void)?
    private static var shared: HotKey?

    init(keyCode: UInt32 = UInt32(kVK_Space), modifiers: UInt32 = UInt32(optionKey)) {
        HotKey.shared = self
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
            DispatchQueue.main.async { HotKey.shared?.action?() }
            return noErr
        }, 1, &spec, nil, &handler)
        let id = EventHotKeyID(signature: OSType(0x4A525653), id: 1) // "JRVS"
        RegisterEventHotKey(keyCode, modifiers, id, GetApplicationEventTarget(), 0, &ref)
    }

    deinit {
        if let r = ref { UnregisterEventHotKey(r) }
        if let h = handler { RemoveEventHandler(h) }
    }
}

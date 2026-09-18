"""Pulsa UNA tecla en la pestaña de Terminal.app donde corre una sesión de Claude Code.

Es el módulo más delicado del proyecto: manda una pulsación sintética a una ventana del usuario.
Tres decisiones lo mantienen a raya:
1. El destino se localiza por tty (pid → tty → pestaña), nunca por la ventana en primer plano.
   Si ninguna pestaña de Terminal tiene ese tty, se devuelve `not_found` y NO se pulsa nada.
2. El vocabulario es cerrado: Return, Escape o un dígito 1-9. Nunca se escribe texto.
3. Nada aquí lanza excepciones: cada camino devuelve un resultado auditable.
Requiere permiso de Accesibilidad para la app que ejecuta el backend (Jarvis.app o la terminal).
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass

SENT = "sent"
NO_TTY = "no_tty"
NOT_FOUND = "not_found"
NOT_PERMITTED = "not_permitted"
FAILED = "failed"
BAD_KEY = "bad_key"

OUTCOME_LABEL = {SENT: "tecla enviada", NO_TTY: "ese proceso no tiene terminal", NOT_FOUND: "no está en una pestaña de Terminal.app",
                 NOT_PERMITTED: "macOS no me deja controlar Terminal (falta el permiso de Accesibilidad)",
                 FAILED: "falló el envío", BAD_KEY: "tecla no permitida"}

LOOKUP_TIMEOUT = 10.0
SEND_TIMEOUT = 20.0
_RETURN_KEY_CODE = 36
_ESCAPE_KEY_CODE = 53
_ALIASES = {"enter": "return", "return": "return", "intro": "return", "sí": "return", "si": "return", "yes": "return",
            "y": "return", "escape": "escape", "esc": "escape", "cancelar": "escape", "no": "escape", "n": "escape"}
_PERMISSION_MARKERS = ("-1743", "-25211", "not allowed assistive access", "not authorized to send apple events",
                       "is not allowed to send keystrokes", "assistive access")


@dataclass(frozen=True)
class TerminalTab:
    window_id: int
    tab_index: int
    tty: str


def normalize_key(key: object) -> str | None:
    if not isinstance(key, str):
        return None
    k = key.strip().lower()
    if k in _ALIASES:
        return _ALIASES[k]
    if len(k) == 1 and k in "123456789":
        return k
    return None


def spoken_key(normalized: str) -> str:
    return {"return": "Intro", "escape": "Escape"}.get(normalized, f"la opción {normalized}")


def normalize_tty(tty: str | None) -> str | None:
    if not tty:
        return None
    t = tty.strip()
    if not t or t == "??":
        return None
    if not t.startswith("/dev/"):
        t = "/dev/" + t
    return t if re.fullmatch(r"/dev/tty[a-zA-Z0-9]+", t) else None


def tty_for_pid(pid: object) -> str | None:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        out = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)], capture_output=True, text=True, timeout=1.0)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return normalize_tty(out.stdout.strip())


def _terminal_is_running() -> bool:
    if shutil.which("pgrep") is None:
        return False
    try:
        return subprocess.run(["pgrep", "-x", "Terminal"], capture_output=True, timeout=1.0).returncode == 0
    except Exception:
        return False


async def _osascript(script: str, timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec("osascript", "-e", script, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return -1, "", "timeout"
    return proc.returncode or 0, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


def _is_permission_error(stderr: str) -> bool:
    low = stderr.lower()
    return any(m in low for m in _PERMISSION_MARKERS)


_ENUMERATE_SCRIPT = '''
tell application "Terminal"
    set out to ""
    repeat with w in windows
        set wid to (id of w) as text
        set n to (count of tabs of w)
        repeat with i from 1 to n
            set tt to ""
            try
                set tt to (tty of tab i of w) as text
            end try
            set out to out & wid & ":" & (i as text) & ":" & tt & linefeed
        end repeat
    end repeat
    return out
end tell
'''


async def find_terminal_tab(tty: str | None) -> TerminalTab | None:
    want = normalize_tty(tty)
    if want is None or not await asyncio.to_thread(_terminal_is_running):
        return None
    code, stdout, _ = await _osascript(_ENUMERATE_SCRIPT, LOOKUP_TIMEOUT)
    if code != 0:
        return None
    for line in stdout.splitlines():
        parts = line.strip().split(":")
        if len(parts) != 3 or normalize_tty(parts[2]) != want:
            continue
        try:
            return TerminalTab(window_id=int(parts[0]), tab_index=int(parts[1]), tty=want)
        except ValueError:
            continue
    return None


def _send_script(tab: TerminalTab, normalized: str) -> str:
    if normalized == "return":
        press = f"key code {_RETURN_KEY_CODE}"
    elif normalized == "escape":
        press = f"key code {_ESCAPE_KEY_CODE}"
    else:
        press = f'keystroke "{normalized}"'
    return f'''
tell application "Terminal"
    set w to first window whose id is {tab.window_id}
    set t to tab {tab.tab_index} of w
    if ((tty of t) as text) is not "{tab.tty}" then error "tab changed"
    set selected tab of w to t
    set index of w to 1
    activate
end tell
delay 0.2
tell application "System Events" to tell process "Terminal" to {press}
'''


async def press_key(pid: object, key: object) -> str:
    """Pulsa `key` en la pestaña de Terminal.app del proceso `pid`. Devuelve un resultado, nunca lanza."""
    normalized = normalize_key(key)
    if normalized is None:
        return BAD_KEY
    tty = await asyncio.to_thread(tty_for_pid, pid)
    if tty is None:
        return NO_TTY
    tab = await find_terminal_tab(tty)
    if tab is None:
        return NOT_FOUND
    code, _, stderr = await _osascript(_send_script(tab, normalized), SEND_TIMEOUT)
    if code == 0:
        return SENT
    return NOT_PERMITTED if _is_permission_error(stderr) else FAILED

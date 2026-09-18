"""Notificación nativa de macOS como respaldo cuando no hay voz ni app conectada.

El texto procede del transcript de otra sesión (no confiable), así que nunca se interpola en el
código AppleScript: el script es fijo y el texto viaja como argumentos (`argv`), puro dato.
"""
from __future__ import annotations

import asyncio
import shutil
import sys

_TITLE_MAX, _SUBTITLE_MAX, _MESSAGE_MAX = 120, 120, 300
_TIMEOUT = 5.0
_SCRIPT = """\
on run argv
    set theTitle to item 1 of argv
    set theMessage to item 2 of argv
    set theSubtitle to item 3 of argv
    if theSubtitle is "" then
        display notification theMessage with title theTitle
    else
        display notification theMessage with title theTitle subtitle theSubtitle
    end if
end run
"""


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def available() -> bool:
    return sys.platform == "darwin" and shutil.which("osascript") is not None


async def notify(title: str, message: str, subtitle: str = "") -> bool:
    try:
        if not available():
            return False
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-", _cut(str(title or ""), _TITLE_MAX), _cut(str(message or ""), _MESSAGE_MAX),
            _cut(str(subtitle or ""), _SUBTITLE_MAX), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            await asyncio.wait_for(proc.communicate(_SCRIPT.encode("utf-8")), timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.communicate()
            except Exception:
                pass
            return False
        return proc.returncode == 0
    except Exception:
        return False

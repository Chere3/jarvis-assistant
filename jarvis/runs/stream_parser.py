"""Parseo puro de la salida de `claude -p --output-format stream-json`. Sin E/S."""
from __future__ import annotations

import json
from typing import Any

_SUMMARY_MAX = 160


def parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        parsed = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def event_kind(event: dict) -> str:
    return event.get("type") or "unknown"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def extract_init_metadata(event: dict) -> dict:
    return {"model": event.get("model") or "", "cwd": event.get("cwd") or ""}


def extract_result_metrics(event: dict) -> dict:
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    result = event.get("result")
    return {"cost_usd": _f(event.get("total_cost_usd")), "input_tokens": _i(usage.get("input_tokens")),
            "output_tokens": _i(usage.get("output_tokens")), "cache_read_tokens": _i(usage.get("cache_read_input_tokens")),
            "cache_creation_tokens": _i(usage.get("cache_creation_input_tokens")), "num_turns": _i(event.get("num_turns")),
            "result_text": result if isinstance(result, str) else "", "is_error": bool(event.get("is_error"))}


def extract_assistant_usage(event: dict) -> dict:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    return {"input_tokens": _i(usage.get("input_tokens")), "output_tokens": _i(usage.get("output_tokens")),
            "cache_read_tokens": _i(usage.get("cache_read_input_tokens")),
            "cache_creation_tokens": _i(usage.get("cache_creation_input_tokens"))}


def assistant_parts(event: dict) -> tuple[str, list[str]]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), list) else []
    texts: list[str] = []
    tools: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            tools.append(str(block.get("name") or ""))
    return "\n".join(texts), tools


def summarize_assistant(event: dict) -> str:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), list) else []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = " ".join((block.get("text") or "").split())
            if text:
                return text[:_SUMMARY_MAX]
        elif block.get("type") == "tool_use":
            inp = block.get("input") or {}
            target = inp.get("file_path") or inp.get("command") or inp.get("pattern") or inp.get("description") or ""
            return f"{block.get('name') or 'tool'}: {' '.join(str(target).split())}".strip().rstrip(":")[:_SUMMARY_MAX]
    return ""


READ_ONLY_TOOLS = frozenset({"read", "glob", "grep", "ls", "todowrite", "todoread", "websearch", "webfetch", "skill",
                             "notebookread", "listmcpresources", "readmcpresource", "exitplanmode", "askuserquestion",
                             "bashoutput", "killshell", "slashcommand", "toolsearch"})
OK = "ok"
STALLED = "stalled"        # terminó preguntando algo, sin cambiar nada
NO_CHANGES = "no_changes"  # terminó limpio pero nada indica que cambiara algo
_TRAILING = " \t\r\n*_`\"')】]>"


def ends_with_question(text: str) -> bool:
    return (text or "").rstrip(_TRAILING).endswith("?")


def changed_anything(events: list[dict]) -> bool:
    for ev in events:
        if not isinstance(ev, dict) or event_kind(ev) != "assistant":
            continue
        _, tools = assistant_parts(ev)
        if any(t.lower() not in READ_ONLY_TOOLS for t in tools):
            return True
    return False


def assess_outcome(events: list[dict], result_text: str = "") -> str:
    assistant = [e for e in events if isinstance(e, dict) and event_kind(e) == "assistant"]
    if not assistant or changed_anything(assistant):
        return OK
    final = (result_text or "").strip()
    if not final:
        for ev in reversed(assistant):
            text, _ = assistant_parts(ev)
            if text.strip():
                final = text.strip()
                break
    return STALLED if final and ends_with_question(final) else NO_CHANGES


PAYLOAD_MAX_CHARS = 256 * 1024
PAYLOAD_STRING_MAX = 8 * 1024


def _shrink(value: Any, budget: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= budget else f"{value[:budget]}… [truncado, {len(value)} caracteres]"
    if isinstance(value, dict):
        return {k: _shrink(v, budget) for k, v in value.items()}
    if isinstance(value, list):
        return [_shrink(v, budget) for v in value]
    return value


def cap_payload(line: str, event: dict) -> str:
    if len(line) <= PAYLOAD_MAX_CHARS:
        return line
    try:
        shrunk = json.dumps(_shrink(event, PAYLOAD_STRING_MAX))
    except (TypeError, ValueError):
        shrunk = ""
    if shrunk and len(shrunk) <= PAYLOAD_MAX_CHARS:
        return shrunk
    return json.dumps({"type": event_kind(event), "jarvis_truncated": True, "jarvis_original_chars": len(line),
                       "jarvis_preview": line[:PAYLOAD_STRING_MAX]})

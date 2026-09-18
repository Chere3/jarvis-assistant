"""Uso de la suscripción: lo último que el CLI dijo sobre las ventanas de 5 horas y 7 días.

No hay gasto en dólares que informar (no se usa API key). Lo que sí importa es cuánto de cada ventana se ha consumido.
El dato llega como `RateLimitEvent` del Agent SDK durante un turno; aquí se guarda con su marca de tiempo por ventana.
La ausencia se conserva como ausencia: una ventana nunca observada devuelve `utilization: None`, nunca 0.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

STALE_AFTER_SEC = 30 * 60
KNOWN_WINDOWS = ("five_hour", "seven_day")
WINDOW_LABELS = {"five_hour": "Sesión de 5 horas", "seven_day": "Semana (7 días)", "seven_day_opus": "Semana Opus",
                 "seven_day_sonnet": "Semana Sonnet", "overage": "Excedente"}


def window_label(key: str) -> str:
    return WINDOW_LABELS.get(key, key.replace("_", " "))


def _percent(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    v = float(raw)
    if v != v or v < 0 or v in (float("inf"), float("-inf")):
        return None
    pct = v * 100.0 if v <= 1.0 else v
    return round(min(pct, 100.0), 1)


def _stored_percent(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    v = float(raw)
    if v != v or v < 0:
        return None
    return round(min(v, 100.0), 1)


def _epoch(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    v = float(raw)
    if v != v or v <= 0 or v in (float("inf"), float("-inf")):
        return None
    return v / 1000.0 if v > 1e11 else v


class UsageStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def latest(self) -> dict | None:
        try:
            body = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(body, dict) or not isinstance(body.get("windows"), dict):
            return None
        return body

    def _write(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".usage-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(record, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def record(self, info: Any, now: float | None = None) -> dict | None:
        """Guarda una observación. Acepta el `RateLimitInfo` del SDK (via `raw`) o un dict del CLI."""
        raw = getattr(info, "raw", None)
        data: dict = dict(raw) if isinstance(raw, dict) and raw else {}
        if not data and isinstance(info, dict):
            data = dict(info)
        if not data and info is not None:
            data = {"status": getattr(info, "status", ""), "rateLimitType": getattr(info, "rate_limit_type", None),
                    "utilization": getattr(info, "utilization", None), "resetsAt": getattr(info, "resets_at", None)}
        if not data:
            return None
        when = time.time() if now is None else float(now)
        stored = self.latest() or {}
        windows = {k: dict(v) for k, v in (stored.get("windows") or {}).items() if isinstance(v, dict)}
        status = data.get("status") if isinstance(data.get("status"), str) else ""
        named = data.get("rateLimitType") if isinstance(data.get("rateLimitType"), str) else ""
        incoming: dict[str, dict] = {}
        unified = data.get("unifiedWindows")
        if isinstance(unified, dict):
            for key, w in unified.items():
                if isinstance(key, str) and isinstance(w, dict):
                    incoming[key] = {"utilization": _percent(w.get("utilization")), "resets_at": _epoch(w.get("resetsAt")),
                                     "status": (w.get("status") if isinstance(w.get("status"), str) else "") or status,
                                     "observed_at": when}
        if named and named not in incoming:
            incoming[named] = {"utilization": _percent(data.get("utilization")), "resets_at": _epoch(data.get("resetsAt")),
                               "status": status, "observed_at": when}
        windows.update(incoming)
        record = {"observed_at": when, "status": status, "rate_limit_type": named, "windows": windows}
        try:
            self._write(record)
        except OSError:
            pass
        return record

    def snapshot(self, now: float | None = None) -> dict:
        at = time.time() if now is None else float(now)
        stored = self.latest() or {}
        windows = stored.get("windows") if isinstance(stored.get("windows"), dict) else {}
        keys = list(KNOWN_WINDOWS) + [k for k in windows if k not in KNOWN_WINDOWS]
        views = []
        for k in keys:
            w = windows.get(k) if isinstance(windows.get(k), dict) else {}
            observed = _epoch(w.get("observed_at"))
            resets = _epoch(w.get("resets_at"))
            age = None if observed is None else max(0.0, at - observed)
            views.append({"key": k, "label": window_label(k), "utilization": _stored_percent(w.get("utilization")),
                          "resets_at": resets, "status": w.get("status") if isinstance(w.get("status"), str) else "",
                          "observed_at": observed, "age_sec": age, "stale": age is not None and age > STALE_AFTER_SEC,
                          "expired": resets is not None and resets <= at})
        observed_all = [v["observed_at"] for v in views if v["observed_at"] is not None]
        newest = max(observed_all) if observed_all else None
        age = None if newest is None else max(0.0, at - newest)
        return {"measured": newest is not None, "observed_at": newest, "age_sec": age,
                "stale": age is not None and age > STALE_AFTER_SEC, "status": stored.get("status") or "", "windows": views}

    def spoken(self) -> str:
        snap = self.snapshot()
        if not snap["measured"]:
            return "Todavía no tengo una lectura de los límites; el CLI solo la manda durante un turno."
        parts = []
        for w in snap["windows"]:
            if w["utilization"] is None:
                continue
            label = "la sesión de cinco horas" if w["key"] == "five_hour" else ("la semana" if w["key"] == "seven_day" else w["label"].lower())
            parts.append(f"{label} va por el {int(round(w['utilization']))} por ciento")
        if not parts:
            return "Todavía no tengo una lectura de los límites."
        text = " y ".join(parts) + "."
        if snap["stale"]:
            text += " La lectura tiene más de media hora."
        return text[0].upper() + text[1:]

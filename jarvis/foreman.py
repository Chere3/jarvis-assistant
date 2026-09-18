"""El capataz: sesiones de Claude Code, runs, construcciones y documentos, en un solo objeto.

Lo usan las herramientas del modelo (`agent/foreman_tools.py`) y la API del panel nativo (`ui/server.py`).
Aquí no hay política de aprobación (vive en el registro de herramientas) ni voz (vive en `announce.py`).
"""
from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from .announce import URGENT, Announcer
from .runs import builds, specs
from .runs.executor import RunExecutor
from .runs.store import STATUS_LABEL, RunStatus, RunStore
from .sessions import dialog, notify, steer
from .sessions.watch import (GONE, NEEDS_YOU, WORKING, SessionState, SessionWatcher, Snapshot, age_phrase,
                             phrase_needs, session_to_dict)
from .usage import UsageStore

_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class Foreman:
    def __init__(self, cfg: Any, bus: Any, orch: Any, logger: Any = None, provider: Any = None) -> None:
        self.cfg = cfg
        self.bus = bus
        self.orch = orch
        self.log = logger
        self.provider = provider
        rt = cfg.memory.runtime_dir
        self.store = RunStore(rt / "runs.sqlite")
        self.usage = UsageStore(rt / "usage.json")
        r = cfg.runs
        self.executor = RunExecutor(self.store, max_concurrent=r.max_concurrent, default_timeout_sec=r.timeout_s,
                                    idle_sec=r.idle_s, default_model=r.model, skip_permissions=r.skip_permissions, logger=logger)
        self.watcher = SessionWatcher(interval=cfg.sessions.interval_s, exclude_ids=self._own_session_ids, logger=logger)
        self.announcer = Announcer(orch, bus, cfg, self.store, logger, notifier=notify.notify, clients=lambda: self.clients)
        self.clients = 0  # apps/paneles conectados por SSE
        self.last_mentioned: str | None = None
        self._last_states: dict[str, str] = {}
        self._started = False
        self.watcher.on_event(self._on_session_event)
        self.executor.subscribe(self._on_run_event)

    # ------------------------------------------------------------- ciclo de vida
    async def start(self) -> None:
        if self._started:
            return
        swept = await asyncio.to_thread(self.store.sweep_stale_runs)
        if swept and self.log:
            self.log.warning("%d runs quedaron activos tras un reinicio; marcados como fallidos", swept)
        if self.cfg.sessions.watch:
            await self.watcher.start()
        self.announcer.enabled = self.cfg.sessions.announce
        self._started = True

    async def stop(self) -> None:
        await self.watcher.stop()
        self.announcer.close()
        self._started = False

    def _own_session_ids(self) -> set[str]:
        ids = set()
        try:
            ids |= self.store.all_run_ids()
        except Exception:
            pass
        sid = getattr(self.provider, "sdk_session_id", None)
        if sid:
            ids.add(sid)
        return ids

    # ------------------------------------------------------------- eventos
    def _on_session_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        s = event.get("session") or {}
        self.bus.publish("sessions.event", event_kind=kind, session=s)
        name = s.get("voice_name") or "una sesión"
        if kind == "needs_you":
            if s.get("needs"):
                line = f"{name} está {phrase_needs(s.get('needs'))}."
                if s.get("needs_a_human_hand"):
                    line += " Esa necesita una tecla tuya; puedo pulsarla si me lo pides."
            else:
                line = f"{name} se detuvo y te está esperando."
            self._spawn(self.announcer.say(line, URGENT, "needs_you"))
        elif kind == "finished":
            self.announcer.session_finished(name)

    def _on_run_event(self, message: dict[str, Any]) -> None:
        t = message.get("type")
        run = message.get("run") or {}
        if t in ("run_started", "run_updated", "run_finished"):
            self.bus.publish(f"runs.{t}", run=run)
        elif t == "run_event":
            self.bus.publish("runs.event", run_id=message.get("run_id"), seq=message.get("seq"), event_kind=message.get("kind"),
                             summary=message.get("summary", ""))
        if t != "run_finished" or run.get("origin") != "voice":
            return
        project = run.get("project_name") or "el proyecto"
        status = run.get("status")
        if status == RunStatus.SUCCEEDED:
            outcome = self.executor.outcome_of(run["id"])
            if outcome == "stalled":
                self._spawn(self.announcer.say(f"El trabajo en {project} se paró para hacer una pregunta, así que no construyó nada.", URGENT, "run_stalled"))
            elif outcome == "no_changes":
                self._spawn(self.announcer.say(f"El trabajo en {project} terminó, pero no veo que cambiara nada.", URGENT, "run_no_changes"))
            else:
                self.announcer.run_finished(project)
        elif status == RunStatus.TIMED_OUT:
            self._spawn(self.announcer.say(f"El trabajo en {project} se quedó sin tiempo.", URGENT, "run_timed_out"))
        elif status == RunStatus.FAILED:
            self._spawn(self.announcer.say(f"El trabajo en {project} falló.", URGENT, "run_failed"))

    def _spawn(self, coro: Any) -> None:
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()

    def poll_sessions_changed(self) -> bool:
        """Para el panel: True si cambió el conjunto (id, estado) desde la última vez."""
        snap = self.watcher.snapshot
        states = {s.session_id: s.state for s in snap.sessions}
        changed = states != self._last_states
        self._last_states = states
        return changed

    # ------------------------------------------------------------- sesiones
    @property
    def snapshot(self) -> Snapshot:
        return self.watcher.snapshot

    def sessions_payload(self) -> dict[str, Any]:
        snap = self.snapshot
        return {"taken_at": snap.taken_at, "sessions": [session_to_dict(s) for s in snap.sessions],
                "needs_you": [s.session_id for s in snap.needing_you()], "watching": self.watcher._task is not None}

    def resolve(self, reference: str) -> list[SessionState]:
        return self.snapshot.resolve(reference, self.last_mentioned)

    def session_line(self, s: SessionState, now: float | None = None) -> str:
        """Una línea hablable por conversación."""
        now = now or time.time()
        age = age_phrase(now - s.since) if s.since else "un rato"
        if s.state == NEEDS_YOU:
            what = phrase_needs(s.needs) if s.needs else "esperándote"
            line = f"{s.voice_name}: {what} desde hace {age}"
            if s.needs_a_human_hand:
                line += " (necesita una tecla tuya)"
        elif s.state == WORKING:
            line = f"{s.voice_name}: trabajando desde hace {age}"
        elif s.state == GONE:
            line = f"{s.voice_name}: terminada"
        else:
            from .sessions.watch import STATE_LABEL
            line = f"{s.voice_name}: {STATE_LABEL.get(s.state, s.state)} desde hace {age}"
        if s.summary():
            line += f" — {s.summary()[:100]}"
        return line

    def sessions_summary_lines(self) -> list[str]:
        snap = self.snapshot
        live = [s for s in snap.sessions if s.state not in (GONE, "fresh")]
        return [self.session_line(s) for s in live]

    async def steer_session(self, s: SessionState, text: str) -> str:
        if not s.steerable:
            outcome = steer.NOT_LIVE
        else:
            outcome = await asyncio.to_thread(steer.post_to_session, s.socket_path, text)
        await asyncio.to_thread(self.store.record_steer, s.session_id, s.voice_name, s.project, text, outcome)
        self.last_mentioned = s.session_id
        return outcome

    async def answer_dialog(self, s: SessionState, key: str) -> str:
        pid = s.primary_pid or (s.pids[0] if s.pids else None)
        self.last_mentioned = s.session_id
        return await dialog.press_key(pid, key)

    def transcript_tail(self, s: SessionState, max_items: int = 30) -> list[dict[str, Any]]:
        from .sessions.watch import config_roots, tail_objects, transcript_path
        out: list[dict[str, Any]] = []
        for root in (self.watcher.roots or config_roots()):
            p = transcript_path(root, s.cwd, s.session_id)
            if not p.exists():
                continue
            for o in tail_objects(p, 200 * 1024):
                t = o.get("type")
                msg = o.get("message") if isinstance(o.get("message"), dict) else None
                if t in ("user", "assistant") and msg and not o.get("isSidechain"):
                    content = msg.get("content")
                    texts, tools = [], []
                    if isinstance(content, str):
                        texts.append(content)
                    elif isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                                texts.append(str(b["text"]))
                            elif isinstance(b, dict) and b.get("type") == "tool_use":
                                tools.append(str(b.get("name") or "tool"))
                            elif isinstance(b, dict) and b.get("type") == "tool_result":
                                tools.append("resultado")
                    text = " ".join(" ".join(texts).split())[:1200]
                    if text or tools:
                        out.append({"role": t, "text": text, "tools": tools, "ts": o.get("timestamp")})
            break
        return out[-max_items:]

    # ------------------------------------------------------------- proyectos
    def projects_root(self) -> Path:
        return Path(self.cfg.runs.projects_root).expanduser()

    def resolve_project(self, reference: str) -> Path | None:
        """Carpeta de proyecto a partir de un nombre o ruta. Busca en la raíz de proyectos, en el roster y en los runs."""
        ref = (reference or "").strip()
        if not ref:
            return None
        p = Path(ref).expanduser()
        if p.is_absolute() and p.is_dir():
            return p
        root = self.projects_root()
        cand = root / ref
        if cand.is_dir():
            return cand
        low = ref.lower()
        for d in sorted(root.iterdir()) if root.is_dir() else []:
            if d.is_dir() and d.name.lower() == low:
                return d
        for s in self.snapshot.sessions:
            if s.project.lower() == low and Path(s.cwd).is_dir():
                return Path(s.cwd)
        for r in self.store.projects():
            if (r["project_name"] or "").lower() == low and Path(r["project_path"]).is_dir():
                return Path(r["project_path"])
        for d in sorted(root.iterdir()) if root.is_dir() else []:
            if d.is_dir() and low in d.name.lower():
                return d
        return None

    def projects_payload(self) -> list[dict[str, Any]]:
        by_path: dict[str, dict[str, Any]] = {}

        def entry(path: Path, name: str | None = None) -> dict[str, Any]:
            key = str(path)
            if key not in by_path:
                by_path[key] = {"name": name or path.name, "path": key, "sessions": 0, "needs_you": 0, "working": 0,
                                "runs": 0, "active_runs": 0, "last_run": None, "review": None, "exists": path.is_dir()}
            return by_path[key]

        root = self.projects_root()
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if d.is_dir() and not d.name.startswith("."):
                    entry(d)
        for s in self.snapshot.sessions:
            if s.state in (GONE, "fresh"):
                continue
            e = entry(Path(s.cwd), s.project)
            e["sessions"] += 1
            e["needs_you"] += int(s.state == NEEDS_YOU)
            e["working"] += int(s.state == WORKING)
        for r in self.store.projects():
            e = entry(Path(r["project_path"]), r["project_name"])
            e["runs"] = r["n"]
            e["active_runs"] = r["active"]
            e["last_run"] = r["last"]
        for e in by_path.values():
            try:
                rev = specs.project_review(e["path"])
                e["review"] = rev["state"] if rev else None
            except Exception:
                e["review"] = None
        items = list(by_path.values())
        items.sort(key=lambda e: (-(e["needs_you"] + e["working"] + e["active_runs"]), -(e["last_run"] or 0), e["name"].lower()))
        return items

    def create_project(self, name: str) -> Path:
        if not _PROJECT_NAME_RE.match(name):
            raise ValueError("nombre de proyecto inválido (letras, números, punto, guion)")
        root = self.projects_root()
        root.mkdir(parents=True, exist_ok=True)
        target = (root / name).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ValueError("ruta fuera de la carpeta de proyectos")
        if target.exists():
            raise FileExistsError(f"ya existe {target}")
        target.mkdir()
        (target / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=target, check=False, capture_output=True, timeout=10)
        return target

    # ------------------------------------------------------------- runs
    async def spawn_run(self, prompt: str, project: Path, origin: str = "voice", model: str | None = None,
                        kind: str = "run", timeout_s: float = 0) -> str:
        return await self.executor.spawn(prompt, project.name, str(project), origin, model=model, kind=kind, timeout_sec=timeout_s)

    def run_line(self, run: dict[str, Any]) -> str:
        label = STATUS_LABEL.get(run.get("status"), run.get("status"))
        gist = builds.gist_of_build(run.get("prompt") or "") if builds.is_build_prompt(run.get("prompt") or "") else (run.get("prompt") or "")[:80]
        line = f"{run.get('project_name')}: {label} — {gist}"
        if run.get("status") == RunStatus.RUNNING and run.get("summary"):
            line += f" (ahora: {run['summary'][:80]})"
        if run.get("error"):
            line += f" · error: {run['error'][:120]}"
        return line

    def active_runs(self) -> list[dict[str, Any]]:
        return self.store.list_runs(status=[RunStatus.QUEUED, RunStatus.RUNNING], limit=20)

    def find_run(self, reference: str) -> list[dict[str, Any]]:
        ref = (reference or "").strip().lower()
        runs = self.store.list_runs(limit=50)
        if not ref:
            return [r for r in runs if r["status"] in RunStatus.ACTIVE] or runs[:1]
        by_id = [r for r in runs if r["id"].startswith(ref)]
        if by_id:
            return by_id
        return [r for r in runs if ref in (r["project_name"] or "").lower()]

    # ------------------------------------------------------------- construcciones
    def write_spec(self, project: Path, spec: str, constraints: str = "", non_goals: str = "") -> str:
        return builds.write_spec(str(project), spec, constraints, non_goals, assistant=self.cfg.assistant.display_name)

    async def start_build(self, project: Path, spec_relative: str, model: str | None = None) -> str:
        brief = builds.compose_build_brief(spec_relative)
        specs.record_approval(str(project), spec_relative, by="voz")
        return await self.spawn_run(brief, project, origin="voice", model=model, kind="build")

    def build_status(self, project: Path) -> dict[str, Any]:
        progress = builds.plan_progress(str(project))
        runs = [r for r in self.store.list_runs(project=project.name, limit=10) if builds.is_build_prompt(r["prompt"])]
        return {"progress": progress.to_dict() if progress else None, "run": runs[0] if runs else None}

    def build_status_line(self, project: Path) -> str:
        st = self.build_status(project)
        run, prog = st["run"], st["progress"]
        if not run and not prog:
            return f"No hay ninguna construcción en {project.name}."
        parts = []
        if run:
            parts.append(f"la sesión está {STATUS_LABEL.get(run['status'], run['status'])}")
        if prog:
            cur = f", ahora en «{prog['current']['title']}»" if prog.get("current") else ""
            parts.append(f"el plan lleva {prog['done']} de {prog['total']} tareas{cur}")
        else:
            parts.append("todavía está escribiendo el plan")
        return f"En {project.name}, " + " y ".join(parts) + "."

    # ------------------------------------------------------------- terminal
    async def open_in_terminal(self, project: Path, command: str | None = None) -> str:
        script = f'tell application "Terminal"\n activate\n do script "cd {shlex.quote(str(project))}'
        if command:
            script += f" && {command}"
        script += '"\nend tell'
        proc = await asyncio.create_subprocess_exec("osascript", "-e", script, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            return "Terminal no respondió a tiempo."
        return "ok" if proc.returncode == 0 else f"Terminal falló: {err.decode(errors='replace')[:200]}"

    # ------------------------------------------------------------- contexto para el modelo
    def context_block(self) -> str:
        lines = []
        snap = self.snapshot
        needs = snap.needing_you()
        live = [s for s in snap.sessions if s.state not in (GONE, "fresh")]
        if live:
            lines.append(f"Sesiones de Claude Code vivas: {len(live)}; te esperan: {len(needs)}"
                         + (" (" + ", ".join(s.voice_name for s in needs[:4]) + ")" if needs else "") + ".")
        active = self.active_runs()
        if active:
            lines.append("Runs activos: " + "; ".join(self.run_line(r) for r in active[:4]) + ".")
        recent = self.announcer.recent_lines(5)
        if recent:
            lines.append("Avisos que ya diste en voz alta (no los repitas):\n" + "\n".join(recent))
        if not lines:
            return ""
        return "<contexto_operativo>\n" + "\n".join(lines) + "\n</contexto_operativo>"

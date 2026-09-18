"""Vigila todas las sesiones de Claude Code de esta máquina.

Tres niveles: un *proceso* es un `claude`; una *conversación* es un `sessionId` con su transcript;
un *proyecto* es un `cwd`. Lo que se cuenta y se nombra en voz alta son conversaciones.

Solo lectura del sistema de archivos: sin asyncio en la parte pura, sin modelo, sin servidor.
Los archivos los escribe otro proceso mientras se leen, así que cada parse tolera fallos.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

DEFAULT_ROOTS = ("~/.claude",)


def config_roots() -> list[Path]:
    roots = [Path(r).expanduser() for r in DEFAULT_ROOTS]
    for raw in os.getenv("JARVIS_CLAUDE_CONFIG_DIRS", "").split(os.pathsep):
        raw = raw.strip()
        if raw:
            p = Path(raw).expanduser()
            if p not in roots:
                roots.append(p)
    return roots


def pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def encode_cwd(cwd: str) -> str:
    """Nombre del directorio de transcripts del CLI: todo lo no alfanumérico pasa a `-`."""
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd)


def _ms(value: Any) -> float | None:
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class RosterEntry:
    """Un `sessions/<pid>.json`: un proceso `claude` vivo."""
    pid: int
    session_id: str
    cwd: str
    name: str
    root: Path
    kind: str = "interactive"
    entrypoint: str = "cli"
    status: str | None = None
    waiting_for: str | None = None
    started_at: float | None = None
    status_updated_at: float | None = None
    socket_path: str | None = None
    version: str = ""

    @property
    def steerable(self) -> bool:
        return bool(self.socket_path) and Path(self.socket_path).exists()


def _parse_entry(path: Path, root: Path) -> RosterEntry | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        pid = int(data["pid"])
        session_id = str(data["sessionId"])
        cwd = str(data["cwd"])
    except (KeyError, TypeError, ValueError):
        return None
    wf = data.get("waitingFor")
    return RosterEntry(
        pid=pid, session_id=session_id, cwd=cwd,
        name=str(data.get("name") or Path(cwd).name or session_id[:8]), root=root,
        kind=str(data.get("kind") or "interactive"), entrypoint=str(data.get("entrypoint") or "cli"),
        status=data.get("status") if isinstance(data.get("status"), str) else None,
        waiting_for=wf if isinstance(wf, str) and wf else None,
        started_at=_ms(data.get("startedAt")), status_updated_at=_ms(data.get("statusUpdatedAt")),
        socket_path=str(data["messagingSocketPath"]) if isinstance(data.get("messagingSocketPath"), str) else None,
        version=str(data.get("version") or ""))


def read_roster(roots: list[Path] | None = None) -> list[RosterEntry]:
    entries: list[RosterEntry] = []
    seen: set[tuple[int, str]] = set()
    for root in (roots if roots is not None else config_roots()):
        d = Path(root) / "sessions"
        try:
            files = sorted(d.glob("*.json"))
        except OSError:
            continue
        for f in files:
            e = _parse_entry(f, Path(root))
            if e is None or (e.pid, e.session_id) in seen:
                continue
            seen.add((e.pid, e.session_id))
            entries.append(e)
    return entries


# ------------------------------------------------------------------ transcript
TAIL_BYTES = 64 * 1024
MAX_RECENT_TOOLS = 5
MAX_TEXT = 600


@dataclass
class Recap:
    exists: bool = False
    title: str | None = None
    last_prompt: str | None = None
    last_text: str | None = None
    recent_tools: list[str] = field(default_factory=list)

    def summary(self) -> str | None:
        return self.title or self.last_prompt


def transcript_path(root: Path, cwd: str, session_id: str) -> Path:
    return Path(root) / "projects" / encode_cwd(cwd) / f"{session_id}.jsonl"


def tail_objects(path: Path, nbytes: int = TAIL_BYTES) -> list[dict]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size > nbytes:
                fh.seek(size - nbytes)
                fh.readline()
            else:
                fh.seek(0)
            raw = fh.read()
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _clip(text: Any, limit: int = MAX_TEXT) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def recap_from(objs: list[dict]) -> Recap:
    r = Recap(exists=True)
    tools: list[str] = []
    for o in objs:
        kind = o.get("type")
        if kind == "ai-title":
            r.title = _clip(o.get("aiTitle"), 200) or r.title
        elif kind == "last-prompt":
            r.last_prompt = _clip(o.get("lastPrompt")) or r.last_prompt
        elif kind == "assistant" and not o.get("isSidechain"):
            message = o.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    r.last_text = _clip(block.get("text")) or r.last_text
                elif block.get("type") == "tool_use" and block.get("name"):
                    tools.append(_clip(block["name"]) or "")
    r.recent_tools = tools[-MAX_RECENT_TOOLS:]
    return r


def read_recap(root: Path, cwd: str, session_id: str, nbytes: int = TAIL_BYTES) -> Recap:
    path = transcript_path(root, cwd, session_id)
    if not path.exists():
        return Recap(exists=False)
    return recap_from(tail_objects(path, nbytes))


def _first_recap(roots: list[Path], cwd: str, session_id: str) -> Recap:
    for root in roots:
        recap = read_recap(root, cwd, session_id)
        if recap.exists:
            return recap
    return Recap(exists=False)


# ------------------------------------------------------------------ estados
FRESH = "fresh"          # viva pero nunca usada (sin transcript): nunca se anuncia
NEEDS_YOU = "needs_you"
WORKING = "working"
SHELL = "shell"
IDLE = "idle"
GONE = "gone"
UNKNOWN = "unknown"

STATE_LABEL = {FRESH: "sin empezar", NEEDS_YOU: "te necesita", WORKING: "trabajando", SHELL: "en una shell",
               IDLE: "en espera", GONE: "terminada", UNKNOWN: "estado desconocido"}

HUMAN_HAND_REASONS = ("permission", "dialog", "trust", "login", "auth", "permiso", "diálogo")
_QUESTION_TOOLS = ("AskUserQuestion", "ExitPlanMode")
_URL_RE = re.compile(r"\S+://\S+|\bwww\.\S+")


def _looks_like_a_question(text: str | None) -> bool:
    if not text:
        return False
    cleaned = _URL_RE.sub(" ", text).rstrip()
    if not cleaned:
        return False
    if cleaned.endswith("?"):
        return True
    return any(line.rstrip().endswith("?") for line in cleaned.splitlines()[-6:])


def phrase_needs(needs: str | None) -> str:
    """El motivo de espera del CLI, dicho en español."""
    n = (needs or "").lower()
    if "permission" in n:
        return "esperando un permiso"
    if "dialog" in n:
        return "con un diálogo abierto"
    if "input" in n:
        return "esperando una respuesta tuya"
    if "trust" in n:
        return "pidiendo confiar en la carpeta"
    if "login" in n or "auth" in n:
        return "pidiendo iniciar sesión"
    return f"esperándote ({needs})" if needs else "esperándote"


@dataclass
class SessionState:
    session_id: str
    cwd: str
    project: str
    state: str
    pids: list[int] = field(default_factory=list)
    primary_pid: int | None = None
    roster_name: str = ""
    voice_name: str = ""
    needs: str | None = None
    title: str | None = None
    last_prompt: str | None = None
    last_text: str | None = None
    recent_tools: list[str] = field(default_factory=list)
    started: float | None = None   # cuándo empezó la conversación
    since: float | None = None     # cuándo empezó el estado actual (para «lleva una hora esperando»)
    origin: str = "terminal"
    steerable: bool = False
    socket_path: str | None = None
    primary: bool = False
    agents_seen: int = 0
    agents_active: int = 0

    @property
    def announceable(self) -> bool:
        return self.state != FRESH

    @property
    def needs_a_human_hand(self) -> bool:
        reason = (self.needs or "").lower()
        return any(w in reason for w in HUMAN_HAND_REASONS)

    def summary(self) -> str | None:
        return self.title or self.last_prompt


PRIMARY_MARGIN_SEC = 120.0


def _mark_primary(sessions: list[SessionState]) -> None:
    by_project: dict[str, list[SessionState]] = {}
    for s in sessions:
        s.primary = False
        if s.state in (GONE, FRESH) or s.origin != "terminal":
            continue
        by_project.setdefault(s.project, []).append(s)
    for group in by_project.values():
        if len(group) == 1:
            group[0].primary = True
            continue
        ranked = sorted(group, key=lambda s: s.since if s.since is not None else float("-inf"), reverse=True)
        lead, runner = ranked[0], ranked[1]
        if lead.since is not None and (runner.since is None or lead.since - runner.since > PRIMARY_MARGIN_SEC):
            lead.primary = True


AGENT_ACTIVE_WITHIN_SEC = 90.0
MAX_AGENT_FILES = 300


def count_agents(roots: list[Path], cwd: str, session_id: str, now: float) -> tuple[int, int]:
    by_name: dict[str, Path] = {}
    for root in roots:
        d = Path(root) / "projects" / encode_cwd(cwd) / session_id / "subagents"
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for f in entries:
            if f.name.endswith(".jsonl"):
                by_name.setdefault(f.name, f)
    seen = active = 0
    for f in [by_name[n] for n in sorted(by_name)][:MAX_AGENT_FILES]:
        try:
            st = f.stat()
        except OSError:
            continue
        seen += 1
        if now - st.st_mtime <= AGENT_ACTIVE_WITHIN_SEC:
            active += 1
    return seen, active


def project_name(cwd: str) -> str:
    parts = Path(cwd).parts
    if ".claude" in parts:
        i = parts.index(".claude")
        if i > 0 and i + 1 < len(parts) and parts[i + 1] == "worktrees":
            return parts[i - 1]
    return Path(cwd).name or cwd


_ORIGIN_BY_ENTRYPOINT = {"cli": "terminal", "sdk-cli": "background", "sdk-py": "background",
                         "sdk-ts": "background", "claude-desktop": "desktop", "desktop": "desktop"}


def _origin(entry: RosterEntry) -> str:
    return _ORIGIN_BY_ENTRYPOINT.get(entry.entrypoint, "terminal")


_ppid_cache: dict[int, int | None] = {}


def parent_pid(pid: int) -> int | None:
    """ppid de un proceso (cacheado: el ppid no cambia mientras el proceso vive)."""
    if pid in _ppid_cache:
        return _ppid_cache[pid]
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=1.0)
        val = int(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        val = None
    _ppid_cache[pid] = val
    if len(_ppid_cache) > 5000:
        _ppid_cache.clear()
    return val


def _is_own_child(entry: RosterEntry, own_pid: int) -> bool:
    """El cerebro del asistente (CLI lanzado por el Agent SDK) y sus runs son hijos de este proceso."""
    if entry.entrypoint == "cli":
        return False
    return parent_pid(entry.pid) == own_pid


def _pick_primary(entries: list[RosterEntry]) -> RosterEntry:
    return sorted(entries, key=lambda e: (pid_alive(e.pid), e.steerable, e.status_updated_at or 0.0), reverse=True)[0]


def _derive_state(entries: list[RosterEntry], recap: Recap) -> tuple[str, str | None]:
    if not any(pid_alive(e.pid) for e in entries):
        return GONE, None
    if not recap.exists:
        return FRESH, None
    live = [e for e in entries if pid_alive(e.pid)]
    waiting = next((e for e in live if e.waiting_for), None)
    if waiting is not None:
        return NEEDS_YOU, waiting.waiting_for
    if any(e.status == "waiting" for e in live):
        return NEEDS_YOU, None
    if any(e.status == "busy" for e in live):
        return WORKING, None
    if any(e.status == "shell" for e in live):
        return SHELL, None
    if all(e.status is None for e in live):
        return UNKNOWN, None
    if any(t in _QUESTION_TOOLS for t in recap.recent_tools) or _looks_like_a_question(recap.last_text):
        return NEEDS_YOU, None
    return IDLE, None


# ------------------------------------------------------------------ nombres hablables
_TITLE_STOPWORDS = frozenset({"a", "an", "the", "and", "or", "to", "for", "of", "in", "on", "with", "at", "by", "from",
                              "is", "are", "this", "that", "it", "its", "your", "my", "our", "el", "la", "los", "las",
                              "de", "del", "y", "o", "en", "con", "para", "por", "un", "una", "que", "se", "al"})


def _topic_phrase(title: str, project: str) -> str | None:
    tokens = [w for w in re.split(r"[^a-z0-9áéíóúñ]+", title.lower()) if w]
    proj = {w for w in re.split(r"[^a-z0-9áéíóúñ]+", project.lower()) if w}
    kept = [w for w in tokens if w not in _TITLE_STOPWORDS and w not in proj]
    return " ".join(kept[-2:]) if kept else None


_STATE_PHRASES = {NEEDS_YOU: "que te necesita", WORKING: "que está trabajando", IDLE: "que está en espera",
                  SHELL: "en una shell", GONE: "que terminó", FRESH: "sin empezar", UNKNOWN: "en estado desconocido"}


def _name_group(group: list[SessionState], base: str) -> None:
    phrases: dict[str, str] = {}
    for s in group:
        p = _topic_phrase((s.title or "").strip(), s.project) if s.title else None
        if not p:
            phrases = {}
            break
        phrases[s.session_id] = p
    if phrases and len(set(phrases.values())) == len(phrases):
        for s in group:
            s.voice_name = f"{base}, la de {phrases[s.session_id]}"
        return
    states = [s.state for s in group]
    if len(set(states)) == len(states):
        for s in group:
            s.voice_name = f"la {base} {_STATE_PHRASES.get(s.state, 'en estado desconocido')}"
        return
    ordered = sorted(group, key=lambda s: s.started or 0.0, reverse=True)
    if len(ordered) == 2:
        ordered[0].voice_name = f"la {base} más nueva"
        ordered[1].voice_name = f"la {base} más antigua"
        return
    words = ("más nueva", "segunda", "tercera", "cuarta", "quinta")
    for i, s in enumerate(ordered):
        s.voice_name = f"la {base} {words[i]}" if i < len(words) else f"{base} número {i + 1}"


def _assign_voice_names(sessions: list[SessionState]) -> None:
    by_project: dict[str, list[SessionState]] = {}
    for s in sessions:
        by_project.setdefault(s.project, []).append(s)
    for project, group in by_project.items():
        if len(group) == 1:
            group[0].voice_name = project
            continue
        by_parent: dict[str, list[SessionState]] = {}
        for s in group:
            by_parent.setdefault(Path(s.cwd).parent.name, []).append(s)
        if len(by_parent) > 1:
            for parent, sub in by_parent.items():
                base = f"{project} en {parent}"
                if len(sub) == 1:
                    sub[0].voice_name = base
                else:
                    _name_group(sub, base)
        else:
            _name_group(group, project)


_FILLER = frozenset({"la", "el", "una", "un", "sesión", "sesion", "sesiones", "conversación", "conversacion", "proyecto",
                     "en", "de", "del", "que", "esa", "ese", "esta", "este", "por", "favor", "the", "a", "one", "session"})


def _name_words(s: SessionState) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9áéíóúñ\-]+", f"{s.voice_name} {s.project}".lower()) if w}


def _prefer_real(matches: list[SessionState]) -> list[SessionState]:
    real = [s for s in matches if s.state != FRESH]
    return real if real else matches


@dataclass
class Snapshot:
    sessions: list[SessionState] = field(default_factory=list)
    taken_at: float = 0.0

    def by_id(self, session_id: str) -> SessionState | None:
        return next((s for s in self.sessions if s.session_id == session_id), None)

    def by_project(self) -> dict[str, list[SessionState]]:
        out: dict[str, list[SessionState]] = {}
        for s in self.sessions:
            out.setdefault(s.project, []).append(s)
        return out

    def needing_you(self) -> list[SessionState]:
        return sorted((s for s in self.sessions if s.state == NEEDS_YOU),
                      key=lambda s: s.since if s.since is not None else float("-inf"), reverse=True)

    def resolve(self, reference: str, last_mentioned: str | None = None) -> list[SessionState]:
        """Traduce lo que dijo el usuario a conversaciones. Devuelve TODAS las candidatas si es ambiguo."""
        ref = " ".join((reference or "").lower().split())
        if not ref:
            return []
        if ref in ("esa", "esa misma", "la misma", "esa sesión", "esa sesion", "la de antes", "esa conversación"):
            s = self.by_id(last_mentioned) if last_mentioned else None
            return [s] if s else []
        exact = [s for s in self.sessions if s.voice_name.lower() == ref]
        if exact:
            return exact
        sid = [s for s in self.sessions if s.session_id == reference or s.session_id.startswith(reference)]
        if sid and len(reference) >= 6:
            return sid
        roster = [s for s in self.sessions if s.roster_name.lower() == ref]
        if roster:
            return roster
        exact_project = [s for s in self.sessions if s.project.lower() == ref]
        if exact_project:
            return _prefer_real(exact_project)
        ref_words = {w for w in re.split(r"[^a-z0-9áéíóúñ\-]+", ref) if w and w not in _FILLER}
        if ref_words:
            hits = [s for s in self.sessions if ref_words <= _name_words(s)]
            if hits:
                narrowed = _prefer_real(hits)
                if len(narrowed) < len(self.sessions):
                    return narrowed
        loose = [s for s in self.sessions if s.voice_name.lower() in ref or s.project.lower() in ref or ref in s.voice_name.lower()]
        return _prefer_real(loose)


def build_snapshot(entries: list[RosterEntry] | None = None, roots: list[Path] | None = None,
                   now: float | None = None, exclude_ids: set[str] | None = None,
                   own_pid: int | None = None) -> Snapshot:
    if entries is None:
        entries = read_roster(roots)
    own_pid = own_pid if own_pid is not None else os.getpid()
    exclude_ids = exclude_ids or set()
    entries = [e for e in entries if e.session_id not in exclude_ids and not _is_own_child(e, own_pid)]
    grouped: dict[str, list[RosterEntry]] = {}
    for e in entries:
        grouped.setdefault(e.session_id, []).append(e)
    at = now if now is not None else time.time()
    sessions: list[SessionState] = []
    for session_id, group in grouped.items():
        primary = _pick_primary(group)
        roots_here = [primary.root] + [e.root for e in group if e.root != primary.root]
        recap = _first_recap(roots_here, primary.cwd, session_id)
        state, needs = _derive_state(group, recap)
        seen, active = count_agents(roots_here, primary.cwd, session_id, at)
        live = [e for e in group if pid_alive(e.pid)]
        steerable = next((e for e in live if e.steerable), None)
        sessions.append(SessionState(
            session_id=session_id, cwd=primary.cwd, project=project_name(primary.cwd), state=state,
            pids=[e.pid for e in group], primary_pid=primary.pid, roster_name=primary.name, needs=needs,
            title=recap.title, last_prompt=recap.last_prompt, last_text=recap.last_text, recent_tools=recap.recent_tools,
            started=primary.started_at or primary.status_updated_at, since=primary.status_updated_at or primary.started_at,
            origin=_origin(primary), steerable=steerable is not None, socket_path=steerable.socket_path if steerable else None,
            agents_seen=seen, agents_active=active))
    _assign_voice_names(sessions)
    _mark_primary(sessions)
    sessions.sort(key=lambda s: (s.project, s.voice_name))
    return Snapshot(sessions=sessions, taken_at=at)


def session_to_dict(s: SessionState) -> dict[str, Any]:
    return {"session_id": s.session_id, "voice_name": s.voice_name, "roster_name": s.roster_name, "project": s.project,
            "cwd": s.cwd, "state": s.state, "state_label": STATE_LABEL.get(s.state, s.state), "needs": s.needs,
            "needs_label": phrase_needs(s.needs) if s.state == NEEDS_YOU else None,
            "needs_a_human_hand": s.needs_a_human_hand, "title": s.title, "summary": s.summary(),
            "last_prompt": s.last_prompt, "last_text": s.last_text, "recent_tools": list(s.recent_tools),
            "started": s.started, "since": s.since, "origin": s.origin, "steerable": s.steerable, "pids": list(s.pids),
            "primary_pid": s.primary_pid, "primary": s.primary, "agents_seen": s.agents_seen, "agents_active": s.agents_active}


# ------------------------------------------------------------------ vigilante
GONE_RETENTION_SEC = 600.0
MIN_WORK_SEC = 30.0


class SessionWatcher:
    """Sondea el roster y publica solo las transiciones que merecen decirse.

    El primer sondeo no emite nada: al arrancar no se recitan las sesiones que ya estaban abiertas.
    """

    def __init__(self, roots: list[Path] | None = None, interval: float = 1.0,
                 exclude_ids: Callable[[], set[str]] | None = None, logger: Any = None) -> None:
        self.roots = roots
        self.interval = interval
        self.exclude_ids = exclude_ids or (lambda: set())
        self.log = logger
        self.snapshot = Snapshot()
        self._previous: dict[str, str] = {}
        self._working_since: dict[str, float] = {}
        self._gone_at: dict[str, float] = {}
        self._gone_cache: dict[str, SessionState] = {}
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._started = False
        self._task = None
        self._event_loop = None
        self._loop_thread = None

    def on_event(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers.append(callback)

    def _publish(self, kind: str, session: SessionState, at: float) -> None:
        event = {"kind": kind, "at": at, "session": session_to_dict(session)}

        def invoke(cb):
            try:
                cb(event)
            except Exception:
                if self.log:
                    self.log.warning("suscriptor de sesiones falló", exc_info=True)

        loop = self._event_loop
        on_loop = loop is not None and threading.current_thread() is self._loop_thread
        for cb in list(self._subscribers):
            if loop is not None and not on_loop:
                loop.call_soon_threadsafe(invoke, cb)
            else:
                invoke(cb)

    def poll_once(self, now: float | None = None) -> Snapshot:
        now = now if now is not None else time.time()
        snap = build_snapshot(roots=self.roots, now=now, exclude_ids=self.exclude_ids())
        present = {s.session_id for s in snap.sessions}
        for sid, cached in list(self._gone_cache.items()):
            if sid in present:
                del self._gone_cache[sid]
                self._gone_at.pop(sid, None)
                continue
            if now - self._gone_at.get(sid, now) > GONE_RETENTION_SEC:
                del self._gone_cache[sid]
                self._gone_at.pop(sid, None)
                self._previous.pop(sid, None)
                self._working_since.pop(sid, None)
            else:
                snap.sessions.append(cached)
        for sid in list(self._previous):
            if sid not in present and sid not in self._gone_cache:
                prior = self.snapshot.by_id(sid)
                if prior is not None:
                    gone = SessionState(**{**prior.__dict__, "state": GONE, "steerable": False, "socket_path": None})
                    self._gone_cache[sid] = gone
                    self._gone_at[sid] = now
                    snap.sessions.append(gone)
        _assign_voice_names(snap.sessions)
        _mark_primary(snap.sessions)
        snap.sessions.sort(key=lambda s: (s.project, s.voice_name))
        first = not self._started
        self.snapshot = snap
        self._started = True
        for s in snap.sessions:
            was = self._previous.get(s.session_id)
            if s.state == WORKING and was != WORKING:
                self._working_since[s.session_id] = now
            if not first and s.announceable:
                if s.state == NEEDS_YOU and was != NEEDS_YOU:
                    self._publish("needs_you", s, now)
                elif was == WORKING and s.state in (IDLE, GONE, UNKNOWN):
                    started = self._working_since.get(s.session_id)
                    if started is not None and now - started >= MIN_WORK_SEC:
                        self._publish("finished", s, now)
            self._previous[s.session_id] = s.state
        return snap

    async def start(self) -> None:
        import asyncio
        if self._task is not None:
            return
        self._event_loop = asyncio.get_running_loop()
        self._loop_thread = threading.current_thread()
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        import asyncio
        while True:
            try:
                await asyncio.to_thread(self.poll_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                if self.log:
                    self.log.warning("sondeo de sesiones falló", exc_info=True)
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        import asyncio
        task, self._task = self._task, None
        self._event_loop = None
        self._loop_thread = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def age_phrase(seconds: float | None) -> str:
    """«unos veinte segundos», «unos cinco minutos», «cerca de una hora», «unas tres horas»."""
    if seconds is None or seconds < 0:
        return "un rato"
    if seconds < 60:
        return "menos de un minuto"
    m = int(seconds // 60)
    if m < 60:
        return "un minuto" if m == 1 else f"unos {m} minutos"
    h = int(seconds // 3600)
    if h < 24:
        return "cerca de una hora" if h == 1 else f"unas {h} horas"
    d = int(seconds // 86400)
    return "un día" if d == 1 else f"{d} días"

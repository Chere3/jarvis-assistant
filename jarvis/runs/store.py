"""Única fuente de verdad de los runs. Toda transición se persiste ANTES de anunciarse."""
from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any


class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    ALL = (QUEUED, RUNNING, SUCCEEDED, FAILED, TIMED_OUT, CANCELLED)
    TERMINAL = frozenset({SUCCEEDED, FAILED, TIMED_OUT, CANCELLED})
    ACTIVE = frozenset({QUEUED, RUNNING})


STATUS_LABEL = {"queued": "en cola", "running": "en marcha", "succeeded": "terminado", "failed": "falló",
                "timed_out": "se agotó el tiempo", "cancelled": "cancelado"}

_UPDATABLE = frozenset({"status", "result_text", "summary", "error", "exit_code", "pid", "cost_usd", "input_tokens",
                        "output_tokens", "cache_read_tokens", "cache_creation_tokens", "num_turns", "model",
                        "requested_model", "started_at", "ended_at", "project_path", "project_name", "is_error", "kind"})


class RunStore:
    RunStatus = RunStatus

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, project_name TEXT NOT NULL, project_path TEXT NOT NULL,
                    prompt TEXT NOT NULL, origin TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'run',
                    status TEXT NOT NULL, resume_from TEXT,
                    result_text TEXT DEFAULT '', summary TEXT DEFAULT '', error TEXT DEFAULT '',
                    exit_code INTEGER, pid INTEGER, cost_usd REAL DEFAULT 0,
                    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0, cache_creation_tokens INTEGER DEFAULT 0,
                    num_turns INTEGER DEFAULT 0, model TEXT DEFAULT '', requested_model TEXT DEFAULT '',
                    is_error INTEGER DEFAULT 0, created_at REAL NOT NULL, started_at REAL, ended_at REAL);
                CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
                CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                    seq INTEGER NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_events_run_seq ON run_events(run_id, seq);
                CREATE TABLE IF NOT EXISTS steers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, voice_name TEXT NOT NULL,
                    project TEXT NOT NULL, prompt TEXT NOT NULL, outcome TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS announcements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, priority TEXT NOT NULL,
                    text TEXT NOT NULL, channel TEXT NOT NULL, kind TEXT NOT NULL DEFAULT '');
            """)
            conn.commit()

    # ------------------------------------------------------------- runs
    def create_run(self, prompt: str, project_name: str, project_path: str, origin: str,
                   resume_from: str | None = None, kind: str = "run") -> str:
        run_id = str(uuid.uuid4())
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO runs (id, project_name, project_path, prompt, origin, kind, status, resume_from, created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)",
                         (run_id, project_name, project_path, prompt, origin, kind, RunStatus.QUEUED, resume_from, time.time()))
            conn.commit()
        return run_id

    def get_run(self, run_id: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def all_run_ids(self) -> set[str]:
        """Un id de run ES el session id de Claude Code (`--session-id`), lo que permite excluirlos del roster."""
        with closing(self._connect()) as conn:
            return {r["id"] for r in conn.execute("SELECT id FROM runs")}

    def list_runs(self, status: list[str] | None = None, project: str | None = None, limit: int = 50,
                  before: float | None = None) -> list[dict]:
        sql, params = "SELECT * FROM runs WHERE 1=1", []
        if status:
            sql += f" AND status IN ({','.join('?' * len(status))})"
            params.extend(status)
        if project:
            sql += " AND project_name = ?"
            params.append(project)
        if before is not None:
            sql += " AND created_at < ?"
            params.append(before)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"campos no actualizables: {sorted(unknown)}")
        assignments = ", ".join(f"{k}=?" for k in fields)
        with closing(self._connect()) as conn:
            conn.execute(f"UPDATE runs SET {assignments} WHERE id=?", (*fields.values(), run_id))
            conn.commit()

    def next_seq(self, run_id: str) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM run_events WHERE run_id=?", (run_id,)).fetchone()
            return int(row["m"]) + 1

    def append_events(self, run_id: str, rows: list[tuple[int, str, str]]) -> None:
        if not rows:
            return
        now = time.time()
        with closing(self._connect()) as conn:
            conn.executemany("INSERT OR IGNORE INTO run_events (run_id, seq, ts, kind, payload) VALUES (?,?,?,?,?)",
                             [(run_id, seq, now, kind, payload) for seq, kind, payload in rows])
            conn.commit()

    def get_events(self, run_id: str, after_seq: int = 0, limit: int = 200) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq ASC LIMIT ?",
                                (run_id, after_seq, limit)).fetchall()
            return [dict(r) for r in rows]

    def count_events(self, run_id: str) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM run_events WHERE run_id=?", (run_id,)).fetchone()["n"])

    def sweep_stale_runs(self) -> int:
        """Los runs que un backend caído dejó activos pasan a `failed`."""
        with closing(self._connect()) as conn:
            cur = conn.execute("UPDATE runs SET status=?, error=?, ended_at=? WHERE status IN (?,?)",
                               (RunStatus.FAILED, "el backend se reinició durante el run", time.time(),
                                RunStatus.QUEUED, RunStatus.RUNNING))
            conn.commit()
            return cur.rowcount

    def stats(self, period_s: float = 86400) -> dict:
        cutoff = time.time() - period_s
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n, COALESCE(SUM(cost_usd),0) AS cost, "
                                "COALESCE(SUM(input_tokens),0) AS inp, COALESCE(SUM(output_tokens),0) AS out "
                                "FROM runs WHERE created_at >= ? GROUP BY status", (cutoff,)).fetchall()
        by_status = {s: 0 for s in RunStatus.ALL}
        cost = inp = out = 0
        for r in rows:
            by_status[r["status"]] = r["n"]
            cost += r["cost"]
            inp += r["inp"]
            out += r["out"]
        return {"by_status": by_status, "total_runs": sum(by_status.values()), "total_cost_usd": round(cost, 6),
                "total_input_tokens": inp, "total_output_tokens": out}

    def projects(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT project_name, project_path, COUNT(*) AS n, MAX(created_at) AS last, "
                                "SUM(CASE WHEN status IN ('queued','running') THEN 1 ELSE 0 END) AS active "
                                "FROM runs GROUP BY project_path ORDER BY last DESC").fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------- steers y avisos
    def record_steer(self, session_id: str, voice_name: str, project: str, prompt: str, outcome: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO steers (session_id, voice_name, project, prompt, outcome, created_at) VALUES (?,?,?,?,?,?)",
                         (session_id, voice_name, project, prompt, outcome, time.time()))
            conn.commit()

    def list_steers(self, limit: int = 50) -> list[dict]:
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM steers ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]

    def record_announcement(self, priority: str, text: str, channel: str, kind: str = "") -> None:
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO announcements (ts, priority, text, channel, kind) VALUES (?,?,?,?,?)",
                         (time.time(), priority, text, channel, kind))
            conn.commit()

    def list_announcements(self, limit: int = 50, since: float | None = None) -> list[dict]:
        with closing(self._connect()) as conn:
            if since is not None:
                rows = conn.execute("SELECT * FROM announcements WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (since, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM announcements ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

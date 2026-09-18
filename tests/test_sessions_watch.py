"""Vigilancia de sesiones de Claude Code: roster + transcripts → conversaciones nombrables y transiciones."""
import json
import os
import time
from pathlib import Path

from jarvis.sessions import watch
from jarvis.sessions.watch import (FRESH, GONE, IDLE, NEEDS_YOU, WORKING, SessionWatcher, build_snapshot, encode_cwd,
                                   session_to_dict)

ME = os.getpid()
DEAD = 999999


def live(monkeypatch):
    """Los pids de prueba están vivos salvo DEAD; el ppid es ajeno (no son hijos nuestros)."""
    monkeypatch.setattr(watch, "pid_alive", lambda pid: int(pid) != DEAD)
    monkeypatch.setattr(watch, "parent_pid", lambda pid: 1)


def roster(root: Path, pid: int, sid: str, cwd: str, status="busy", waiting=None, entrypoint="cli", name=None,
           started=None, sock=None):
    d = root / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    now_ms = int(time.time() * 1000)
    data = {"pid": pid, "sessionId": sid, "cwd": cwd, "startedAt": started or now_ms, "statusUpdatedAt": now_ms,
            "entrypoint": entrypoint, "kind": "interactive", "name": name or Path(cwd).name, "version": "2.1.276"}
    if status:
        data["status"] = status
    if waiting:
        data["waitingFor"] = waiting
    if sock:
        data["messagingSocketPath"] = sock
    (d / f"{pid}.json").write_text(json.dumps(data))


def transcript(root: Path, cwd: str, sid: str, title="Arreglar el footer", last_text="Hecho.", tools=("Edit",)):
    p = root / "projects" / encode_cwd(cwd) / f"{sid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "last-prompt", "lastPrompt": "arregla el footer", "sessionId": sid},
             {"type": "ai-title", "aiTitle": title, "sessionId": sid},
             {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": t} for t in tools]
                                               + [{"type": "text", "text": last_text}]}}]
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


def test_roster_and_states(tmp_path, monkeypatch):
    live(monkeypatch)
    root = tmp_path / "claude"
    roster(root, 1001, "s-work", "/tmp/proj/pingou", status="busy")
    transcript(root, "/tmp/proj/pingou", "s-work")
    roster(root, 1002, "s-wait", "/tmp/proj/sonda", status="waiting", waiting="permission prompt", sock=str(tmp_path / "x.sock"))
    transcript(root, "/tmp/proj/sonda", "s-wait")
    roster(root, 1003, "s-fresh", "/tmp/proj/nuevo", status="idle")
    roster(root, DEAD, "s-dead", "/tmp/proj/viejo", status="busy")
    transcript(root, "/tmp/proj/viejo", "s-dead")
    roster(root, 1004, "s-q", "/tmp/proj/fluo", status="idle")
    transcript(root, "/tmp/proj/fluo", "s-q", last_text="¿Postgres o SQLite?", tools=("Read",))
    snap = build_snapshot(roots=[root])
    by = {s.session_id: s for s in snap.sessions}
    assert by["s-work"].state == WORKING and by["s-work"].voice_name == "pingou" and by["s-work"].title == "Arreglar el footer"
    assert by["s-wait"].state == NEEDS_YOU and by["s-wait"].needs == "permission prompt" and by["s-wait"].needs_a_human_hand
    assert not by["s-wait"].steerable  # el socket no existe en disco
    assert by["s-fresh"].state == FRESH and not by["s-fresh"].announceable
    assert by["s-dead"].state == GONE
    assert by["s-q"].state == NEEDS_YOU and by["s-q"].needs is None  # terminó preguntando
    assert [s.session_id for s in snap.needing_you()] and snap.needing_you()[0].state == NEEDS_YOU
    d = session_to_dict(by["s-wait"])
    assert d["needs_label"] == "esperando un permiso" and d["state_label"] == "te necesita"


def test_voice_names_and_resolve(tmp_path, monkeypatch):
    live(monkeypatch)
    root = tmp_path / "claude"
    roster(root, 1001, "a", "/tmp/Desktop/hammer", status="busy", started=1000)
    transcript(root, "/tmp/Desktop/hammer", "a", title="Hammer login flow")
    roster(root, 1002, "b", "/tmp/Projects/hammer", status="idle", started=2000)
    transcript(root, "/tmp/Projects/hammer", "b", title="Hammer search index")
    roster(root, 1003, "c", "/tmp/Projects/hammer", status="busy", started=3000)
    transcript(root, "/tmp/Projects/hammer", "c", title="Hammer login flow")  # mismo título que a: no distingue por tema
    snap = build_snapshot(roots=[root])
    names = {s.session_id: s.voice_name for s in snap.sessions}
    assert names["a"] == "hammer en Desktop"
    assert names["b"] == "hammer en Projects, la de search index" and names["c"] == "hammer en Projects, la de login flow"
    assert [s.session_id for s in snap.resolve("hammer en Desktop")] == ["a"]
    assert len(snap.resolve("hammer")) == 3  # ambiguo: se devuelven todas
    assert [s.session_id for s in snap.resolve("la de login flow en Projects")] == ["c"]
    roster(root, 1003, "c", "/tmp/Projects/hammer", status="busy", started=3000)
    transcript(root, "/tmp/Projects/hammer", "c", title="Hammer search index")  # mismo tema que b: se distingue por estado
    names = {s.session_id: s.voice_name for s in build_snapshot(roots=[root]).sessions}
    assert {names["b"], names["c"]} == {"la hammer en Projects que está en espera", "la hammer en Projects que está trabajando"}
    assert [s.session_id for s in snap.resolve("esa", last_mentioned="b")] == ["b"]
    assert snap.resolve("") == []


def test_own_children_are_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "pid_alive", lambda pid: True)
    root = tmp_path / "claude"
    roster(root, 1001, "brain", "/tmp/home", status="busy", entrypoint="sdk-cli")
    transcript(root, "/tmp/home", "brain")
    roster(root, 1002, "user", "/tmp/proj/x", status="busy")
    transcript(root, "/tmp/proj/x", "user")
    monkeypatch.setattr(watch, "parent_pid", lambda pid: ME)  # todos parecen hijos nuestros…
    snap = build_snapshot(roots=[root])
    assert [s.session_id for s in snap.sessions] == ["user"]  # …pero solo se excluye el que no viene de un cli interactivo
    snap = build_snapshot(roots=[root], exclude_ids={"user"})
    assert snap.sessions == []


def test_watcher_publishes_transitions_only(tmp_path, monkeypatch):
    live(monkeypatch)
    root = tmp_path / "claude"
    cwd = "/tmp/proj/pingou"
    roster(root, 1001, "s1", cwd, status="busy")
    transcript(root, cwd, "s1")
    w = SessionWatcher(roots=[root], interval=0.01)
    events = []
    w.on_event(events.append)
    t0 = time.time()
    w.poll_once(now=t0)
    assert events == []  # el primer sondeo no anuncia nada
    roster(root, 1001, "s1", cwd, status="waiting", waiting="permission prompt")
    w.poll_once(now=t0 + 5)
    assert [e["kind"] for e in events] == ["needs_you"] and events[0]["session"]["voice_name"] == "pingou"
    roster(root, 1001, "s1", cwd, status="busy")
    w.poll_once(now=t0 + 10)
    roster(root, 1001, "s1", cwd, status="idle")
    w.poll_once(now=t0 + 15)  # trabajó 5 s: no cuenta como trabajo terminado
    assert len(events) == 1
    roster(root, 1001, "s1", cwd, status="busy")
    w.poll_once(now=t0 + 20)
    roster(root, 1001, "s1", cwd, status="idle")
    w.poll_once(now=t0 + 20 + watch.MIN_WORK_SEC + 1)
    assert [e["kind"] for e in events] == ["needs_you", "finished"]
    # desaparece del roster: se conserva como «gone» un rato
    (root / "sessions" / "1001.json").unlink()
    snap = w.poll_once(now=t0 + 100)
    assert snap.sessions and snap.sessions[0].state == GONE
    snap = w.poll_once(now=t0 + 100 + watch.GONE_RETENTION_SEC + 1)
    assert snap.sessions == []


def test_age_phrase_and_encode():
    assert watch.age_phrase(30) == "menos de un minuto"
    assert watch.age_phrase(65) == "un minuto" and watch.age_phrase(600) == "unos 10 minutos"
    assert watch.age_phrase(3700) == "cerca de una hora" and watch.age_phrase(3 * 3600) == "unas 3 horas"
    assert encode_cwd("/Users/x/a.b/c") == "-Users-x-a-b-c"
    assert watch.project_name("/x/repo/.claude/worktrees/rama") == "repo"

"""Runs: parser puro, almacén SQLite y ejecutor con un `claude` falso (nunca se lanza el real)."""
import asyncio
import json
import os
import sys
import textwrap
import time

import pytest

from jarvis.runs import stream_parser
from jarvis.runs.executor import RunExecutor, child_env
from jarvis.runs.store import RunStatus, RunStore

FAKE_CLAUDE = textwrap.dedent(f"""\
    #!{sys.executable}
    import json, sys, time
    prompt = sys.stdin.read()
    def out(o):
        sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    out({{"type": "system", "subtype": "init", "model": "claude-test", "cwd": "."}})
    if "SLEEP" in prompt:
        time.sleep(30)
    if "QUESTION" in prompt:
        out({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "¿Postgres o SQLite?"}}],
                                              "usage": {{"input_tokens": 3, "output_tokens": 2}}}}}})
        out({{"type": "result", "result": "¿Postgres o SQLite?", "total_cost_usd": 0.0, "usage": {{}}, "num_turns": 1}})
        sys.exit(0)
    out({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "voy"}}, {{"type": "tool_use", "name": "Write", "input": {{"file_path": "x.py"}}}}],
                                          "usage": {{"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 1}}}}}})
    if "FAIL" in prompt:
        sys.stderr.write("boom\\n"); sys.exit(2)
    out({{"type": "result", "result": "listo", "total_cost_usd": 0.01, "usage": {{"input_tokens": 10, "output_tokens": 5}}, "num_turns": 1, "is_error": False}})
""")


@pytest.fixture
def fake_claude(tmp_path):
    p = tmp_path / "claude"
    p.write_text(FAKE_CLAUDE)
    os.chmod(p, 0o755)
    return str(p)


def test_stream_parser_outcomes():
    assert stream_parser.parse_line("no json") is None and stream_parser.parse_line("") is None
    ev = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}]}}
    assert stream_parser.summarize_assistant(ev) == "Bash: ls -la"
    assert stream_parser.assess_outcome([ev]) == stream_parser.OK
    q = {"type": "assistant", "message": {"content": [{"type": "text", "text": "¿Postgres o SQLite?"}]}}
    assert stream_parser.assess_outcome([q]) == stream_parser.STALLED
    r = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}, {"type": "text", "text": "ok."}]}}
    assert stream_parser.assess_outcome([r]) == stream_parser.NO_CHANGES
    big = json.dumps({"type": "assistant", "x": "a" * (stream_parser.PAYLOAD_MAX_CHARS + 10)})
    assert len(stream_parser.cap_payload(big, json.loads(big))) <= stream_parser.PAYLOAD_MAX_CHARS
    assert stream_parser.extract_result_metrics({"total_cost_usd": "x", "usage": None})["cost_usd"] == 0.0


def test_store_crud_and_sweep(tmp_path):
    st = RunStore(tmp_path / "runs.sqlite")
    rid = st.create_run("hola", "proj", "/tmp/proj", "voice")
    assert st.get_run(rid)["status"] == RunStatus.QUEUED and rid in st.all_run_ids()
    st.update_run(rid, status=RunStatus.RUNNING, pid=1)
    with pytest.raises(ValueError):
        st.update_run(rid, nope=1)
    st.append_events(rid, [(1, "system", "{}"), (2, "assistant", "{}")])
    assert st.count_events(rid) == 2 and st.next_seq(rid) == 3 and [e["seq"] for e in st.get_events(rid, after_seq=1)] == [2]
    assert st.sweep_stale_runs() == 1 and st.get_run(rid)["status"] == RunStatus.FAILED
    assert st.stats()["by_status"]["failed"] == 1 and st.projects()[0]["project_name"] == "proj"
    st.record_steer("s", "pingou", "pingou", "usa postgres", "sent")
    st.record_announcement("urgent", "pingou te necesita", "voice", "needs_you")
    assert st.list_steers()[0]["outcome"] == "sent" and st.list_announcements()[0]["kind"] == "needs_you"


def test_child_env_scrubs_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "x")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("HOME_KEEP", "1")
    env = child_env()
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env and "CLAUDECODE" not in env and env["HOME_KEEP"] == "1"


async def _run(store, ex, prompt, project):
    rid = await ex.spawn(prompt, project.name, str(project), "voice")
    await ex.wait_for(rid, timeout=20)
    return store.get_run(rid)


async def test_executor_success_records_everything(tmp_path, fake_claude):
    store = RunStore(tmp_path / "runs.sqlite")
    ex = RunExecutor(store, claude_path=fake_claude, poll_sec=0.05)
    seen = []
    ex.subscribe(seen.append)
    proj = tmp_path / "proj"
    proj.mkdir()
    run = await _run(store, ex, "haz algo", proj)
    assert run["status"] == RunStatus.SUCCEEDED and run["model"] == "claude-test" and run["cost_usd"] == 0.01
    assert run["input_tokens"] == 10 and run["result_text"] == "listo" and run["summary"] == "voy"
    kinds = [e["kind"] for e in store.get_events(run["id"])]
    assert kinds == ["system", "assistant", "result"]
    assert [m["type"] for m in seen][0] == "run_started" and seen[-1]["type"] == "run_finished"
    assert ex._command(run["id"], None, None)[1:6] == ["-p", "--output-format", "stream-json", "--verbose", "--session-id"]
    assert "--dangerously-skip-permissions" in ex._command("x", None, None)
    assert ex.outcome_of(run["id"]) == stream_parser.OK


async def test_executor_failure_and_stall(tmp_path, fake_claude):
    store = RunStore(tmp_path / "runs.sqlite")
    ex = RunExecutor(store, claude_path=fake_claude, poll_sec=0.05)
    proj = tmp_path / "proj"
    proj.mkdir()
    run = await _run(store, ex, "FAIL por favor", proj)
    assert run["status"] == RunStatus.FAILED and run["exit_code"] == 2 and "boom" in run["error"]
    run = await _run(store, ex, "QUESTION", proj)
    assert run["status"] == RunStatus.SUCCEEDED and ex.outcome_of(run["id"]) == stream_parser.STALLED


async def test_executor_timeout_and_cancel(tmp_path, fake_claude):
    store = RunStore(tmp_path / "runs.sqlite")
    ex = RunExecutor(store, claude_path=fake_claude, poll_sec=0.05, grace_sec=1)
    proj = tmp_path / "proj"
    proj.mkdir()
    rid = await ex.spawn("SLEEP", proj.name, str(proj), "voice", timeout_sec=1.5)
    run = await ex.wait_for(rid, timeout=20)
    assert run["status"] == RunStatus.TIMED_OUT and "límite" in run["error"]
    rid = await ex.spawn("SLEEP", proj.name, str(proj), "voice")
    await asyncio.sleep(0.5)
    assert await ex.cancel(rid) is True
    run = await ex.wait_for(rid, timeout=10)
    assert run["status"] == RunStatus.CANCELLED and await ex.cancel(rid) is False


async def test_executor_missing_binary_is_terminal(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    ex = RunExecutor(store, claude_path=str(tmp_path / "nope"), poll_sec=0.05)
    proj = tmp_path / "proj"
    proj.mkdir()
    run = await _run(store, ex, "x", proj)
    assert run["status"] == RunStatus.FAILED and "no se pudo lanzar" in run["error"]

"""Lanza runs de Claude Code (`claude -p`) y registra todo lo que hacen.

Dos invariantes:
1. Un run SIEMPRE llega a un estado terminal (succeeded/failed/timed_out/cancelled).
2. Toda transición se escribe en el almacén antes de publicarse; los eventos son avisos, no la verdad.
"""
from __future__ import annotations

import asyncio
import math
import os
import shutil
import time
from typing import Any, Callable

from . import stream_parser
from .store import RunStatus, RunStore

SCRUBBED_ENV_PREFIXES = ("CLAUDE_CODE_", "ANTHROPIC_")
SCRUBBED_ENV_KEYS = {"CLAUDECODE"}
STREAM_LINE_LIMIT = 32 * 1024 * 1024
_EVENT_BATCH = 25
_EVENT_FLUSH_SEC = 0.5
_STDERR_CHARS = 2000
_STDERR_BYTES = _STDERR_CHARS * 4
_EOF_EXIT_GRACE_SEC = 30.0
_DEFAULT_IDLE_SEC = 30 * 60
_DEFAULT_TIMEOUT_SEC = 6 * 3600
MAX_BOUND_SEC = 24 * 3600


def child_env() -> dict[str, str]:
    """Entorno de cada hijo: sin claves de API (el CLI las preferiría a la sesión) y sin marcas de anidamiento."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(SCRUBBED_ENV_PREFIXES) and k not in SCRUBBED_ENV_KEYS}


def _bound(value: Any, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v) or v <= 0:
        return default
    return min(v, float(MAX_BOUND_SEC))


class _IdleTimeout(Exception):
    pass


class RunExecutor:
    def __init__(self, store: RunStore, claude_path: str | None = None, max_concurrent: int = 3,
                 grace_sec: float = 10.0, default_timeout_sec: float | None = None, idle_sec: float | None = None,
                 default_model: str | None = None, skip_permissions: bool = True, poll_sec: float = 1.0,
                 logger: Any = None) -> None:
        self.store = store
        self.claude_path = claude_path or shutil.which("claude") or "claude"
        self.grace_sec = grace_sec
        self.idle_sec = _bound(idle_sec, _DEFAULT_IDLE_SEC)
        self.default_timeout = _bound(default_timeout_sec, _DEFAULT_TIMEOUT_SEC)
        self.default_model = default_model
        self.skip_permissions = skip_permissions
        self.poll_sec = poll_sec if poll_sec and poll_sec > 0 else 1.0
        self.log = logger
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._subscribers: list[Callable[[dict], None]] = []
        self._slots = asyncio.Semaphore(max_concurrent)
        self._cancelling: set[str] = set()

    def subscribe(self, cb: Callable[[dict], None]) -> None:
        if cb not in self._subscribers:
            self._subscribers.append(cb)

    def _publish(self, message: dict) -> None:
        for cb in list(self._subscribers):
            try:
                cb(message)
            except Exception:
                if self.log:
                    self.log.warning("suscriptor de runs falló", exc_info=True)

    def active_count(self) -> int:
        return len([t for t in self._tasks.values() if not t.done()])

    def _command(self, run_id: str, resume_from: str | None, model: str | None) -> list[str]:
        cmd = [self.claude_path, "-p", "--output-format", "stream-json", "--verbose", "--session-id", run_id]
        if resume_from:
            cmd += ["--resume", resume_from, "--fork-session"]
        m = model or self.default_model
        if m:
            cmd += ["--model", m]
        if self.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        return cmd

    async def spawn(self, prompt: str, project_name: str, project_path: str, origin: str,
                    resume_from: str | None = None, timeout_sec: float = 0, model: str | None = None,
                    kind: str = "run") -> str:
        run_id = await asyncio.to_thread(self.store.create_run, prompt, project_name, project_path, origin, resume_from, kind)
        return await self.start_existing(run_id, prompt, project_path, resume_from, timeout_sec, model)

    async def start_existing(self, run_id: str, prompt: str, project_path: str, resume_from: str | None = None,
                             timeout_sec: float = 0, model: str | None = None) -> str:
        coro = None
        try:
            bound = timeout_sec if timeout_sec and timeout_sec > 0 else self.default_timeout
            await asyncio.to_thread(self.store.update_run, run_id, requested_model=model or self.default_model or "")
            coro = self._drive(run_id, prompt, project_path, resume_from, bound, model)
            task = asyncio.create_task(coro)
            coro = None
        except BaseException as e:
            if coro is not None:
                coro.close()
            self._finish_blocking(run_id, RunStatus.FAILED, error=repr(e)[:_STDERR_CHARS])
            raise
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))
        return run_id

    async def cancel(self, run_id: str) -> bool:
        run = await asyncio.to_thread(self.store.get_run, run_id)
        if run is None or run["status"] in RunStatus.TERMINAL:
            return False
        self._cancelling.add(run_id)
        proc = self._procs.get(run_id)
        if proc is not None:
            if proc.returncode is None:
                await self._terminate(proc)
            await self._finish(run_id, RunStatus.CANCELLED, exit_code=proc.returncode)
            return True
        try:
            await self._finish(run_id, RunStatus.CANCELLED)
            task = self._tasks.get(run_id)
            if task is not None and not task.done() and run["status"] == RunStatus.QUEUED:
                task.cancel()
        finally:
            if self._tasks.get(run_id) is None:
                self._cancelling.discard(run_id)
        return True

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=self.grace_sec)
            return
        except asyncio.TimeoutError:
            pass
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=self.grace_sec)
        except asyncio.TimeoutError:
            if self.log:
                self.log.error("el hijo %s no murió tras SIGKILL", getattr(proc, "pid", "?"))

    async def wait_for(self, run_id: str, timeout: float = 30) -> dict | None:
        task = self._tasks.get(run_id)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return await asyncio.to_thread(self.store.get_run, run_id)

    async def _publish_run_updated(self, run_id: str) -> None:
        run = await asyncio.to_thread(self.store.get_run, run_id)
        if run is not None:
            self._publish({"type": "run_updated", "run": run})

    def _finish_write(self, run_id: str, status: str, **fields: Any) -> dict | None:
        run = self.store.get_run(run_id)
        if run and run["status"] in RunStatus.TERMINAL:
            return None
        self.store.update_run(run_id, status=status, ended_at=time.time(), **fields)
        return self.store.get_run(run_id)

    async def _finish(self, run_id: str, status: str, **fields: Any) -> None:
        row = await asyncio.to_thread(self._finish_write, run_id, status, **fields)
        if row is not None:
            self._publish({"type": "run_finished", "run": row})

    def _finish_blocking(self, run_id: str, status: str, **fields: Any) -> None:
        row = self._finish_write(run_id, status, **fields)
        if row is not None:
            self._publish({"type": "run_finished", "run": row})

    async def _drain_stderr(self, proc: asyncio.subprocess.Process, sink: bytearray) -> None:
        if proc.stderr is None:
            return
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                return
            sink.extend(chunk)
            if len(sink) > _STDERR_BYTES:
                del sink[:-_STDERR_BYTES]

    async def _collect_stderr(self, task: asyncio.Task | None, sink: bytearray) -> str:
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
            except Exception:
                pass
        return bytes(sink).decode(errors="replace")[-_STDERR_CHARS:]

    async def _drive(self, run_id: str, prompt: str, project_path: str, resume_from: str | None,
                     timeout_sec: float, model: str | None) -> None:
        proc = None
        slot_held = False
        stderr_task = None
        stderr_sink = bytearray()
        try:
            await self._slots.acquire()
            slot_held = True
            if run_id in self._cancelling:
                await self._finish(run_id, RunStatus.CANCELLED)
                return
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self._command(run_id, resume_from, model), stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=project_path or None,
                    env=child_env(), limit=STREAM_LINE_LIMIT)
            except (FileNotFoundError, NotADirectoryError, PermissionError) as e:
                await self._finish(run_id, RunStatus.FAILED, error=f"no se pudo lanzar claude: {e}")
                return
            self._procs[run_id] = proc
            stderr_task = asyncio.create_task(self._drain_stderr(proc, stderr_sink))
            await asyncio.to_thread(self.store.update_run, run_id, status=RunStatus.RUNNING, pid=proc.pid, started_at=time.time())
            started = await asyncio.to_thread(self.store.get_run, run_id)
            self._publish({"type": "run_started", "run": started})
            try:
                proc.stdin.write(prompt.encode())
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass
            try:
                await asyncio.wait_for(self._consume(run_id, proc), timeout=timeout_sec)
            except asyncio.TimeoutError:
                await self._terminate(proc)
                stderr = await self._collect_stderr(stderr_task, stderr_sink)
                await self._finish(run_id, RunStatus.TIMED_OUT, error=f"superó el límite de {int(timeout_sec)}s" + (f"\n{stderr}" if stderr else ""))
                return
            except _IdleTimeout:
                await self._terminate(proc)
                stderr = await self._collect_stderr(stderr_task, stderr_sink)
                await self._finish(run_id, RunStatus.TIMED_OUT, error=f"claude no produjo salida durante {int(self.idle_sec)}s; se detuvo" + (f"\n{stderr}" if stderr else ""))
                return
            eof_hang = False
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=_EOF_EXIT_GRACE_SEC)
            except asyncio.TimeoutError:
                eof_hang = True
                await self._terminate(proc)
            stderr = await self._collect_stderr(stderr_task, stderr_sink)
            run = await asyncio.to_thread(self.store.get_run, run_id)
            if run and run["status"] in RunStatus.TERMINAL:
                return
            if run_id in self._cancelling:
                await self._finish(run_id, RunStatus.CANCELLED, exit_code=proc.returncode)
            elif eof_hang:
                await self._finish(run_id, RunStatus.FAILED, exit_code=proc.returncode,
                                   error="claude cerró la salida pero no terminó; se detuvo" + (f"\n{stderr}" if stderr else ""))
            elif proc.returncode == 0 and run and run["is_error"]:
                await self._finish(run_id, RunStatus.FAILED, exit_code=0,
                                   error=(run["result_text"] or stderr or "claude marcó is_error en su resultado")[:_STDERR_CHARS])
            elif proc.returncode == 0:
                await self._finish(run_id, RunStatus.SUCCEEDED, exit_code=0)
            else:
                await self._finish(run_id, RunStatus.FAILED, exit_code=proc.returncode, error=stderr or f"código de salida {proc.returncode}")
        except BaseException as e:
            cancelled = run_id in self._cancelling
            if not (cancelled and isinstance(e, asyncio.CancelledError)) and self.log:
                self.log.exception("el run %s falló en el driver", run_id)
            try:
                self._finish_blocking(run_id, RunStatus.CANCELLED if cancelled else RunStatus.FAILED, error=repr(e)[:_STDERR_CHARS])
            except Exception:
                pass
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=self.grace_sec)
                except BaseException:
                    pass
            if isinstance(e, (asyncio.CancelledError, SystemExit, KeyboardInterrupt)):
                raise
        finally:
            self._procs.pop(run_id, None)
            self._cancelling.discard(run_id)
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
            if slot_held:
                self._slots.release()

    async def _consume(self, run_id: str, proc: asyncio.subprocess.Process) -> None:
        seq = await asyncio.to_thread(self.store.next_seq, run_id)
        pending: list[tuple[int, str, str]] = []
        last_flush = time.monotonic()
        totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
        saw_error = False

        async def flush() -> None:
            nonlocal pending, last_flush
            if pending:
                batch, pending = pending, []
                await asyncio.to_thread(self.store.append_events, run_id, batch)
            last_flush = time.monotonic()

        line_task = None
        last_line = time.monotonic()
        exited_ticks = 0
        try:
            while True:
                if line_task is None:
                    line_task = asyncio.ensure_future(proc.stdout.readline())
                done, _ = await asyncio.wait({line_task}, timeout=self.poll_sec)
                if not done:
                    if proc.returncode is not None:
                        exited_ticks += 1
                        if exited_ticks >= 2:
                            return
                        continue
                    if time.monotonic() - last_line >= self.idle_sec:
                        raise _IdleTimeout()
                    continue
                exited_ticks = 0
                finished, line_task = line_task, None
                last_line = time.monotonic()
                try:
                    raw = finished.result()
                except ValueError:
                    continue
                if not raw:
                    break
                line = raw.decode(errors="replace")
                event = stream_parser.parse_line(line)
                if event is None:
                    continue
                kind = stream_parser.event_kind(event)
                pending.append((seq, kind, stream_parser.cap_payload(line.strip(), event)))
                if kind == "system" and event.get("subtype") == "init":
                    meta = stream_parser.extract_init_metadata(event)
                    if meta["model"]:
                        await flush()
                        await asyncio.to_thread(self.store.update_run, run_id, model=meta["model"])
                        await self._publish_run_updated(run_id)
                elif kind == "assistant":
                    usage = stream_parser.extract_assistant_usage(event)
                    summary = stream_parser.summarize_assistant(event)
                    if any(usage.values()) or summary:
                        for k, v in usage.items():
                            totals[k] += v
                        await flush()
                        await asyncio.to_thread(self.store.update_run, run_id, summary=summary[:200], **totals)
                        await self._publish_run_updated(run_id)
                elif kind == "result":
                    m = stream_parser.extract_result_metrics(event)
                    saw_error = saw_error or bool(m["is_error"])
                    await flush()
                    await asyncio.to_thread(self.store.update_run, run_id, cost_usd=m["cost_usd"], input_tokens=m["input_tokens"],
                                            output_tokens=m["output_tokens"], cache_read_tokens=m["cache_read_tokens"],
                                            cache_creation_tokens=m["cache_creation_tokens"], num_turns=m["num_turns"],
                                            result_text=m["result_text"][:20000], is_error=int(saw_error))
                    await self._publish_run_updated(run_id)
                if len(pending) >= _EVENT_BATCH or time.monotonic() - last_flush >= _EVENT_FLUSH_SEC:
                    await flush()
                self._publish({"type": "run_event", "run_id": run_id, "seq": seq, "kind": kind,
                               "summary": stream_parser.summarize_assistant(event) if kind == "assistant" else ""})
                seq += 1
        finally:
            if line_task is not None:
                line_task.cancel()
            await flush()

    def outcome_of(self, run_id: str) -> str:
        """OK, STALLED o NO_CHANGES para un run que salió con 0, leyendo TODO su flujo."""
        events = []
        after = 0
        while True:
            batch = self.store.get_events(run_id, after_seq=after, limit=1000)
            if not batch:
                break
            for row in batch:
                ev = stream_parser.parse_line(row["payload"])
                if ev:
                    events.append(ev)
            after = batch[-1]["seq"]
        run = self.store.get_run(run_id) or {}
        return stream_parser.assess_outcome(events, run.get("result_text") or "")

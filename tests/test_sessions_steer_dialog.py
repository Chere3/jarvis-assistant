"""Envío por socket a una sesión y pulsación de teclas: vocabulario cerrado, resultados auditables."""
import asyncio
import json
import socket
import tempfile
import threading

from jarvis.sessions import dialog, steer


def test_post_to_session_over_unix_socket(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "tok")
    path = tempfile.mkdtemp(prefix="jv", dir="/tmp") + "/s.sock"  # AF_UNIX exige rutas cortas
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    got = []

    def serve():
        conn, _ = srv.accept()
        data = b""
        while not data.endswith(b"\n") or data.count(b"\n") < 2:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        got.append(data)
        conn.close()

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    assert steer.post_to_session(path, "  usa Postgres  ") == steer.SENT
    th.join(2)
    lines = [json.loads(x) for x in got[0].decode().splitlines()]
    assert lines[0] == {"type": "auth", "token": "tok"}
    assert lines[1]["type"] == "user" and lines[1]["message"]["content"] == "usa Postgres"
    srv.close()


def test_post_refusals(tmp_path):
    assert steer.post_to_session(str(tmp_path / "no.sock"), "hola") == steer.NOT_LIVE
    assert steer.post_to_session(None, "hola") == steer.NOT_LIVE
    stale = tmp_path / "stale.sock"
    stale.write_text("")
    assert steer.post_to_session(str(stale), "hola") in (steer.NOT_LIVE, steer.FAILED)
    assert steer.post_to_session(str(stale), "   ") == steer.REFUSED


def test_key_vocabulary_is_closed():
    assert dialog.normalize_key("sí") == "return" and dialog.normalize_key("Enter") == "return"
    assert dialog.normalize_key("no") == "escape" and dialog.normalize_key("3") == "3"
    for bad in ("", "10", "rm -rf /", "yes please", None, "0", "a"):
        assert dialog.normalize_key(bad) is None
    assert dialog.spoken_key("return") == "Intro" and dialog.spoken_key("2") == "la opción 2"


def test_tty_normalisation():
    assert dialog.normalize_tty("ttys006") == "/dev/ttys006" == dialog.normalize_tty("/dev/ttys006")
    assert dialog.normalize_tty("??") is None and dialog.normalize_tty("") is None
    assert dialog.normalize_tty("/etc/passwd") is None


def test_press_key_never_raises_and_refuses_bad_input():
    assert asyncio.run(dialog.press_key(os_pid := 1, "hola")) == dialog.BAD_KEY
    assert asyncio.run(dialog.press_key("no-es-pid", "return")) == dialog.NO_TTY
    assert asyncio.run(dialog.press_key(-5, "return")) == dialog.NO_TTY

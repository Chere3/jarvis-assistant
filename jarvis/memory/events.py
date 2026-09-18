"""Bus de eventos en proceso. Los eventos se publican solo tras confirmar una escritura."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

Listener = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self._seq = 0

    def subscribe(self, fn: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._listeners:
                    self._listeners.remove(fn)

        return unsubscribe

    def publish(self, kind: str, **data: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "kind": kind, "ts": time.time(), **data}
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event)
            except Exception:  # un oyente roto no debe tumbar la escritura ya confirmada
                pass
        return event

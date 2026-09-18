"""Máquina de estados explícita de la conversación por voz/texto."""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    DISABLED = "DISABLED"
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


TRANSITIONS: dict[State, set[State]] = {
    State.DISABLED: {State.IDLE, State.ERROR},
    State.IDLE: {State.LISTENING, State.THINKING, State.DISABLED, State.ERROR, State.SPEAKING},
    State.LISTENING: {State.TRANSCRIBING, State.IDLE, State.ERROR, State.DISABLED},
    State.TRANSCRIBING: {State.THINKING, State.IDLE, State.ERROR},
    State.THINKING: {State.SPEAKING, State.AWAITING_APPROVAL, State.IDLE, State.ERROR},
    State.AWAITING_APPROVAL: {State.THINKING, State.IDLE, State.SPEAKING, State.ERROR, State.LISTENING},
    State.SPEAKING: {State.IDLE, State.LISTENING, State.AWAITING_APPROVAL, State.ERROR},
    State.ERROR: {State.IDLE, State.DISABLED},
}


class InvalidTransition(Exception):
    pass


def check_transition(src: State, dst: State) -> None:
    if src == dst:
        return
    if dst not in TRANSITIONS[src]:
        raise InvalidTransition(f"{src.value} -> {dst.value}")

"""Ambient "who is making this change" context.

The flush listener sees *what* changed but has no way to know who asked
for it. Threading an actor argument through every crud function would
touch every call site and still miss the ones that mutate an ORM object
directly, so the actor rides in a ContextVar instead.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

HUMAN = "human"
AGENT = "agent"
SYSTEM = "system"


@dataclass(frozen=True)
class Actor:
    """Who (or what) is responsible for a change.

    `agent_run_id` / `agent_action_id` stay meaningful even when `type` is
    `human`: an operator approving an agent proposal is a human actor
    applying an agent's work, and the change log records both sides.
    """

    type: str = SYSTEM
    id: str | None = None
    agent_run_id: str | None = None
    agent_action_id: str | None = None


SYSTEM_ACTOR = Actor(type=SYSTEM, id="system")

_current_actor: ContextVar[Actor] = ContextVar("current_actor", default=SYSTEM_ACTOR)


def get_actor() -> Actor:
    return _current_actor.get()


def set_actor(actor: Actor) -> Token:
    return _current_actor.set(actor)


def reset_actor(token: Token) -> None:
    _current_actor.reset(token)


@contextmanager
def actor_context(actor: Actor) -> Iterator[Actor]:
    """Attribute every change made in this block to `actor`.

    Nests correctly, which matters for delegated agent runs: a subagent's
    context is restored to the Boss's on exit.
    """
    token = set_actor(actor)
    try:
        yield actor
    finally:
        reset_actor(token)


#: Free-text explanation attached to every change made in the current block.
#: Populates `change_log.note` -- used by reverts to record what they undo.
_current_note: ContextVar[str | None] = ContextVar("current_change_note", default=None)


def get_note() -> str | None:
    return _current_note.get()


@contextmanager
def note_context(note: str | None) -> Iterator[None]:
    token = _current_note.set(note)
    try:
        yield
    finally:
        _current_note.reset(token)

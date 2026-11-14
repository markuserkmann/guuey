"""Shared resource limits for the worker, retrieval and prompt construction."""
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class ResearchMode:
    rounds: int
    deadline: int
    prompt_chars: int
    livecrawl_pages: int
    planning_model: str


RESEARCH_MODES = {
    'fast': ResearchMode(1, 45, 150000, 4, 'gemini-3.5-flash-lite'),
    'auto': ResearchMode(2, 120, 320000, 8, 'gemini-3.5-flash'),
    'deep': ResearchMode(3, 240, 600000, 16, 'gemini-3.5-flash'),
}
RESEARCH_LIMITS = {name: (profile.rounds, profile.deadline) for name, profile in RESEARCH_MODES.items()}
research_mode: ContextVar[str] = ContextVar('research_mode', default='auto')
research_deadline: ContextVar[float | None] = ContextVar('research_deadline', default=None)


def current_profile():
    return RESEARCH_MODES[research_mode.get()]


def remaining_seconds():
    deadline = research_deadline.get()
    return max(0, deadline - monotonic()) if deadline is not None else float('inf')


class ResearchDeadline(Exception):
    pass

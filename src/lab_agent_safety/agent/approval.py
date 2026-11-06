"""Pluggable approval for physical tool calls.

The harness asks an Approver before every physical tool call (never for read-only
tools). Two implementations:
    CliApprover   a person answers y/n in the terminal
    AutoApprover  approves everything, so benchmark runs are unattended (D3); approval
                  is held fixed so it doesn't confound the safety-layer comparison
"""

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Protocol

try:
    import termios
except ImportError:  # Windows
    termios = None


@dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str
    approver: str
    answer: str | None = None  # the operator's raw input, for the audit trail


class Approver(Protocol):
    async def decide(self, tool: str, tool_input: dict) -> Decision: ...


class AutoApprover:
    async def decide(self, tool: str, tool_input: dict) -> Decision:
        return Decision(True, "auto-approve policy", "auto")


def _discard_typeahead() -> None:
    """Throw away keystrokes typed before the question was shown.

    Otherwise an answer typed while no prompt was visible is read by whichever
    question comes next, and the operator approves or denies an action they never saw.
    """
    if termios is not None and sys.stdin.isatty():
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)


class CliApprover:
    """Ask a person at the terminal. Anything other than y/yes is a denial."""

    def __init__(self) -> None:
        # The agent can issue several tool calls in one turn, and the SDK asks for
        # permission concurrently. One prompt at a time, or a person would see them interleaved.
        self._one_at_a_time = asyncio.Lock()

    @staticmethod
    def available() -> bool:
        """True if there is an interactive terminal to ask."""
        return sys.stdin.isatty()

    def _ask(self, question: str) -> str:
        _discard_typeahead()
        return input(question)

    async def decide(self, tool: str, tool_input: dict) -> Decision:
        question = f"\nApprove {tool} {json.dumps(tool_input)}? [y/N] "
        async with self._one_at_a_time:
            try:
                answer = await asyncio.to_thread(self._ask, question)  # don't block the event loop
            except EOFError:
                return Decision(False, "denied: no operator input (stdin closed)", "cli")
        if answer.strip().lower() in ("y", "yes"):
            return Decision(True, "approved by operator", "cli", answer)
        return Decision(False, "denied by operator", "cli", answer)

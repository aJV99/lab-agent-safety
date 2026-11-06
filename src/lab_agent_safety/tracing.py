"""Append-only JSONL traces (D8).

Each run directory holds two streams written by two processes:
    agent.jsonl   written by the agent harness: tool calls, approvals, tokens, cost, latency
    lab.jsonl     written by the MCP server: every command, its violations, who stopped it
Every line has the same envelope: {"ts", "run_id", "source", "event", ...payload}.
The grader merges the streams by timestamp.

Each line is flushed as it is written, so a crashed run still leaves a usable trace.
"""

import json
import time
from pathlib import Path


class TraceWriter:
    def __init__(self, path: Path, run_id: str, source: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")
        self.run_id = run_id
        self.source = source

    def write(self, event: str, **payload) -> None:
        record = {"ts": time.time(), "run_id": self.run_id, "source": self.source, "event": event, **payload}
        self._file.write(json.dumps(record, default=str) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_trace(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def merge_traces(*paths: Path) -> list[dict]:
    """All events from several streams, in time order (stable for equal timestamps)."""
    events = [e for p in paths for e in read_trace(p)]
    return sorted(events, key=lambda e: e["ts"])

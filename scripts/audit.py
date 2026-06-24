#!/usr/bin/env python3
"""Loopita structured audit log.

Append-only audit.jsonl, one event per line. The report and retro are
assembled from this log, so every meaningful transition is logged here.
See conventions.md section 5.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import _common as c

EVENTS = {
    "spawn", "status", "signal", "blocker", "escalation",
    "merge", "checkpoint", "report", "learning", "strategy",
}


def _audit_path(home: Path, run_id: str) -> Path:
    return c.run_dir(home, run_id) / "audit.jsonl"


def cmd_log(args: argparse.Namespace, home: Path) -> None:
    if args.event not in EVENTS:
        c.fail(f"invalid event {args.event!r}")
    event = {
        "ts": args.ts,
        "run_id": args.run_id,
        "agent_id": args.agent_id,
        "task_id": args.task_id,
        "event": args.event,
        "strategy": args.strategy,
        "tokens": args.tokens,
        "duration_ms": args.duration_ms,
        "note": args.note,
    }
    c.append_jsonl(_audit_path(home, args.run_id), event)
    c.emit({"ok": True, "event": args.event})


def cmd_query(args: argparse.Namespace, home: Path) -> None:
    rows = c.read_jsonl(_audit_path(home, args.run_id))
    if args.event is not None:
        rows = [r for r in rows if r.get("event") == args.event]
    if args.agent_id is not None:
        rows = [r for r in rows if r.get("agent_id") == args.agent_id]
    c.emit(rows)


def summarize(home: Path, run_id: str) -> dict[str, Any]:
    """Aggregate token/duration totals overall, by agent, and by task."""
    rows = c.read_jsonl(_audit_path(home, run_id))
    total_tokens = 0
    total_duration = 0
    by_agent: dict[str, dict[str, int]] = {}
    by_task: dict[str, dict[str, int]] = {}
    for r in rows:
        tokens = r.get("tokens") or 0
        duration = r.get("duration_ms") or 0
        total_tokens += tokens
        total_duration += duration
        aid = r.get("agent_id")
        if aid:
            slot = by_agent.setdefault(aid, {"tokens": 0, "duration_ms": 0})
            slot["tokens"] += tokens
            slot["duration_ms"] += duration
        tid = r.get("task_id")
        if tid:
            slot = by_task.setdefault(tid, {"tokens": 0, "duration_ms": 0})
            slot["tokens"] += tokens
            slot["duration_ms"] += duration
    return {
        "run_id": run_id,
        "total_tokens": total_tokens,
        "total_duration_ms": total_duration,
        "by_agent": by_agent,
        "by_task": by_task,
    }


def cmd_summary(args: argparse.Namespace, home: Path) -> None:
    c.emit(summarize(home, args.run_id))


def _selftest(home: Path) -> None:
    import io
    import json as _json

    rid = "run-test"
    ns = argparse.Namespace
    cmd_log(ns(run_id=rid, event="spawn", agent_id="a1", task_id="t1",
               strategy="swarm", tokens=None, duration_ms=None,
               note="spawned", ts="2026-06-24T17:31:00Z"), home)
    cmd_log(ns(run_id=rid, event="signal", agent_id="a1", task_id="t1",
               strategy="swarm", tokens=84852, duration_ms=23332,
               note="done", ts="2026-06-24T17:40:00Z"), home)
    cmd_log(ns(run_id=rid, event="signal", agent_id="a2", task_id="t2",
               strategy="swarm", tokens=1000, duration_ms=500,
               note="done", ts="2026-06-24T17:41:00Z"), home)

    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        cmd_query(ns(run_id=rid, event="signal", agent_id="a1"), home)
    finally:
        sys.stdout = old
    q = _json.loads(buf.getvalue())
    assert len(q) == 1 and q[0]["agent_id"] == "a1", q

    s = summarize(home, rid)
    assert s["total_tokens"] == 85852, s
    assert s["total_duration_ms"] == 23832, s
    assert s["by_agent"]["a1"] == {"tokens": 84852, "duration_ms": 23332}, s
    assert s["by_task"]["t2"] == {"tokens": 1000, "duration_ms": 500}, s


def build_parser() -> argparse.ArgumentParser:
    # Carry --home on a parent parser so it is accepted both before AND
    # after the subcommand (argparse only honors top-level options that
    # appear before the verb).
    home_parent = argparse.ArgumentParser(add_help=False)
    home_parent.add_argument("--home", default=argparse.SUPPRESS,
                             help="override $LOOPITA_HOME")

    p = argparse.ArgumentParser(description="Loopita structured audit log", parents=[home_parent])
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("log", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--event", required=True)
    s.add_argument("--agent-id", default=None, dest="agent_id")
    s.add_argument("--task-id", default=None, dest="task_id")
    s.add_argument("--strategy", default=None)
    s.add_argument("--tokens", default=None, type=int)
    s.add_argument("--duration-ms", default=None, type=int, dest="duration_ms")
    s.add_argument("--note", default=None)
    s.add_argument("--ts", required=True)
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("query", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--event", default=None)
    s.add_argument("--agent-id", default=None, dest="agent_id")
    s.set_defaults(func=cmd_query)

    s = sub.add_parser("summary", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.set_defaults(func=cmd_summary)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    home = c.home_dir(getattr(args, "home", None))
    if args.selftest:
        c.run_selftest(_selftest)
        return
    if not getattr(args, "func", None):
        parser.print_help()
        sys.exit(2)
    args.func(args, home)


if __name__ == "__main__":
    main()

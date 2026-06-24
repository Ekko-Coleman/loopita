#!/usr/bin/env python3
"""Loopita report assembly.

`build` renders a Markdown run report from run.json + tasks.jsonl +
audit.jsonl. `retro` is a structured query over the audit log so the
orchestrator can answer follow-up retro questions. Numbers come from the
same aggregation as `audit.py summary` (reused directly).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import _common as c
import audit


def _fmt_ms(ms: int | None) -> str:
    if not ms:
        return "0s"
    seconds = ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60.0
    return f"{minutes:.1f}m"


def _fmt_tokens(tokens: int | None) -> str:
    return f"{tokens or 0:,}"


def _blockers_section(rows: list[dict[str, Any]]) -> str:
    """Pair blocker/escalation events with later resolution events.

    A 'blocker' or 'escalation' event is considered resolved if a later
    'status' or 'signal' event for the same task/agent appears, or if its
    note explicitly mentions a resolution. We keep this simple and report
    the raised event plus the first matching follow-up.
    """
    raised = [r for r in rows if r.get("event") in ("blocker", "escalation")]
    if not raised:
        return "_No blockers or escalations recorded._"

    lines = []
    for b in raised:
        key_agent = b.get("agent_id")
        key_task = b.get("task_id")
        ts = b.get("ts") or ""
        # First later event (status/signal/merge) touching the same task/agent.
        resolution = None
        for r in rows:
            if r is b:
                continue
            if (r.get("ts") or "") <= ts:
                continue
            if r.get("event") not in ("status", "signal", "merge"):
                continue
            if (key_task and r.get("task_id") == key_task) or (
                key_agent and r.get("agent_id") == key_agent
            ):
                resolution = r
                break
        who = key_agent or key_task or "orchestration"
        desc = b.get("note") or b.get("event")
        line = f"- **{b.get('event')}** ({who}): {desc}"
        if resolution:
            res_desc = resolution.get("note") or resolution.get("event")
            line += f"\n  - resolved by *{resolution.get('event')}*: {res_desc}"
        else:
            line += "\n  - _unresolved_"
        lines.append(line)
    return "\n".join(lines)


def build_report(home: Path, run_id: str) -> str:
    run_path = c.run_dir(home, run_id) / "run.json"
    if not run_path.exists():
        c.fail(f"no run.json for run_id={run_id!r}")
    run = c.read_json(run_path)
    tasks = c.read_jsonl(c.run_dir(home, run_id) / "tasks.jsonl")
    audit_rows = c.read_jsonl(c.run_dir(home, run_id) / "audit.jsonl")
    summary = audit.summarize(home, run_id)
    by_task = summary["by_task"]
    by_agent = summary["by_agent"]

    out: list[str] = []
    out.append(f"# Loopita Run Report — {run.get('run_id')}")
    out.append("")
    out.append(f"**Goal:** {run.get('goal')}")
    out.append(f"**Strategy:** {run.get('strategy')}")
    out.append(f"**Status:** {run.get('status')}")
    out.append("")

    # Per-task table
    out.append("## Tasks")
    out.append("")
    out.append("| Task | Technique | Status | Tokens | Elapsed |")
    out.append("| --- | --- | --- | --- | --- |")
    for t in tasks:
        tid = t.get("task_id", "")
        agg = by_task.get(tid, {})
        out.append(
            f"| {tid}: {t.get('title', '')} "
            f"| {t.get('strategy') or '-'} "
            f"| {t.get('status', '')} "
            f"| {_fmt_tokens(agg.get('tokens'))} "
            f"| {_fmt_ms(agg.get('duration_ms'))} |"
        )
    out.append("")

    # Per-agent table
    out.append("## Agents")
    out.append("")
    out.append("| Agent | Task | Tokens | Elapsed |")
    out.append("| --- | --- | --- | --- |")
    # Map agent -> task via the spawn/signal audit events.
    agent_task: dict[str, str] = {}
    for r in audit_rows:
        aid = r.get("agent_id")
        tid = r.get("task_id")
        if aid and tid and aid not in agent_task:
            agent_task[aid] = tid
    for aid in sorted(by_agent):
        agg = by_agent[aid]
        out.append(
            f"| {aid} "
            f"| {agent_task.get(aid, '-')} "
            f"| {_fmt_tokens(agg.get('tokens'))} "
            f"| {_fmt_ms(agg.get('duration_ms'))} |"
        )
    if not by_agent:
        out.append("| _none_ | - | 0 | 0s |")
    out.append("")

    # Blockers & resolutions
    out.append("## Blockers & resolutions")
    out.append("")
    out.append(_blockers_section(audit_rows))
    out.append("")

    # Totals
    out.append("## Totals")
    out.append("")
    out.append(f"- **Total tokens:** {_fmt_tokens(summary['total_tokens'])}")
    out.append(f"- **Total elapsed:** {_fmt_ms(summary['total_duration_ms'])}")
    out.append("")
    return "\n".join(out)


def cmd_build(args: argparse.Namespace, home: Path) -> None:
    print(build_report(home, args.run_id))


def cmd_retro(args: argparse.Namespace, home: Path) -> None:
    rows = c.read_jsonl(c.run_dir(home, args.run_id) / "audit.jsonl")
    if args.event is not None:
        rows = [r for r in rows if r.get("event") == args.event]
    if args.agent_id is not None:
        rows = [r for r in rows if r.get("agent_id") == args.agent_id]
    c.emit(rows)


def _selftest(home: Path) -> None:
    import argparse as _a

    rid = "run-test"
    rd = c.run_dir(home, rid)
    (rd / "agents").mkdir(parents=True, exist_ok=True)
    c.write_json(rd / "run.json", {
        "run_id": rid, "goal": "add auth", "strategy": "swarm",
        "status": "done", "config": {},
        "created_at": "2026-06-24T17:30:00Z",
        "updated_at": "2026-06-24T18:00:00Z",
    })
    c.append_jsonl(rd / "tasks.jsonl", {
        "task_id": "t1", "title": "login endpoint", "scope": "auth",
        "strategy": "swarm", "status": "done", "agent_ids": ["a1"],
        "depends_on": [], "signal_summary": "ok",
        "created_at": "x", "updated_at": "y",
    })
    for ev in [
        {"ts": "2026-06-24T17:31:00Z", "run_id": rid, "agent_id": "a1",
         "task_id": "t1", "event": "spawn", "strategy": "swarm",
         "tokens": None, "duration_ms": None, "note": "go"},
        {"ts": "2026-06-24T17:35:00Z", "run_id": rid, "agent_id": "a1",
         "task_id": "t1", "event": "blocker", "strategy": "swarm",
         "tokens": None, "duration_ms": None, "note": "missing db fixture"},
        {"ts": "2026-06-24T17:40:00Z", "run_id": rid, "agent_id": "a1",
         "task_id": "t1", "event": "signal", "strategy": "swarm",
         "tokens": 84852, "duration_ms": 23332, "note": "resolved, 3 tests"},
    ]:
        c.append_jsonl(rd / "audit.jsonl", ev)

    md = build_report(home, rid)
    assert "Loopita Run Report" in md
    assert "add auth" in md
    assert "login endpoint" in md
    assert "84,852" in md, md  # token formatting
    assert "missing db fixture" in md
    assert "resolved by" in md  # blocker paired to its signal
    assert "Total tokens:** 84,852" in md, md

    # retro is a filtered query
    import io
    import json as _json
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        cmd_retro(_a.Namespace(run_id=rid, event="blocker", agent_id=None), home)
    finally:
        sys.stdout = old
    r = _json.loads(buf.getvalue())
    assert len(r) == 1 and r[0]["event"] == "blocker", r


def build_parser() -> argparse.ArgumentParser:
    # Carry --home on a parent parser so it is accepted both before AND
    # after the subcommand (argparse only honors top-level options that
    # appear before the verb).
    home_parent = argparse.ArgumentParser(add_help=False)
    home_parent.add_argument("--home", default=argparse.SUPPRESS,
                             help="override $LOOPITA_HOME")

    p = argparse.ArgumentParser(description="Loopita report assembly", parents=[home_parent])
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("build", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("retro", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--event", default=None)
    s.add_argument("--agent-id", default=None, dest="agent_id")
    s.set_defaults(func=cmd_retro)

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

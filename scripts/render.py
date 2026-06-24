#!/usr/bin/env python3
"""Loopita run dashboard frame renderer.

Reads a run's state files and prints ONE styled frame, then exits. No daemon,
no raw mode, no Live loop. `rich` is an OPTIONAL dependency: if present, render
a styled TUI; if absent, render clean plain text.

See references/conventions.md for the file schemas this reads.
"""

from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import _common as c
import audit
import report  # for _fmt_ms, _fmt_tokens

# ---------------------------------------------------------------------------
# Optional rich dependency
# ---------------------------------------------------------------------------

try:
    from rich.console import Console, Group
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.progress_bar import ProgressBar
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ---------------------------------------------------------------------------
# Status style map — shared by both render paths (plain uses glyph only)
# ---------------------------------------------------------------------------

STATUS_STYLE: dict[str, tuple[str, str]] = {
    "done":        ("green",  "✓"),
    "in-progress": ("cyan",   "⟳"),
    "blocked":     ("red",    "✗"),
    "pending":     ("yellow", "·"),
    "planning":    ("yellow", "·"),
    "running":     ("cyan",   "⟳"),
    "paused":      ("yellow", "⏸"),
    "failed":      ("red",    "✗"),
}

# ---------------------------------------------------------------------------
# ISO helper — stdlib only
# ---------------------------------------------------------------------------

def _parse_iso(s: str) -> datetime:
    """Parse "YYYY-MM-DDTHH:MM:SSZ" (and variants) to a tz-aware UTC datetime."""
    # Handle trailing Z and +00:00 offset variants
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.strptime(s.replace("+00:00", ""), "%Y-%m-%dT%H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "config" / "defaults.json"
_DEFAULT_STALE_SECONDS = 900


def _stale_threshold() -> int:
    """Read stale_tracking_seconds from config/defaults.json; fallback 900."""
    try:
        cfg = c.read_json(_DEFAULTS_PATH)
        return int(cfg.get("stale_tracking_seconds", _DEFAULT_STALE_SECONDS))
    except (FileNotFoundError, KeyError, ValueError, OSError):
        return _DEFAULT_STALE_SECONDS


# ---------------------------------------------------------------------------
# Pure data layer — FRAME-DATA CONTRACT
# ---------------------------------------------------------------------------

def gather_frame_data(
    home: Path,
    run_id: str,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the frame data dict from run state files.

    No wall clock calls. `now` resolution: explicit arg → run["updated_at"].
    If neither resolves, stale/age fields are None.
    """
    rd = c.run_dir(home, run_id)
    run_path = rd / "run.json"
    if not run_path.exists():
        c.fail(f"no run.json for run_id={run_id!r}")

    run: dict[str, Any] = c.read_json(run_path)
    tasks: list[dict[str, Any]] = c.read_jsonl(rd / "tasks.jsonl")
    audit_rows: list[dict[str, Any]] = c.read_jsonl(rd / "audit.jsonl")
    summary = audit.summarize(home, run_id)

    # --- resolve now ---------------------------------------------------------

    now_iso: str | None = now or run.get("updated_at")
    now_dt: datetime | None = None
    if now_iso:
        try:
            now_dt = _parse_iso(now_iso)
        except (ValueError, AttributeError):
            now_dt = None

    # --- progress counts -----------------------------------------------------

    status_counts: dict[str, int] = {}
    for t in tasks:
        st = t.get("status", "pending")
        status_counts[st] = status_counts.get(st, 0) + 1

    total = len(tasks)
    done = status_counts.get("done", 0)
    in_progress = status_counts.get("in-progress", 0)
    blocked = status_counts.get("blocked", 0)
    pending = sum(
        status_counts.get(k, 0)
        for k in ("pending", "planning")
    )
    pct = round(100 * done / total, 1) if total else 0.0

    # --- model-by-agent from audit events ------------------------------------

    model_by_agent: dict[str, str] = {}
    agent_task_map: dict[str, str] = {}
    for r in audit_rows:
        aid = r.get("agent_id")
        mdl = r.get("model")
        tid = r.get("task_id")
        if aid and mdl and aid not in model_by_agent:
            model_by_agent[aid] = mdl
        if aid and tid and aid not in agent_task_map:
            agent_task_map[aid] = tid

    task_model_map: dict[str, str | None] = {
        t.get("task_id", ""): t.get("model") for t in tasks
    }

    # --- stale threshold -----------------------------------------------------

    stale_threshold = _stale_threshold()

    # --- tasks with merged token/duration ------------------------------------

    by_task = summary["by_task"]
    enriched_tasks: list[dict[str, Any]] = []
    for t in tasks:
        tid = t.get("task_id", "")
        agg = by_task.get(tid, {})
        row = dict(t)
        row["tokens"] = agg.get("tokens", 0)
        row["duration_ms"] = agg.get("duration_ms", 0)
        enriched_tasks.append(row)

    # --- agents: walk agents/<id>/tracking.json dirs -------------------------

    by_agent = summary["by_agent"]
    agents_dir = rd / "agents"
    enriched_agents: list[dict[str, Any]] = []
    if agents_dir.exists():
        for sub in sorted(agents_dir.iterdir()):
            jp = sub / "tracking.json"
            if not jp.exists():
                continue
            agent = dict(c.read_json(jp))
            aid = agent.get("agent_id", "")
            agg = by_agent.get(aid, {})
            agent["tokens"] = agg.get("tokens", 0)
            agent["duration_ms"] = agg.get("duration_ms", 0)

            # Resolve model: agent's own audit events first, then its task's model.
            tid_for_agent = agent_task_map.get(aid) or agent.get("task_id")
            agent["model"] = (
                model_by_agent.get(aid)
                or task_model_map.get(tid_for_agent or "", None)
                or None
            )

            # Staleness
            if now_dt is not None:
                upd = agent.get("updated_at")
                if upd:
                    try:
                        age_s = (now_dt - _parse_iso(upd)).total_seconds()
                        agent["age_seconds"] = age_s
                        agent["stale"] = (
                            age_s > stale_threshold
                            and agent.get("status") == "in-progress"
                        )
                    except (ValueError, AttributeError):
                        agent["age_seconds"] = None
                        agent["stale"] = None
                else:
                    agent["age_seconds"] = None
                    agent["stale"] = None
            else:
                agent["age_seconds"] = None
                agent["stale"] = None

            enriched_agents.append(agent)

    return {
        "run": run,
        "now": now_iso,
        "progress": {
            "done": done,
            "total": total,
            "in_progress": in_progress,
            "blocked": blocked,
            "pending": pending,
            "pct": pct,
        },
        "totals": {
            "tokens": summary["total_tokens"],
            "duration_ms": summary["total_duration_ms"],
        },
        "tasks": enriched_tasks,
        "agents": enriched_agents,
        "stale_threshold_seconds": stale_threshold,
    }


# ---------------------------------------------------------------------------
# Render paths
# ---------------------------------------------------------------------------

def build_frame(data: dict[str, Any]) -> "str | Any":
    """Dispatcher: rich if available, plain otherwise."""
    return _build_frame_rich(data) if HAS_RICH else build_frame_plain(data)


def build_frame_plain(data: dict[str, Any]) -> str:
    """Render a plain-text frame. Public so selftest and --plain can call it."""
    run = data["run"]
    prog = data["progress"]
    totals = data["totals"]
    tasks = data["tasks"]
    agents = data["agents"]

    run_id = run.get("run_id", "")
    goal = (run.get("goal", "") or "")[:60]
    strategy = run.get("strategy", "-")
    status = run.get("status", "")
    updated = run.get("updated_at", "-")

    _, status_glyph = STATUS_STYLE.get(status, ("", status))

    lines: list[str] = []

    # Header
    lines.append("=" * 72)
    lines.append(f"  Loopita ⟲  {run_id}")
    lines.append(f"  Goal:     {goal}")
    lines.append(f"  Strategy: {strategy}  Status: {status_glyph} {status}  Updated: {updated}")
    lines.append("=" * 72)
    lines.append("")

    # Progress bar (plain text)
    total = prog["total"]
    done = prog["done"]
    bar_width = 30
    filled = int(bar_width * done / total) if total else 0
    bar = "[" + "#" * filled + "-" * (bar_width - filled) + "]"
    lines.append(
        f"  {bar} {done}/{total} tasks ({prog['pct']}%)"
        f" · in-progress {prog['in_progress']}"
        f" · blocked {prog['blocked']}"
        f" · pending {prog['pending']}"
        f" · tokens {report._fmt_tokens(totals['tokens'])}"
        f" · elapsed {report._fmt_ms(totals['duration_ms'])}"
    )
    lines.append("")

    # Tasks table
    lines.append("  Tasks")
    lines.append("  " + "-" * 68)
    lines.append(f"  {'Task':<22} {'Technique':<10} {'Model':<10} {'St':<3} {'Tokens':>8} {'Elapsed':>8}")
    lines.append("  " + "-" * 68)
    for t in tasks:
        tid = t.get("task_id", "")
        title = t.get("title", "")
        label = f"{tid}: {title}"[:21]
        technique = (t.get("strategy") or "-")[:9]
        model = (t.get("model") or "-")[:9]
        st = t.get("status", "")
        _, glyph = STATUS_STYLE.get(st, ("", st[:2]))
        tokens_str = report._fmt_tokens(t.get("tokens"))
        elapsed_str = report._fmt_ms(t.get("duration_ms"))
        lines.append(
            f"  {label:<22} {technique:<10} {model:<10} {glyph:<3} {tokens_str:>8} {elapsed_str:>8}"
        )
    lines.append("")

    # Agents table
    lines.append("  Agents")
    lines.append("  " + "-" * 80)
    lines.append(
        f"  {'Agent':<18} {'Task':<6} {'Status':<12} {'Model':<10} {'Tokens':>8} {'Elapsed':>8}  Note"
    )
    lines.append("  " + "-" * 80)
    for a in agents:
        aid = (a.get("agent_id") or "")[:17]
        tid = (a.get("task_id") or "-")[:5]
        st = a.get("status", "")
        _, glyph = STATUS_STYLE.get(st, ("", st[:2]))
        stale_flag = " ⚠ stale" if a.get("stale") else ""
        status_cell = f"{glyph} {st}{stale_flag}"[:11]
        model = (a.get("model") or "-")[:9]
        tokens_str = report._fmt_tokens(a.get("tokens"))
        elapsed_str = report._fmt_ms(a.get("duration_ms"))
        # Note: last progress_notes entry, or first blocker if blocked
        notes = a.get("progress_notes") or []
        blockers = a.get("blockers") or []
        if st == "blocked" and blockers:
            note = blockers[0][:30]
        elif notes:
            note = notes[-1][:30]
        else:
            note = ""
        lines.append(
            f"  {aid:<18} {tid:<6} {status_cell:<12} {model:<10} {tokens_str:>8} {elapsed_str:>8}  {note}"
        )
    lines.append("")

    return "\n".join(lines)


def _build_frame_rich(data: dict[str, Any]) -> "Group":
    """Render a rich Group. Only called when HAS_RICH is True."""
    run = data["run"]
    prog = data["progress"]
    totals = data["totals"]
    tasks = data["tasks"]
    agents = data["agents"]

    run_id = run.get("run_id", "")
    goal = (run.get("goal", "") or "")[:60]
    strategy = run.get("strategy", "-")
    status = run.get("status", "")
    updated = run.get("updated_at", "-")

    color, glyph = STATUS_STYLE.get(status, ("white", status))

    # Header text inside a Panel
    header_text = Text()
    header_text.append(f"{run_id}", style="bold")
    header_text.append(f"  {goal}\n", style="dim")
    header_text.append(f"Strategy: {strategy}  ", style="")
    header_text.append(f"{glyph} {status}", style=color)
    header_text.append(f"  Updated: {updated}", style="dim")

    header_panel = Panel(header_text, title="Loopita ⟲", border_style="blue")

    # Progress bar
    total = prog["total"]
    done = prog["done"]
    prog_bar = ProgressBar(total=total or 1, completed=done, width=40)
    prog_suffix = Text(
        f"  {done}/{total} tasks ({prog['pct']}%)"
        f" · in-progress {prog['in_progress']}"
        f" · blocked {prog['blocked']}"
        f" · pending {prog['pending']}"
        f" · tokens {report._fmt_tokens(totals['tokens'])}"
        f" · elapsed {report._fmt_ms(totals['duration_ms'])}"
    )

    # Tasks table
    tasks_table = Table(
        "Task", "Technique", "Model", "Status", "Tokens", "Elapsed",
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
    )
    for t in tasks:
        tid = t.get("task_id", "")
        title = t.get("title", "")
        label = f"{tid}: {title}"[:40]
        technique = t.get("strategy") or "-"
        model = t.get("model") or "-"
        st = t.get("status", "")
        clr, glyph = STATUS_STYLE.get(st, ("white", st[:2]))
        status_cell = Text(f"{glyph} {st}", style=clr)
        tasks_table.add_row(
            label,
            technique,
            model,
            status_cell,
            report._fmt_tokens(t.get("tokens")),
            report._fmt_ms(t.get("duration_ms")),
        )

    tasks_panel = Panel(tasks_table, title="Tasks", border_style="dim")

    # Agents table
    agents_table = Table(
        "Agent", "Task", "Status", "Model", "Tokens", "Elapsed", "Note",
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
    )
    for a in agents:
        aid = a.get("agent_id") or ""
        tid = a.get("task_id") or "-"
        st = a.get("status", "")
        clr, glyph = STATUS_STYLE.get(st, ("white", st[:2]))
        stale_suffix = " ⚠ stale" if a.get("stale") else ""
        status_cell = Text(f"{glyph} {st}{stale_suffix}", style=clr)
        model = a.get("model") or "-"
        notes = a.get("progress_notes") or []
        blockers = a.get("blockers") or []
        if st == "blocked" and blockers:
            note = blockers[0][:50]
        elif notes:
            note = notes[-1][:50]
        else:
            note = ""
        agents_table.add_row(
            aid,
            tid,
            status_cell,
            model,
            report._fmt_tokens(a.get("tokens")),
            report._fmt_ms(a.get("duration_ms")),
            note,
        )

    agents_panel = Panel(agents_table, title="Agents", border_style="dim")

    return Group(header_panel, prog_bar, prog_suffix, tasks_panel, agents_panel)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_frame(args: argparse.Namespace, home: Path) -> None:
    run_id = args.run_id
    now = getattr(args, "now", None)
    plain = getattr(args, "plain", False)
    width = getattr(args, "width", None)

    data = gather_frame_data(home, run_id, now=now)
    if plain or not HAS_RICH:
        print(build_frame_plain(data))
    else:
        frame = _build_frame_rich(data)
        Console(width=width).print(frame)


def cmd_json(args: argparse.Namespace, home: Path) -> None:
    now = getattr(args, "now", None)
    c.emit(gather_frame_data(home, args.run_id, now=now))


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def _selftest(home: Path) -> None:
    rid = "run-dash-test"
    rd = c.run_dir(home, rid)
    (rd / "agents").mkdir(parents=True, exist_ok=True)

    # run.json
    c.write_json(rd / "run.json", {
        "run_id": rid,
        "goal": "build dashboard renderer",
        "strategy": "swarm",
        "status": "running",
        "config": {},
        "created_at": "2026-06-24T17:30:00Z",
        "updated_at": "2026-06-24T17:55:00Z",
    })

    # tasks.jsonl — one done, one in-progress
    for task in [
        {
            "task_id": "t1", "title": "write render.py", "scope": "scripts/render.py",
            "strategy": "swarm", "model": "haiku", "status": "done",
            "agent_ids": ["a1"], "depends_on": [], "signal_summary": "done",
            "created_at": "2026-06-24T17:30:00Z", "updated_at": "2026-06-24T17:50:00Z",
        },
        {
            "task_id": "t2", "title": "write selftest", "scope": "scripts/render.py#selftest",
            "strategy": "swarm", "model": "haiku", "status": "in-progress",
            "agent_ids": ["a2"], "depends_on": ["t1"], "signal_summary": None,
            "created_at": "2026-06-24T17:30:00Z", "updated_at": "2026-06-24T17:55:00Z",
        },
    ]:
        c.append_jsonl(rd / "tasks.jsonl", task)

    # agent a1: done, normal
    c.write_json(
        rd / "agents" / "a1" / "tracking.json",
        {
            "agent_id": "a1", "task_id": "t1", "scope": "scripts/render.py",
            "status": "done",
            "progress_notes": ["wrote render.py", "selftest passed"],
            "blockers": [], "escalation": None, "signal": "render.py written",
            "started_at": "2026-06-24T17:31:00Z",
            "updated_at": "2026-06-24T17:50:00Z",
        },
    )
    # agent a2: in-progress, old updated_at — should go stale vs now=18:00
    (rd / "agents" / "a2").mkdir(parents=True, exist_ok=True)
    c.write_json(
        rd / "agents" / "a2" / "tracking.json",
        {
            "agent_id": "a2", "task_id": "t2", "scope": "selftest",
            "status": "in-progress",
            "progress_notes": ["started"],
            "blockers": [], "escalation": None, "signal": None,
            "started_at": "2026-06-24T17:00:00Z",
            "updated_at": "2026-06-24T17:00:00Z",   # 1h before now → stale
        },
    )

    # audit.jsonl — one signal event with tokens + model
    c.append_jsonl(rd / "audit.jsonl", {
        "ts": "2026-06-24T17:50:00Z",
        "run_id": rid,
        "agent_id": "a1",
        "task_id": "t1",
        "event": "signal",
        "strategy": "swarm",
        "model": "haiku",
        "tokens": 84852,
        "duration_ms": 23332,
        "note": "render.py done",
    })

    # --- gather_frame_data ---------------------------------------------------

    data = gather_frame_data(home, rid, now="2026-06-24T18:00:00Z")

    prog = data["progress"]
    assert prog["done"] == 1, prog
    assert prog["total"] == 2, prog
    assert prog["in_progress"] == 1, prog
    assert data["totals"]["tokens"] == 84852, data["totals"]
    assert any(a["stale"] for a in data["agents"]), [a["stale"] for a in data["agents"]]

    # --- plain render --------------------------------------------------------

    plain = build_frame_plain(data)
    assert isinstance(plain, str), type(plain)
    assert rid in plain, "run_id missing from plain output"
    assert "84,852" in plain, "token count missing from plain output"
    assert "✓" in plain, "done glyph missing from plain output"

    # --- rich render (if available) ------------------------------------------

    if HAS_RICH:
        buf = io.StringIO()
        con = Console(file=buf, width=100, force_terminal=False)
        rich_frame = _build_frame_rich(data)
        con.print(rich_frame)
        rich_out = buf.getvalue()
        assert rid in rich_out, "run_id missing from rich output"
        assert "84,852" in rich_out, "token count missing from rich output"

    # --- dispatcher fallback -------------------------------------------------

    import sys as _sys
    this_module = _sys.modules[__name__]
    saved = this_module.HAS_RICH  # type: ignore[attr-defined]
    try:
        this_module.HAS_RICH = False  # type: ignore[attr-defined]
        result = build_frame(data)
        assert isinstance(result, str), type(result)
    finally:
        this_module.HAS_RICH = saved


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # Carry --home on a parent parser so it is accepted both before AND
    # after the subcommand (argparse only honors top-level options that
    # appear before the verb).
    home_parent = argparse.ArgumentParser(add_help=False)
    home_parent.add_argument("--home", default=argparse.SUPPRESS,
                             help="override $LOOPITA_HOME")

    p = argparse.ArgumentParser(description="Loopita run dashboard frame renderer",
                                parents=[home_parent])
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("frame", parents=[home_parent],
                       help="render and print one dashboard frame")
    s.add_argument("--run-id", required=True, dest="run_id")
    s.add_argument("--now", default=None,
                   help="ISO-8601 UTC timestamp to use as 'now' for stale detection")
    s.add_argument("--plain", action="store_true",
                   help="force plain-text output even when rich is installed")
    s.add_argument("--width", type=int, default=None,
                   help="console width (rich mode only)")
    s.set_defaults(func=cmd_frame)

    s = sub.add_parser("json", parents=[home_parent],
                       help="emit the frame data dict as JSON")
    s.add_argument("--run-id", required=True, dest="run_id")
    s.add_argument("--now", default=None,
                   help="ISO-8601 UTC timestamp to use as 'now' for stale detection")
    s.set_defaults(func=cmd_json)

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

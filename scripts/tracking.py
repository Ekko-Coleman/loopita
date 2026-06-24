#!/usr/bin/env python3
"""Loopita agent tracking files.

Each agent maintains both tracking.json (machine mirror) and tracking.md
(human/grep readable). This helper keeps them consistent: every write
updates the JSON and regenerates the Markdown from it. See conventions.md
section 4.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import _common as c

STATUSES = {"pending", "in-progress", "done", "blocked"}


def _json_path(home: Path, run_id: str, agent_id: str) -> Path:
    return c.agent_dir(home, run_id, agent_id) / "tracking.json"


def _md_path(home: Path, run_id: str, agent_id: str) -> Path:
    return c.agent_dir(home, run_id, agent_id) / "tracking.md"


def _render_md(t: dict[str, Any]) -> str:
    notes = t.get("progress_notes") or []
    blockers = t.get("blockers") or []
    progress = "\n".join(f"- {n}" for n in notes) if notes else "- (none)"
    block_md = "\n".join(f"- {b}" for b in blockers) if blockers else "- (none)"
    escalation = t.get("escalation")
    esc_md = f"- {escalation}" if escalation else "- (none)"
    signal = t.get("signal")
    sig_md = f"- {signal}" if signal else "- (pending)"
    return (
        f"# Agent: {t['agent_id']}  (task {t['task_id']})\n"
        f"- **scope:** {t.get('scope', '')}\n"
        f"- **status:** {t.get('status', '')}\n"
        f"- **started:** {t.get('started_at', '')}\n"
        f"- **updated:** {t.get('updated_at', '')}\n"
        f"\n## Progress\n{progress}\n"
        f"\n## Blockers\n{block_md}\n"
        f"\n## Escalation\n{esc_md}\n"
        f"\n## Signal\n{sig_md}\n"
    )


def _persist(home: Path, t: dict[str, Any]) -> None:
    """Write the JSON mirror and regenerate the Markdown from it."""
    run_id, agent_id = t["__run_id"], t["agent_id"]
    payload = {k: v for k, v in t.items() if not k.startswith("__")}
    c.write_json(_json_path(home, run_id, agent_id), payload)
    c.write_text(_md_path(home, run_id, agent_id), _render_md(payload))


def cmd_create(args: argparse.Namespace, home: Path) -> None:
    t = {
        "agent_id": args.agent_id,
        "task_id": args.task_id,
        "scope": args.scope,
        "status": "pending",
        "progress_notes": [],
        "blockers": [],
        "escalation": None,
        "signal": None,
        "started_at": args.started_at,
        "updated_at": args.started_at,
        "__run_id": args.run_id,
    }
    _persist(home, t)
    c.emit({"ok": True, "agent_id": args.agent_id})


def cmd_update(args: argparse.Namespace, home: Path) -> None:
    path = _json_path(home, args.run_id, args.agent_id)
    if not path.exists():
        c.fail(f"no tracking.json for agent_id={args.agent_id!r}")
    t = c.read_json(path)
    t["__run_id"] = args.run_id
    if args.status is not None:
        if args.status not in STATUSES:
            c.fail(f"invalid status {args.status!r}")
        t["status"] = args.status
    if args.add_note is not None:
        t.setdefault("progress_notes", []).append(args.add_note)
    if args.add_blocker is not None:
        t.setdefault("blockers", []).append(args.add_blocker)
    if args.escalation is not None:
        # Allow clearing with the literal string "null".
        t["escalation"] = None if args.escalation == "null" else args.escalation
    if args.signal is not None:
        t["signal"] = None if args.signal == "null" else args.signal
    t["updated_at"] = args.updated_at
    _persist(home, t)
    c.emit({"ok": True, "agent_id": args.agent_id})


def cmd_get(args: argparse.Namespace, home: Path) -> None:
    path = _json_path(home, args.run_id, args.agent_id)
    if not path.exists():
        c.fail(f"no tracking.json for agent_id={args.agent_id!r}")
    c.emit(c.read_json(path))


def _all_agents(home: Path, run_id: str) -> list[dict[str, Any]]:
    agents_dir = c.run_dir(home, run_id) / "agents"
    out: list[dict[str, Any]] = []
    if not agents_dir.exists():
        return out
    for sub in sorted(agents_dir.iterdir()):
        jp = sub / "tracking.json"
        if jp.exists():
            out.append(c.read_json(jp))
    return out


def cmd_query(args: argparse.Namespace, home: Path) -> None:
    rows = _all_agents(home, args.run_id)
    if args.status is not None:
        rows = [r for r in rows if r.get("status") == args.status]
    c.emit(rows)


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp (trailing Z supported)."""
    v = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def cmd_stale(args: argparse.Namespace, home: Path) -> None:
    now = _parse_iso(args.now)
    threshold = args.threshold_seconds
    stale: list[str] = []
    for t in _all_agents(home, args.run_id):
        if t.get("status") != "in-progress":
            continue
        updated = t.get("updated_at")
        if not updated:
            continue
        age = (now - _parse_iso(updated)).total_seconds()
        if age > threshold:
            stale.append(t["agent_id"])
    c.emit(stale)


def _selftest(home: Path) -> None:
    import io
    import json as _json

    rid = "run-test"
    ns = argparse.Namespace
    cmd_create(ns(run_id=rid, agent_id="login-builder", task_id="t1",
                  scope="auth/login.py", started_at="2026-06-24T17:31:00Z"), home)
    jp = _json_path(home, rid, "login-builder")
    mp = _md_path(home, rid, "login-builder")
    t = c.read_json(jp)
    assert t["status"] == "pending"
    assert "# Agent: login-builder" in mp.read_text()

    cmd_update(ns(run_id=rid, agent_id="login-builder", status="in-progress",
                  add_note="wrote endpoint", add_blocker=None,
                  escalation=None, signal=None,
                  updated_at="2026-06-24T17:42:00Z"), home)
    cmd_update(ns(run_id=rid, agent_id="login-builder", status=None,
                  add_note="added tests", add_blocker="needs db fixture",
                  escalation="not parallel after all", signal="done: 3 tests",
                  updated_at="2026-06-24T17:50:00Z"), home)
    t = c.read_json(jp)
    assert t["progress_notes"] == ["wrote endpoint", "added tests"], t
    assert t["blockers"] == ["needs db fixture"], t
    assert t["escalation"] == "not parallel after all"
    assert t["signal"] == "done: 3 tests"
    md = mp.read_text()
    assert "wrote endpoint" in md and "needs db fixture" in md
    assert "not parallel after all" in md and "done: 3 tests" in md

    # clearing escalation via literal "null"; also mark this agent done so the
    # in-progress query below isolates the other agent.
    cmd_update(ns(run_id=rid, agent_id="login-builder", status="done",
                  add_note=None, add_blocker=None, escalation="null",
                  signal=None, updated_at="2026-06-24T17:51:00Z"), home)
    assert c.read_json(jp)["escalation"] is None
    assert c.read_json(jp)["status"] == "done"

    # second agent for query/stale
    cmd_create(ns(run_id=rid, agent_id="test-runner", task_id="t2",
                  scope="tests/", started_at="2026-06-24T17:00:00Z"), home)
    cmd_update(ns(run_id=rid, agent_id="test-runner", status="in-progress",
                  add_note=None, add_blocker=None, escalation=None,
                  signal=None, updated_at="2026-06-24T17:00:00Z"), home)

    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        cmd_query(ns(run_id=rid, status="in-progress"), home)
    finally:
        sys.stdout = old
    q = _json.loads(buf.getvalue())
    ids = {r["agent_id"] for r in q}
    assert ids == {"test-runner"}, ids  # login-builder is no longer in-progress

    # staleness: test-runner last updated 17:00, "now" 18:00, threshold 900s
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        cmd_stale(ns(run_id=rid, now="2026-06-24T18:00:00Z",
                     threshold_seconds=900), home)
    finally:
        sys.stdout = old
    stale = _json.loads(buf.getvalue())
    assert stale == ["test-runner"], stale


def build_parser() -> argparse.ArgumentParser:
    # Carry --home on a parent parser so it is accepted both before AND
    # after the subcommand (argparse only honors top-level options that
    # appear before the verb).
    home_parent = argparse.ArgumentParser(add_help=False)
    home_parent.add_argument("--home", default=argparse.SUPPRESS,
                             help="override $LOOPITA_HOME")

    p = argparse.ArgumentParser(description="Loopita agent tracking files", parents=[home_parent])
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("create", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--agent-id", required=True, dest="agent_id")
    s.add_argument("--task-id", required=True, dest="task_id")
    s.add_argument("--scope", required=True)
    s.add_argument("--started-at", required=True, dest="started_at")
    s.set_defaults(func=cmd_create)

    s = sub.add_parser("update", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--agent-id", required=True, dest="agent_id")
    s.add_argument("--status", default=None)
    s.add_argument("--add-note", default=None, dest="add_note")
    s.add_argument("--add-blocker", default=None, dest="add_blocker")
    s.add_argument("--escalation", default=None)
    s.add_argument("--signal", default=None)
    s.add_argument("--updated-at", required=True, dest="updated_at")
    s.set_defaults(func=cmd_update)

    s = sub.add_parser("get", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--agent-id", required=True, dest="agent_id")
    s.set_defaults(func=cmd_get)

    s = sub.add_parser("query", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--status", default=None)
    s.set_defaults(func=cmd_query)

    s = sub.add_parser("stale", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--now", required=True)
    s.add_argument("--threshold-seconds", required=True, type=int,
                   dest="threshold_seconds")
    s.set_defaults(func=cmd_stale)

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

#!/usr/bin/env python3
"""Loopita two-tier learnings store.

Tier-1 (session) lives in runs/<run-id>/learnings.md and is fed back during
the run. Tier-2 (persistent) lives in learnings/persistent.md and is read at
the next invocation. Each learning is a single Markdown bullet tagged with
its level: `- [level] text`. See conventions.md section 6.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import _common as c

# Matches a tagged learning bullet: "- [level] text"
_BULLET = re.compile(r"^- \[(?P<level>[^\]]+)\]\s*(?P<text>.*)$")


def _level_ok(level: str) -> bool:
    return level == "orchestration" or level.startswith("agent:")


def _scope_path(home: Path, scope: str, run_id: str | None) -> Path:
    if scope == "session":
        if not run_id:
            c.fail("--run-id is required for --scope session")
        return c.run_dir(home, run_id) / "learnings.md"
    if scope == "persistent":
        return c.persistent_learnings_path(home)
    c.fail(f"invalid scope {scope!r}")


def _append_bullet(path: Path, level: str, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    existing += f"- [{level}] {text}\n"
    c.write_text(path, existing)


def cmd_add(args: argparse.Namespace, home: Path) -> None:
    if not _level_ok(args.level):
        c.fail(f"invalid level {args.level!r}; expected 'orchestration' or 'agent:NAME'")
    if args.scope not in ("session", "persistent"):
        c.fail(f"invalid scope {args.scope!r}")
    path = _scope_path(home, args.scope, args.run_id)
    _append_bullet(path, args.level, args.text)
    c.emit({"ok": True, "level": args.level, "scope": args.scope})


def _parse_file(path: Path, scope: str) -> list[dict[str, str]]:
    if not path.exists():
        return []
    out: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _BULLET.match(line.strip())
        if m:
            out.append({
                "level": m.group("level"),
                "text": m.group("text").strip(),
                "scope": scope,
            })
    return out


def _collect(home: Path, scope: str | None, run_id: str | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if scope in (None, "session"):
        if run_id:
            out.extend(_parse_file(c.run_dir(home, run_id) / "learnings.md", "session"))
    if scope in (None, "persistent"):
        out.extend(_parse_file(c.persistent_learnings_path(home), "persistent"))
    return out


def cmd_list(args: argparse.Namespace, home: Path) -> None:
    rows = _collect(home, args.scope, args.run_id)
    if args.level_prefix:
        rows = [r for r in rows if r["level"].startswith(args.level_prefix)]
    c.emit(rows)


def cmd_apply(args: argparse.Namespace, home: Path) -> None:
    """Persistent learnings the orchestrator bakes into strategy at run start."""
    c.emit(_parse_file(c.persistent_learnings_path(home), "persistent"))


def _selftest(home: Path) -> None:
    import io
    import json as _json

    rid = "run-test"
    ns = argparse.Namespace
    cmd_add(ns(level="orchestration", text="sequence code review here",
               scope="session", run_id=rid), home)
    cmd_add(ns(level="agent:test-runner", text="activate the venv first",
               scope="session", run_id=rid), home)
    cmd_add(ns(level="orchestration", text="swarm conflicts on this repo",
               scope="persistent", run_id=None), home)

    session_file = c.run_dir(home, rid) / "learnings.md"
    assert "- [orchestration] sequence code review here" in session_file.read_text()
    persistent = c.persistent_learnings_path(home)
    assert "- [orchestration] swarm conflicts on this repo" in persistent.read_text()

    def run(fn, **kw):
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            fn(ns(**kw), home)
        finally:
            sys.stdout = old
        return _json.loads(buf.getvalue())

    all_session = run(cmd_list, scope="session", run_id=rid, level_prefix=None)
    assert len(all_session) == 2, all_session
    agent_only = run(cmd_list, scope="session", run_id=rid, level_prefix="agent:")
    assert len(agent_only) == 1 and agent_only[0]["level"] == "agent:test-runner"

    applied = run(cmd_apply, run_id=None)
    assert len(applied) == 1 and applied[0]["scope"] == "persistent", applied

    combined = run(cmd_list, scope=None, run_id=rid, level_prefix=None)
    assert len(combined) == 3, combined


def build_parser() -> argparse.ArgumentParser:
    # Carry --home on a parent parser so it is accepted both before AND
    # after the subcommand (argparse only honors top-level options that
    # appear before the verb).
    home_parent = argparse.ArgumentParser(add_help=False)
    home_parent.add_argument("--home", default=argparse.SUPPRESS,
                             help="override $LOOPITA_HOME")

    p = argparse.ArgumentParser(description="Loopita two-tier learnings store", parents=[home_parent])
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("add", parents=[home_parent])
    s.add_argument("--level", required=True, help="orchestration | agent:NAME")
    s.add_argument("--text", required=True)
    s.add_argument("--scope", required=True, choices=["session", "persistent"])
    s.add_argument("--run-id", default=None, dest="run_id")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("list", parents=[home_parent])
    s.add_argument("--scope", default=None, choices=["session", "persistent"])
    s.add_argument("--run-id", default=None, dest="run_id")
    s.add_argument("--level-prefix", default=None, dest="level_prefix")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("apply", parents=[home_parent])
    s.set_defaults(func=cmd_apply, run_id=None)

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

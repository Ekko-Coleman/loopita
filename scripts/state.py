#!/usr/bin/env python3
"""Loopita run/task/context state.

Manages run.json (run metadata), tasks.jsonl (task list), and
snapshots/<n>.md (orchestrator context offloads), plus a grep across the
run dir. See references/conventions.md sections 2, 3, 8.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import _common as c

RUN_STATUSES = {"planning", "running", "paused", "done", "failed"}
TASK_STATUSES = {"pending", "in-progress", "done", "blocked"}
STRATEGIES = {"linear", "loop", "swarm"}
CHECKPOINT_REASONS = {"user-pause", "quota", "crash"}
MERGE_STATUSES = {"pending", "in-progress", "done", "n/a"}


def _run_json_path(home: Path, run_id: str) -> Path:
    return c.run_dir(home, run_id) / "run.json"


def _tasks_path(home: Path, run_id: str) -> Path:
    return c.run_dir(home, run_id) / "tasks.jsonl"


def _checkpoint_path(home: Path, run_id: str) -> Path:
    return c.run_dir(home, run_id) / "checkpoint.json"


def cmd_init(args: argparse.Namespace, home: Path) -> None:
    rd = c.run_dir(home, args.run_id)
    (rd / "agents").mkdir(parents=True, exist_ok=True)
    (rd / "snapshots").mkdir(parents=True, exist_ok=True)

    config: dict[str, Any] = {}
    if args.config:
        config = json.loads(args.config)

    run = {
        "run_id": args.run_id,
        "goal": args.goal,
        "strategy": args.strategy,
        "status": "planning",
        "config": config,
        "created_at": args.created_at,
        "updated_at": args.created_at,
    }
    c.write_json(_run_json_path(home, args.run_id), run)
    # Touch tasks.jsonl so downstream reads find an empty list, not a miss.
    tasks = _tasks_path(home, args.run_id)
    if not tasks.exists():
        c.write_text(tasks, "")
    c.emit({"ok": True, "run_id": args.run_id, "path": str(rd)})


def cmd_set_run(args: argparse.Namespace, home: Path) -> None:
    path = _run_json_path(home, args.run_id)
    if not path.exists():
        c.fail(f"no run.json for run_id={args.run_id!r}")
    run = c.read_json(path)
    if args.status is not None:
        if args.status not in RUN_STATUSES:
            c.fail(f"invalid status {args.status!r}")
        run["status"] = args.status
    if args.strategy is not None:
        if args.strategy not in STRATEGIES:
            c.fail(f"invalid strategy {args.strategy!r}")
        run["strategy"] = args.strategy
    run["updated_at"] = args.updated_at
    c.write_json(path, run)
    c.emit({"ok": True, "run_id": args.run_id})


def cmd_get_run(args: argparse.Namespace, home: Path) -> None:
    path = _run_json_path(home, args.run_id)
    if not path.exists():
        c.fail(f"no run.json for run_id={args.run_id!r}")
    c.emit(c.read_json(path))


def cmd_add_task(args: argparse.Namespace, home: Path) -> None:
    task = {
        "task_id": args.task_id,
        "title": args.title,
        "scope": args.scope,
        "strategy": args.strategy,
        "status": "pending",
        "agent_ids": [],
        "depends_on": c.split_csv(args.depends_on),
        "signal_summary": None,
        "created_at": args.created_at,
        "updated_at": args.created_at,
    }
    c.upsert_jsonl(_tasks_path(home, args.run_id), task, key="task_id")
    c.emit({"ok": True, "task_id": args.task_id})


def cmd_set_task(args: argparse.Namespace, home: Path) -> None:
    path = _tasks_path(home, args.run_id)
    patch: dict[str, Any] = {"updated_at": args.updated_at}
    if args.status is not None:
        if args.status not in TASK_STATUSES:
            c.fail(f"invalid status {args.status!r}")
        patch["status"] = args.status
    if args.signal is not None:
        patch["signal_summary"] = args.signal
    if args.agent_ids is not None:
        patch["agent_ids"] = c.split_csv(args.agent_ids)
    try:
        c.patch_jsonl(path, "task_id", args.task_id, patch)
    except KeyError as exc:
        c.fail(str(exc))
    c.emit({"ok": True, "task_id": args.task_id})


def cmd_list_tasks(args: argparse.Namespace, home: Path) -> None:
    rows = c.read_jsonl(_tasks_path(home, args.run_id))
    if args.status is not None:
        rows = [r for r in rows if r.get("status") == args.status]
    c.emit(rows)


def cmd_snapshot(args: argparse.Namespace, home: Path) -> None:
    snap_dir = c.run_dir(home, args.run_id) / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    existing = [
        int(p.stem)
        for p in snap_dir.glob("*.md")
        if p.stem.isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8")
    else:
        content = args.content or ""
    path = snap_dir / f"{n}.md"
    c.write_text(path, content)
    c.emit({"ok": True, "snapshot": n, "path": str(path)})


def cmd_grep(args: argparse.Namespace, home: Path) -> None:
    rd = c.run_dir(home, args.run_id)
    if not rd.exists():
        c.fail(f"no run dir for run_id={args.run_id!r}")
    try:
        pattern = re.compile(args.pattern)
    except re.error as exc:
        c.fail(f"bad regex: {exc}")
    matches: list[dict[str, Any]] = []
    for path in sorted(rd.rglob("*")):
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line_no, line in enumerate(fh, start=1):
                    if pattern.search(line):
                        matches.append(
                            {
                                "file": str(path.relative_to(rd)),
                                "line_no": line_no,
                                "line": line.rstrip("\n"),
                            }
                        )
        except (UnicodeDecodeError, OSError):
            # Skip binary or unreadable files; the run dir is meant to be text.
            continue
    c.emit(matches)


def cmd_checkpoint(args: argparse.Namespace, home: Path) -> None:
    if args.reason not in CHECKPOINT_REASONS:
        c.fail(f"invalid reason {args.reason!r}")
    if args.strategy is not None and args.strategy not in STRATEGIES:
        c.fail(f"invalid strategy {args.strategy!r}")
    if args.merge_status is not None and args.merge_status not in MERGE_STATUSES:
        c.fail(f"invalid merge-status {args.merge_status!r}")
    checkpoint = {
        "run_id": args.run_id,
        "strategy": args.strategy,
        "reason": args.reason,
        "workflow_run_id": args.workflow_run_id,
        "open_task_ids": c.split_csv(args.open_tasks),
        "done_task_ids": c.split_csv(args.done_tasks),
        "merge_status": args.merge_status if args.merge_status is not None else "n/a",
        "saved_at": args.saved_at,
    }
    c.write_json(_checkpoint_path(home, args.run_id), checkpoint)
    c.emit({"ok": True, "run_id": args.run_id})


def cmd_get_checkpoint(args: argparse.Namespace, home: Path) -> None:
    path = _checkpoint_path(home, args.run_id)
    if not path.exists():
        c.fail(f"no checkpoint.json for run_id={args.run_id!r}")
    c.emit(c.read_json(path))


def _selftest(home: Path) -> None:
    rid = "run-20260624-103000-test"
    ns = argparse.Namespace
    cmd_init(ns(run_id=rid, goal="g", strategy="swarm",
                created_at="2026-06-24T17:30:00Z", config='{"a": 1}'), home)
    run = c.read_json(_run_json_path(home, rid))
    assert run["status"] == "planning", run
    assert run["config"] == {"a": 1}, run

    cmd_set_run(ns(run_id=rid, status="running", strategy=None,
                   updated_at="2026-06-24T17:31:00Z"), home)
    assert c.read_json(_run_json_path(home, rid))["status"] == "running"

    cmd_add_task(ns(run_id=rid, task_id="t1", title="do it", scope="x",
                    strategy="swarm", depends_on="t0,t-1",
                    created_at="2026-06-24T17:32:00Z"), home)
    cmd_add_task(ns(run_id=rid, task_id="t2", title="other", scope="y",
                    strategy="swarm", depends_on=None,
                    created_at="2026-06-24T17:32:00Z"), home)
    cmd_set_task(ns(run_id=rid, task_id="t1", status="done",
                    signal="all green", agent_ids="a1,a2",
                    updated_at="2026-06-24T17:40:00Z"), home)
    rows = c.read_jsonl(_tasks_path(home, rid))
    assert len(rows) == 2, rows
    t1 = next(r for r in rows if r["task_id"] == "t1")
    assert t1["status"] == "done"
    assert t1["signal_summary"] == "all green"
    assert t1["agent_ids"] == ["a1", "a2"]
    assert t1["depends_on"] == ["t0", "t-1"]

    # upsert replaces, never duplicates
    cmd_add_task(ns(run_id=rid, task_id="t1", title="renamed", scope="x",
                    strategy="loop", depends_on=None,
                    created_at="2026-06-24T17:33:00Z"), home)
    rows = c.read_jsonl(_tasks_path(home, rid))
    assert len(rows) == 2, rows

    snap = c.run_dir(home, rid) / "snapshots"
    cmd_snapshot(ns(run_id=rid, content="first", content_file=None), home)
    cmd_snapshot(ns(run_id=rid, content="second", content_file=None), home)
    assert (snap / "1.md").read_text() == "first"
    assert (snap / "2.md").read_text() == "second"

    # grep finds content across the run dir
    import io
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        cmd_grep(ns(run_id=rid, pattern="renamed"), home)
    finally:
        sys.stdout = old
    found = json.loads(buf.getvalue())
    assert any("tasks.jsonl" in m["file"] for m in found), found

    # checkpoint round-trip (write then read back)
    cmd_checkpoint(ns(run_id=rid, reason="quota", strategy="swarm",
                      workflow_run_id="wf_abc123",
                      open_tasks="t2,t3", done_tasks="t0,t1",
                      merge_status="pending",
                      saved_at="2026-06-24T17:50:00Z"), home)
    cp = c.read_json(_checkpoint_path(home, rid))
    assert cp == {
        "run_id": rid,
        "strategy": "swarm",
        "reason": "quota",
        "workflow_run_id": "wf_abc123",
        "open_task_ids": ["t2", "t3"],
        "done_task_ids": ["t0", "t1"],
        "merge_status": "pending",
        "saved_at": "2026-06-24T17:50:00Z",
    }, cp
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        cmd_get_checkpoint(ns(run_id=rid), home)
    finally:
        sys.stdout = old
    assert json.loads(buf.getvalue())["workflow_run_id"] == "wf_abc123"

    # optional fields default to null (strategy, workflow_run_id) / empty lists;
    # merge_status defaults to "n/a" when omitted (per conventions.md §7)
    cmd_checkpoint(ns(run_id=rid, reason="crash", strategy=None,
                      workflow_run_id=None, open_tasks=None, done_tasks=None,
                      merge_status=None, saved_at="2026-06-24T17:55:00Z"), home)
    cp2 = c.read_json(_checkpoint_path(home, rid))
    assert cp2["strategy"] is None and cp2["workflow_run_id"] is None
    assert cp2["open_task_ids"] == [] and cp2["merge_status"] == "n/a", cp2


def build_parser() -> argparse.ArgumentParser:
    # Carry --home on a parent parser so it is accepted both before AND
    # after the subcommand (argparse only honors top-level options that
    # appear before the verb).
    home_parent = argparse.ArgumentParser(add_help=False)
    home_parent.add_argument("--home", default=argparse.SUPPRESS,
                             help="override $LOOPITA_HOME")

    p = argparse.ArgumentParser(description="Loopita run/task/context state", parents=[home_parent])
    p.add_argument("--selftest", action="store_true", help="run self-test")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("init", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--goal", required=True)
    s.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    s.add_argument("--created-at", required=True, dest="created_at")
    s.add_argument("--config", default=None)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("set-run", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--status", default=None)
    s.add_argument("--strategy", default=None)
    s.add_argument("--updated-at", required=True, dest="updated_at")
    s.set_defaults(func=cmd_set_run)

    s = sub.add_parser("get-run", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.set_defaults(func=cmd_get_run)

    s = sub.add_parser("add-task", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--task-id", required=True, dest="task_id")
    s.add_argument("--title", required=True)
    s.add_argument("--scope", default=None)
    s.add_argument("--strategy", default=None)
    s.add_argument("--depends-on", default=None, dest="depends_on")
    s.add_argument("--created-at", required=True, dest="created_at")
    s.set_defaults(func=cmd_add_task)

    s = sub.add_parser("set-task", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--task-id", required=True, dest="task_id")
    s.add_argument("--status", default=None)
    s.add_argument("--signal", default=None)
    s.add_argument("--agent-ids", default=None, dest="agent_ids")
    s.add_argument("--updated-at", required=True, dest="updated_at")
    s.set_defaults(func=cmd_set_task)

    s = sub.add_parser("list-tasks", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--status", default=None)
    s.set_defaults(func=cmd_list_tasks)

    s = sub.add_parser("snapshot", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--content", default=None)
    g.add_argument("--content-file", default=None, dest="content_file")
    s.set_defaults(func=cmd_snapshot)

    s = sub.add_parser("grep", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--pattern", required=True)
    s.set_defaults(func=cmd_grep)

    s = sub.add_parser("checkpoint", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.add_argument("--reason", required=True, choices=sorted(CHECKPOINT_REASONS))
    s.add_argument("--strategy", default=None, choices=sorted(STRATEGIES))
    s.add_argument("--workflow-run-id", default=None, dest="workflow_run_id")
    s.add_argument("--open-tasks", default=None, dest="open_tasks")
    s.add_argument("--done-tasks", default=None, dest="done_tasks")
    s.add_argument("--merge-status", default=None, dest="merge_status",
                   choices=sorted(MERGE_STATUSES))
    s.add_argument("--saved-at", required=True, dest="saved_at")
    s.set_defaults(func=cmd_checkpoint)

    s = sub.add_parser("get-checkpoint", parents=[home_parent])
    s.add_argument("--run-id", required=True)
    s.set_defaults(func=cmd_get_checkpoint)

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

#!/usr/bin/env python3
"""Loopita live run monitor.

Runs in a separate terminal pane: `python scripts/monitor.py --run-id <id>`.
Uses rich.live.Live to tail a Loopita run's state files and re-render the same
dashboard frame that render.py produces. NEVER invoked by the orchestrator —
it owns its own pane.

Two cadences, decoupled on purpose:
  * the animated pulse border redraws at ANIM_FPS (smooth motion), so there is
    always a live activity signal even when no files change; and
  * the run state is re-read from disk every `--interval` seconds (the numbers
    only change when the orchestrator/agents write them — see references).
The number of racing pulses tracks the count of in-progress agents, read live
from the tracking files.

`rich` is required for this script (unlike render.py where it is optional).
If rich is not installed, a clear error message is printed.

See references/conventions.md for the file schemas this reads.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import _common as c
import render


# ---------------------------------------------------------------------------
# Clock helper — the ONE sanctioned wall-clock read in the skill.
# conventions.md: scripts otherwise never call the clock.
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time as 'YYYY-MM-DDTHH:MM:SSZ'.

    Wall-clock read — monitor is interactive/non-reproducible BY DESIGN.
    This is the ONE sanctioned clock read in the skill (conventions.md: scripts
    otherwise never call the clock). Returns 'YYYY-MM-DDTHH:MM:SSZ' UTC.
    """
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Live monitor loop
# ---------------------------------------------------------------------------

# Animation framerate and the time for one pulse to lap the border.
ANIM_FPS = 15
LAP_SECONDS = 2.5


def run_monitor(home: Path, run_id: str, interval: float) -> None:
    """Run the live dashboard.

    The border animates at ANIM_FPS regardless of file activity; the run state
    is re-read from disk every `interval` seconds. The FIRST gather_frame_data
    call (before entering Live) is NOT wrapped in the tolerant except — if the
    run_id is bad, let it fail loudly so the user gets a clear error.
    """
    if not render.HAS_RICH:
        c.fail("monitor requires the optional 'rich' package (pip install rich). "
               "For a static text view use: python scripts/render.py frame --plain --run-id <id>")

    from rich.live import Live

    frame_dt = 1.0 / ANIM_FPS

    # First gather — fail loudly if run is not found or malformed.
    data = render.gather_frame_data(home, run_id, now=_now_iso())

    start = time.monotonic()
    last_read = start

    with Live(render.build_frame(data, 0.0),
              refresh_per_second=ANIM_FPS, screen=False) as live:
        try:
            while True:
                now_m = time.monotonic()
                # Re-read state at the slower file cadence; keep last data on a
                # transient mid-write read so the animation never stutters.
                if now_m - last_read >= interval:
                    try:
                        data = render.gather_frame_data(home, run_id, now=_now_iso())
                    except (FileNotFoundError, ValueError, json.JSONDecodeError):
                        pass
                    last_read = now_m
                phase = ((now_m - start) / LAP_SECONDS) % 1.0
                live.update(render.build_frame(data, phase))
                time.sleep(frame_dt)
        except KeyboardInterrupt:
            pass  # clean Ctrl-C: Live.__exit__ restores the terminal

    c.emit({"monitor": "stopped", "run_id": run_id})


# ---------------------------------------------------------------------------
# Selftest — MUST NOT enter the live loop (it would block forever)
# ---------------------------------------------------------------------------

def _selftest(home: Path) -> None:
    """Smoke-test the monitor module without entering the live loop."""
    rid = "run-monitor-test"
    rd = c.run_dir(home, rid)
    (rd / "agents").mkdir(parents=True, exist_ok=True)

    # run.json
    c.write_json(rd / "run.json", {
        "run_id": rid,
        "goal": "selftest the monitor",
        "strategy": "swarm",
        "status": "running",
        "config": {},
        "created_at": "2026-06-24T17:30:00Z",
        "updated_at": "2026-06-24T17:55:00Z",
    })

    # tasks.jsonl — one done, one in-progress
    for task in [
        {
            "task_id": "t1", "title": "write monitor.py", "scope": "scripts/monitor.py",
            "strategy": "swarm", "model": "haiku", "status": "done",
            "agent_ids": ["a1"], "depends_on": [], "signal_summary": "done",
            "created_at": "2026-06-24T17:30:00Z", "updated_at": "2026-06-24T17:50:00Z",
        },
        {
            "task_id": "t2", "title": "write selftest", "scope": "monitor#selftest",
            "strategy": "swarm", "model": "haiku", "status": "in-progress",
            "agent_ids": ["a2"], "depends_on": ["t1"], "signal_summary": None,
            "created_at": "2026-06-24T17:30:00Z", "updated_at": "2026-06-24T17:55:00Z",
        },
    ]:
        c.append_jsonl(rd / "tasks.jsonl", task)

    # agent a1: done
    c.write_json(
        rd / "agents" / "a1" / "tracking.json",
        {
            "agent_id": "a1", "task_id": "t1", "scope": "scripts/monitor.py",
            "status": "done",
            "progress_notes": ["wrote monitor.py", "selftest passed"],
            "blockers": [], "escalation": None, "signal": "monitor.py written",
            "started_at": "2026-06-24T17:31:00Z",
            "updated_at": "2026-06-24T17:50:00Z",
        },
    )

    # agent a2: in-progress with old updated_at — goes stale vs now=18:00
    (rd / "agents" / "a2").mkdir(parents=True, exist_ok=True)
    c.write_json(
        rd / "agents" / "a2" / "tracking.json",
        {
            "agent_id": "a2", "task_id": "t2", "scope": "monitor#selftest",
            "status": "in-progress",
            "progress_notes": ["started"],
            "blockers": [], "escalation": None, "signal": None,
            "started_at": "2026-06-24T17:00:00Z",
            "updated_at": "2026-06-24T17:00:00Z",  # 1h before now → stale
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
        "tokens": 42000,
        "duration_ms": 15000,
        "note": "monitor.py done",
    })

    # Gather frame data and build a frame — proves the render reuse contract.
    data = render.gather_frame_data(home, rid, now="2026-06-24T18:00:00Z")
    frame = render.build_frame(data)
    assert frame is not None, "build_frame returned None"

    # If rich is available, render once to a buffer to prove it builds without Live.
    if render.HAS_RICH:
        import io
        from rich.console import Console

        buf = io.StringIO()
        Console(file=buf, force_terminal=False).print(frame)
        rendered = buf.getvalue()
        assert rid in rendered, f"run_id {rid!r} missing from rich output"

    # Do NOT call run_monitor — it would block forever.


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # Carry --home on a parent parser so it is accepted both before AND
    # after other args (mirrors the pattern used in render.py / report.py).
    home_parent = argparse.ArgumentParser(add_help=False)
    home_parent.add_argument("--home", default=argparse.SUPPRESS,
                             help="override $LOOPITA_HOME")

    p = argparse.ArgumentParser(
        description="Loopita live run monitor (run in a separate terminal pane)",
        parents=[home_parent],
    )
    p.add_argument("--run-id", dest="run_id", default=None,
                   help="ID of the Loopita run to monitor (required unless --selftest)")
    p.add_argument("--interval", type=float, default=0.5,
                   help="seconds between state-file re-reads (default: 0.5); "
                        "the border animation is always smooth regardless")
    p.add_argument("--selftest", action="store_true",
                   help="run internal smoke test and exit")

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    home = c.home_dir(getattr(args, "home", None))

    if args.selftest:
        c.run_selftest(_selftest)
        return

    if not args.run_id:
        parser.error("--run-id is required")

    run_monitor(home, args.run_id, args.interval)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Loopita dashboard launcher — dependency check + spawn a side terminal pane.

Two subcommands the orchestrator drives ON THE USER'S BEHALF, but only AFTER
asking the user:

  deps   [--install]        check whether the optional `rich` dependency is
                            present; with --install, install it (pip).
  launch --run-id <id>      open a side terminal pane/window running
         [--home <dir>]     `monitor.py` for the live dashboard. Detects the
         [--interval 0.25]  terminal (tmux / iTerm2 / Terminal.app) and uses
         [--dry-run]        the right mechanism; on an unknown terminal it just
         [--method ...]     prints the command for the user to paste.

HONESTY / CONSENT BOUNDARY: this script performs mechanics only. Opening a
window and installing a package are outward/irreversible-ish actions, so the
ORCHESTRATOR (SKILL.md) must ASK the user before calling `launch` or
`deps --install`. See references/tui-dashboard.md.

Like every Loopita helper: `python scripts/dashboard.py [--home <dir>] <cmd>`,
prints JSON, supports --help and --selftest.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

import _common as c

METHODS = ("auto", "tmux", "iterm", "terminal", "print")
_DETECTED = ("tmux", "iterm", "terminal", "print")


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def _install_cmd() -> str:
    return f"{shlex.quote(sys.executable)} -m pip install rich"


def _has_rich() -> bool:
    """True if `rich` is importable by the interpreter that runs the scripts.

    Checked in a subprocess so a prior failed import in this process can't
    poison the result (and so it reflects the same interpreter monitor.py uses).
    """
    return subprocess.run(
        [sys.executable, "-c", "import rich"],
        capture_output=True,
    ).returncode == 0


def _install_rich() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "rich"],
        capture_output=True, text=True,
    )
    return {
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-2000:],
    }


def cmd_deps(args: argparse.Namespace, home: Path) -> None:
    have = _has_rich()
    out: dict = {"rich": have, "python": sys.executable, "install_cmd": _install_cmd()}
    if getattr(args, "install", False) and not have:
        res = _install_rich()
        out["install_attempted"] = True
        out["install_returncode"] = res["returncode"]
        out["install_stdout"] = res["stdout"]
        out["install_stderr"] = res["stderr"]
        out["rich"] = _has_rich()  # re-check after the attempt
    c.emit(out)


# ---------------------------------------------------------------------------
# Launch helpers
# ---------------------------------------------------------------------------

def _monitor_cmd(home: Path, run_id: str, interval: float) -> str:
    """The shell command that runs the live monitor (absolute paths, quoted)."""
    monitor = Path(__file__).resolve().parent / "monitor.py"
    return (
        f"{shlex.quote(sys.executable)} {shlex.quote(str(monitor))} "
        f"--home {shlex.quote(str(home))} --run-id {shlex.quote(run_id)} "
        f"--interval {interval}"
    )


def _detect_method() -> str:
    """Best-effort detection of the surrounding terminal."""
    if os.environ.get("TMUX"):
        return "tmux"
    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program == "iTerm.app":
        return "iterm"
    if term_program == "Apple_Terminal":
        return "terminal"
    return "print"


def _applescript_quote(s: str) -> str:
    """Quote a string as an AppleScript string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _spawn_tmux(cmd: str) -> dict:
    # -h => left/right split (a sidebar); the pane runs cmd and closes on exit.
    proc = subprocess.run(["tmux", "split-window", "-h", cmd],
                          capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "stderr": (proc.stderr or "").strip()}


def _spawn_iterm(cmd: str) -> dict:
    script = (
        'tell application "iTerm2"\n'
        '  tell current session of current window\n'
        '    set newSession to (split vertically with default profile)\n'
        '  end tell\n'
        f'  tell newSession to write text {_applescript_quote(cmd)}\n'
        'end tell'
    )
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "stderr": (proc.stderr or "").strip()}


def _spawn_terminal(cmd: str) -> dict:
    # Terminal.app cannot split via AppleScript; this opens a new window.
    script = f'tell application "Terminal" to do script {_applescript_quote(cmd)}'
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "stderr": (proc.stderr or "").strip()}


_SPAWNERS = {"tmux": _spawn_tmux, "iterm": _spawn_iterm, "terminal": _spawn_terminal}


def cmd_launch(args: argparse.Namespace, home: Path) -> None:
    # The live monitor requires rich; refuse early with a clear pointer so the
    # orchestrator can offer to install it first.
    if not _has_rich():
        c.emit({
            "ok": False,
            "reason": "rich-missing",
            "install_cmd": _install_cmd(),
            "hint": "ask the user, then: python scripts/dashboard.py deps --install",
        })
        return

    cmd = _monitor_cmd(home, args.run_id, args.interval)
    requested = getattr(args, "method", "auto") or "auto"
    method = _detect_method() if requested == "auto" else requested

    # Dry-run, or no spawnable terminal: just hand back the command to paste.
    if getattr(args, "dry_run", False) or method == "print":
        c.emit({
            "ok": True,
            "spawned": False,
            "method": method,
            "command": cmd,
            "note": "run this in a side terminal pane to launch the live dashboard",
        })
        return

    spawner = _SPAWNERS.get(method)
    if spawner is None:
        c.fail(f"unknown method {method!r} (choose from {METHODS})")
    result = spawner(cmd)
    out = {"method": method, "command": cmd, **result, "spawned": result["ok"]}
    if not result["ok"]:
        # Fall back to printing the command so the user is never stuck.
        out["note"] = "spawn failed; run the command above manually in a side pane"
    c.emit(out)


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def _selftest(home: Path) -> None:
    import io
    import json as _json

    ns = argparse.Namespace

    # detection returns a known method
    assert _detect_method() in _DETECTED

    # the monitor command references monitor.py and the run id
    cmd = _monitor_cmd(home, "run-x", 0.25)
    assert "monitor.py" in cmd and "run-x" in cmd and "--run-id" in cmd, cmd

    # applescript quoting escapes embedded quotes/backslashes
    q = _applescript_quote('a "b" \\c')
    assert q.startswith('"') and q.endswith('"') and '\\"' in q, q

    # deps (no install) reports presence + install command, never installs
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        cmd_deps(ns(install=False), home)
    finally:
        sys.stdout = old
    d = _json.loads(buf.getvalue())
    assert isinstance(d["rich"], bool) and "install_cmd" in d, d
    assert "install_attempted" not in d, d  # no install without --install

    # launch --dry-run yields the command and never spawns a window
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        cmd_launch(ns(run_id="run-x", interval=0.25, method="auto", dry_run=True), home)
    finally:
        sys.stdout = old
    if d["rich"]:
        r = _json.loads(buf.getvalue())
        assert r["ok"] and r["spawned"] is False and "monitor.py" in r["command"], r
    else:
        # rich absent: launch refuses with a rich-missing reason (still no spawn)
        r = _json.loads(buf.getvalue())
        assert r["ok"] is False and r["reason"] == "rich-missing", r

    # an explicit method override is honored in dry-run (still no spawn)
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        cmd_launch(ns(run_id="run-x", interval=1.0, method="tmux", dry_run=True), home)
    finally:
        sys.stdout = old
    r = _json.loads(buf.getvalue())
    if d["rich"]:
        assert r["method"] == "tmux" and r["spawned"] is False, r


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    home_parent = argparse.ArgumentParser(add_help=False)
    home_parent.add_argument("--home", default=argparse.SUPPRESS,
                             help="override $LOOPITA_HOME")

    p = argparse.ArgumentParser(description="Loopita dashboard launcher", parents=[home_parent])
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("deps", parents=[home_parent],
                       help="check (or --install) the optional rich dependency")
    s.add_argument("--install", action="store_true",
                   help="install rich if missing (ask the user first)")
    s.set_defaults(func=cmd_deps)

    s = sub.add_parser("launch", parents=[home_parent],
                       help="open a side terminal pane running the live monitor")
    s.add_argument("--run-id", required=True, dest="run_id")
    s.add_argument("--interval", type=float, default=0.25)
    s.add_argument("--method", choices=METHODS, default="auto",
                   help="terminal to spawn into (default: auto-detect)")
    s.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="print the command instead of spawning")
    s.set_defaults(func=cmd_launch)

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

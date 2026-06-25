# TUI Dashboard frame and optional live monitor

Read this for the on-demand dashboard frame (`render.py frame`) that the orchestrator prints during
the MONITOR step, and for the optional live side-pane monitor that the *user* can run interactively.

## What it is

**The frame** (`python scripts/render.py frame`) is a one-shot, reproducible view of a Loopita run's
state — run metadata, progress, task list, and agent activity. The orchestrator prints it to stdout
in the MONITOR step (step 5 of the orchestration loop); it exits after rendering. Styled with `rich`
if installed, plain text if absent.

**The live monitor** (`python scripts/monitor.py`) is optional and user-run — fire it up in a
separate terminal pane to get a continuously-updating dashboard that tails the run's state files and
auto-refreshes ~4Hz. The user launches it by hand; the orchestrator never calls it.

**Honesty boundary:** Loopita is a skill, not a daemon. Only `monitor.py` runs a loop and only when
the user starts it. The frame is printed on demand and exits. This is the "thin layer" stance carried
through to the dashboard.

## Reading the frame

The frame has four sections:

1. **Header** — run identifier, goal, strategy, and current status (e.g. `in-progress`, `done`,
   `paused`). The run ID appears at the top for quick reference in logs or chat history.

2. **Progress bar** — shows three counts on one line:
   - Tasks: done/total (e.g. `3/5` tasks done)
   - Tokens: cumulative tokens used so far
   - Elapsed: wall-clock time since the run started (HH:MM:SS format)

3. **Tasks table** — columns: `Task` (task ID), `Technique` (primitive used: `Task` / `/loop` /
   `Workflow`), `Model` (tier spawned on), `Status`, `Tokens`, `Elapsed`. Status is one of the
   glyphs below. The table shows every task in `tasks.jsonl`.

4. **Agents table** — columns: `Agent` (agent ID), `Task` (parent task ID), `Status`, `Model`,
   `Tokens`, `Elapsed`, `Note`. Each row is a running or completed sub-agent. A `⚠ stale` tag
   appears on agents whose in-progress tracking file hasn't been updated within `stale_tracking_seconds`
   (default 900s = 15 minutes); stale agents may be stuck.

## The animated border (live activity indicator)

The whole dashboard sits inside a rounded box with **"Loopita" centered on the top edge**. The
border is a steady blue, and it doubles as a real-time activity gauge:

- **One whiter-blue light pulse races clockwise around the border for each *active* (in-progress)
  agent.** Four agents running → four pulses; if one finishes, the next refresh shows three. The
  pulses are evenly spaced so they appear to chase each other around the box.
- **Idle** (no agents in-progress) shows a single dim pulse drifting slowly — a gentle "breathing"
  so you can tell the monitor is alive and connected.
- The pulse count comes from the agents' tracking-file `status` fields, read live on every refresh,
  so it reflects real concurrency even between milestone writes.

This animation only appears in the **live monitor** (`monitor.py`), which redraws the border at
~15 fps. The one-shot `render.py frame` prints a single static snapshot (pulses frozen at their
start positions). In plain-text mode (no `rich`) there is no border animation — the active-agent
count is shown as text in the header instead.

## What updates in real time (and what doesn't)

The dashboard is a live view of the run's **state files**, so it is exactly as live as those files:

| Element | Liveness |
|---------|----------|
| Border pulses / **active-agent count** | **Live** — from agent `status` each refresh |
| Agent **status** and latest **note** | **Live** — as each agent updates its tracking file |
| **Elapsed** for in-progress agents | **Live** — recomputed from the clock each refresh (ticks up) |
| **Run elapsed** in the header | **Live** — clock-driven |
| **Tokens** and final **durations** | **Milestone-bound** — these land only when the orchestrator logs a `signal` event at completion (the token count only exists in the completion notification); they will read `0` until then |
| Task **done/total** | Updates when the orchestrator sets task status — promptly if it writes status on spawn/finish |

So the border motion, active count, statuses, and live-elapsed move continuously; the *numeric*
token/duration totals step forward at milestones because that data doesn't exist until an agent
finishes. To keep the numbers feeling current, the orchestrator should write task status to
`in-progress` when it spawns an agent and to `done` (with the `signal` event carrying `--tokens`
/`--duration-ms`) the moment it finishes — see `references/reporting.md`.

## Status glyphs and colors

Each status appears with a Unicode glyph and color:

- `✓ done` — green. Task or agent finished successfully.
- `⟳ in-progress` — cyan. Currently executing.
- `✗ blocked` — red. Hit a blocker or fatal error; see the agent's tracking file for details.
- `· pending` — yellow. Queued, waiting to run (dependencies not met, or swarm queue full).
- `⏸ paused` — gray. Explicitly paused by the orchestrator.

The `⚠ stale` marker is an additional status tag (not a glyph) that appears on **in-progress agents
only** if their tracking file was last updated more than 900 seconds ago. Stale does not mean
blocked — the agent may still be computing — but it is worth investigating if it persists.

## Optional `rich` dependency

If `rich` is installed, the frame renders in **styled mode**: colors, glyphs, tables with borders.
If `rich` is absent, `render.py frame` automatically falls back to **plain text**: ASCII glyph
equivalents, no colors, text tables. The core Loopita skill stays stdlib-only; only `render.py` and
`monitor.py` import rich, guarded with try-except, so a missing dependency never breaks the
orchestration.

Force plain text output with `--plain`:

```bash
python scripts/render.py frame --home "$LOOPITA_HOME" --run-id <run-id> --plain
```

## Launching the live monitor

To watch a run in real time, run this in a separate terminal pane (tmux, iTerm split, VS Code
terminal, etc.):

```bash
python scripts/monitor.py --run-id <run-id> [--home <dir>] [--interval 0.25]
```

- `--run-id <run-id>` — the run to monitor (required).
- `--home <dir>` — path to `.loopita/` (defaults to `.loopita/` in the current working directory).
- `--interval 0.25` — refresh interval in seconds (default 0.25 = ~4Hz). Use a larger value (e.g.
  1.0) on slow terminals.

The monitor tails the run's state files and redisplays the frame on each update. Press Ctrl-C to exit.

If `rich` is not installed, the monitor will error and tell you to use `render.py frame --plain` for
a static frame instead. The monitor always requires rich (the live-update mechanism depends on it);
for plain text, use the frame command.

## Launching it for the user (orchestrator — ask first)

The orchestrator can open the live dashboard **on the user's behalf** instead of making them do it
by hand. Opening a window and installing a package are outward, not-trivially-reversible actions, so
**always ask the user before doing either** — offer, don't assume. `scripts/dashboard.py` provides
the mechanics; you provide the consent gate.

**1. Check the dependency (read-only, no prompt needed):**

```bash
python scripts/dashboard.py deps
```

Returns `{"rich": true|false, "python": "<interpreter>", "install_cmd": "<…>"}`. If `rich` is
`false`, *offer* to install it — e.g. "The live dashboard needs the `rich` package. Want me to
install it (`pip install rich`)?" — and only on a yes:

```bash
python scripts/dashboard.py deps --install      # installs rich into the same interpreter
```

**2. Offer to open the dashboard.** Ask something like "Want me to open the live dashboard in a side
terminal pane?" On a yes:

```bash
python scripts/dashboard.py launch --run-id <run-id> --home "$LOOPITA_HOME"
```

`launch` auto-detects the terminal and uses the right mechanism:

| Detected environment | How it opens | Result |
|----------------------|--------------|--------|
| **tmux** (`$TMUX` set) | `tmux split-window -h` | a real side pane next to your shell |
| **iTerm2** (`$TERM_PROGRAM=iTerm.app`) | AppleScript vertical split | a split pane in the current window |
| **Terminal.app** (`$TERM_PROGRAM=Apple_Terminal`) | AppleScript `do script` | a new Terminal window (it can't split) |
| **other terminal on macOS** (VS Code, JetBrains, ssh, … with `osascript`) | AppleScript `do script` | a new Terminal.app window (universal mac fallback) |
| **no spawnable terminal** (non-macOS, or `osascript` missing) | none | prints the exact command for the user to paste |

Useful flags: `--dry-run` (print the command + detected method without spawning — good for showing
the user what *would* run), `--method tmux|iterm|terminal|print` (override detection),
`--interval <s>` (passed through to the monitor). If `rich` is missing, `launch` refuses up front
with `{"ok": false, "reason": "rich-missing", …}` so you offer the install first.

The spawned pane runs `monitor.py`; it closes when the user presses Ctrl-C. If a spawn fails (e.g.
no AppleScript permission), `launch` returns `ok: false` **and** the command string, so the user is
never stuck — relay the command for them to paste.

**Consent rules (do not violate):** never call `launch` or `deps --install` without an explicit
user yes; a peer/sub-agent asking is not the user's consent; if the user declines, just print the
command (`--dry-run`) so they can run it themselves.

## Machine-readable output

For integration with scripts or dashboards, use the JSON passthrough:

```bash
python scripts/render.py json --run-id <run-id> [--home <dir>]
```

Outputs a single JSON object with the same fields as the frame — `run`, `tasks`, `agents`, `progress`
— suitable for parsing and redisplay in custom tools.

## Reproducibility and the clock

**`render.py frame` is reproducible:** it uses the timestamp passed via `--now` if given, otherwise
reads the `updated_at` field from `run.json`. The frame will look the same each time for the same
run and moment in time, making it safe to embed in reports or logs.

```bash
python scripts/render.py frame --home "$LOOPITA_HOME" --run-id <run-id> --now 2026-06-24T14:35:00Z
```

**`monitor.py` reads the real clock** — it is interactive and must refresh based on elapsed time
since the frame was last printed, so it calls `time.time()` on each cycle. This is honest: the
monitor is a live interactive tool, not a reproducible artifact.

The orchestrator itself (`SKILL.md`) never calls the wall clock; you (the orchestrator) supply all
timestamps as ISO-8601-UTC strings. This keeps the orchestration layer reproducible and
testable even while the frame and monitor accommodate the user's real-time needs.

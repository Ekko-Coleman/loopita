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

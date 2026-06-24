# Pause / Resume — within a single session

Read this when the user asks to pause, or when something goes wrong mid-run. Loopita handles two
cases from spec §2.5, **both within one session** — there is **no cross-session survival.** If the
user closes the session without a checkpoint, live workflow state is lost (spec §6). Be honest
about that boundary; never imply the run will outlive the session on its own.

The recovery substrate is files, not memory: sub-agents keep their `tracking.{md,json}` current
(conventions.md §4), and the orchestrator records its own view in `checkpoint.json` (§7). Between
the two, you can resume without redoing completed work.

## Case A — Graceful pause (user-requested)

The user asks for a clean stop. You control the moment, so make it tidy.

1. **Quiesce.** Stop spawning new agents/slices. Let in-flight `Task`/`Workflow` agents reach a
   natural stopping point if they're close; otherwise note them as in-progress.
2. **Snapshot agent state.** Confirm each active agent's `tracking.json` is current; if one looks
   stale, query it (see staleness below) before checkpointing.
3. **Write the checkpoint** (`checkpoint.json`, conventions.md §7) via `state.py checkpoint`:
   ```
   python scripts/state.py --home "$LOOPITA_HOME" checkpoint --run-id <id> \
     --reason user-pause --strategy <linear|loop|swarm> \
     --open-tasks t2,t3 --done-tasks t0,t1 --merge-status pending \
     --workflow-run-id <wf_… or omit> --saved-at <ISO-8601-UTC>
   ```
   Derive the `--open-tasks`/`--done-tasks` lists from `python scripts/state.py list-tasks
   --run-id <id> [--status …]`. If a `Workflow` swarm is in flight, pass its run id as
   `--workflow-run-id` — that is the handle for `resumeFromRunId`.
4. **Set status** `paused`: `python scripts/state.py set-run --run-id <id> --status paused
   --updated-at <ISO>`, then `python scripts/audit.py log --run-id <id> --event checkpoint
   --note "user pause" --ts <ISO>`.
5. **Tell the user** what's done, what's open, and that `resume` continues it *in this session*.

### Resuming
1. Read the checkpoint for the orchestrator-level view (open vs. done tasks, merge status):
   `python scripts/state.py --home "$LOOPITA_HOME" get-checkpoint --run-id <id>`.
2. Re-derive per-agent truth from each `tracking.json` — the checkpoint is the orchestrator view;
   the tracking files are authoritative for agent progress.
3. **Do not redo `done` tasks.** Re-spawn only `open_task_ids` whose tracking shows `pending`/
   `blocked`/`in-progress`. For an in-flight swarm, resume the `Workflow` with `resumeFromRunId:
   <workflow_run_id>` instead of starting fresh — it picks up incomplete branches and preserves
   completed worktrees.
4. Flip `run.json` status back to `running`: `python scripts/state.py set-run --run-id <id>
   --status running --updated-at <ISO>`; `python scripts/audit.py log --run-id <id> --event status
   --note "resumed" --ts <ISO>`.

## Case B — Crash recovery (transient failure)

Quota exhaustion, a dropped connection, or a stalled/frozen agent. You did **not** get a clean
pause, so reconstruct state from what's on disk.

1. **Find the run.** Latest `runs/<run-id>/`; read the checkpoint if one exists
   (`python scripts/state.py --home "$LOOPITA_HOME" get-checkpoint --run-id <id>`; may be stale or
   absent — that's fine, tracking files cover the gap).
2. **Rebuild per-agent state from tracking files.** For each agent dir, parse `tracking.json`:
   `done` → keep its `signal`, don't re-run; `in-progress`/`blocked`/`pending` → candidate for
   re-spawn or `Workflow` resume.
3. **Detect stale/frozen agents** — an agent whose status is `in-progress` but whose `updated_at`
   is older than `stale_tracking_seconds` (default 900s):
   ```
   python scripts/tracking.py --home "$LOOPITA_HOME" stale --run-id <run-id> \
     --now <ISO-8601-UTC> --threshold-seconds 900
   ```
   For each stale agent, use the direct-query fallback first (ask it via SendMessage whether it's
   alive); if no response, treat it as dead and re-spawn that one task only.
4. **Resume in-flight swarms** via `Workflow` `resumeFromRunId: <workflow_run_id>` from the
   checkpoint — completed worktree branches are preserved; only incomplete branches re-run.
5. **Reconcile and continue** from the merge/sign-off step if all slices are now `done`.
6. Log it: `python scripts/audit.py log --run-id <id> --event checkpoint --note "crash recovery:
   <cause>" --ts <ISO>`.

## Quota pause/resume (advisory, orchestrator-driven)

This is **not** an autonomous daemon — Loopita is markdown + scripts. Quota pacing is something
*you*, the orchestrator, check each turn, not a background process. Be honest about that.

- **Track usage** against the rolling **5-hour window** (`quota_window_hours`, default 5). Sum the
  `tokens` you've logged to `audit.jsonl` over the window as a running estimate.
- **Pause when about to exhaust.** Before spawning a batch that would likely cross the limit,
  checkpoint with `--reason quota` (as in Case A step 3), set `run.json` status `paused`
  (`state.py set-run --status paused`), and tell the user the expected reset time.
- **Resume on reset.** Schedule the wake with **`ScheduleWakeup`** for the rolling-window reset,
  then resume exactly as in Case A's resume path. This pairs naturally with `/loop` for long loops
  — the loop body checks the budget and self-paces around the window.
- Treat all of this as **advisory**: the window is an estimate, the user can resume manually
  anytime, and you should surface the reasoning rather than silently stalling.

## What resume must never do

- Re-run a task whose `tracking.json` says `done` (you'd duplicate work and possibly conflict).
- Restart a whole `Workflow` from scratch when `resumeFromRunId` can continue it.
- Trust a stale `in-progress` agent without the direct-query check — it may be frozen, not working.

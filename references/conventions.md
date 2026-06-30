# Loopita Conventions — the contract

This file defines the runtime file layout and the schemas for every file Loopita reads or writes.
The `scripts/` helpers and `SKILL.md` both depend on it. **If you change a schema, change it here
first**, then update the scripts and SKILL.md to match. Everything is plain JSON / JSONL /
Markdown so it is greppable and human-readable.

## 1. Runtime layout

All runtime state lives under `$LOOPITA_HOME` (default `.loopita/`, resolved relative to the
project working directory; override with the `LOOPITA_HOME` env var). One subtree per run:

```
.loopita/
├── runs/
│   └── <run-id>/                 run-id = "run-YYYYMMDD-HHMMSS-<slug>" (timestamp passed in, never generated in-script)
│       ├── run.json              run metadata + config snapshot + status
│       ├── tasks.jsonl           task list — one task object per line (append/replace by task_id)
│       ├── audit.jsonl           append-only structured audit events
│       ├── learnings.md          tier-1 in-session learnings (Markdown bullets)
│       ├── checkpoint.json       latest orchestration checkpoint (for pause/resume)
│       ├── snapshots/
│       │   └── <n>.md            orchestrator context offloads (n = 1,2,3…)
│       └── agents/
│           └── <agent-id>/       agent-id = short kebab slug, unique within the run
│               ├── tracking.md   the agent self-reports here (human + grep readable)
│               └── tracking.json machine-state mirror of tracking.md
└── learnings/
    └── persistent.md             tier-2 durable learnings (cross-run; read at next invocation)
```

**Why files, not memory:** the orchestrator stays lean by offloading detail to these files and
grepping them on demand rather than holding everything resident (spec §2.2). Sub-agents write
their own tracking files so the orchestrator never has to retain their internals (spec §2.1).

**Timestamps:** ISO-8601 UTC strings, e.g. `2026-06-24T17:30:00Z`. Scripts never call the system
clock implicitly for IDs — the caller passes timestamps/run-ids in, so behavior is reproducible.

## 2. `run.json` — run metadata

```json
{
  "run_id": "run-20260624-103000-add-auth",
  "goal": "one-sentence statement of the task",
  "strategy": "linear | loop | swarm",
  "status": "planning | running | paused | done | failed",
  "config": { "context_threshold_tokens": 300000, "per_loop_time_limit_hours": 24, "quota_window_hours": 5 },
  "created_at": "2026-06-24T17:30:00Z",
  "updated_at": "2026-06-24T17:45:00Z"
}
```

## 3. `tasks.jsonl` — task list (one JSON object per line)

Each line is a unit of work. Re-writing a line by `task_id` replaces that task; the file is the
source of truth for "what's left."

```json
{
  "task_id": "t1",
  "title": "implement login endpoint",
  "scope": "files/dirs or component this task owns; non-overlapping for swarm",
  "strategy": "linear | loop | swarm",
  "model": "opus | sonnet | haiku | fable | null (null = sub-agent inherits the orchestrator's model)",
  "status": "pending | in-progress | done | blocked",
  "agent_ids": ["login-builder"],
  "depends_on": ["t0"],
  "signal_summary": "minimal result the orchestrator collected back, or null",
  "created_at": "2026-06-24T17:30:00Z",
  "updated_at": "2026-06-24T17:40:00Z"
}
```

## 4. Agent tracking file

Every sub-agent maintains **both** files (the boilerplate in `subagent-contract.md` instructs it
to). `tracking.md` is the readable log the orchestrator greps; `tracking.json` is the structured
mirror the query interface parses. Keep them consistent on every update.

### 4.1 `tracking.json` (machine mirror)

```json
{
  "agent_id": "login-builder",
  "task_id": "t1",
  "scope": "auth/login.py and its unit tests",
  "status": "pending | in-progress | done | blocked",
  "progress_notes": ["short bullet per meaningful step"],
  "blockers": ["description of anything blocking, or empty"],
  "escalation": "null, or a message that invalidates the plan (e.g. 'this is sequential, not parallel')",
  "signal": "the minimal output the orchestrator needs back, or null until done",
  "started_at": "2026-06-24T17:31:00Z",
  "updated_at": "2026-06-24T17:42:00Z"
}
```

### 4.2 `tracking.md` (human + grep readable)

```markdown
# Agent: login-builder  (task t1)
- **scope:** auth/login.py and its unit tests
- **status:** in-progress
- **started:** 2026-06-24T17:31:00Z
- **updated:** 2026-06-24T17:42:00Z

## Progress
- read existing auth module, found session helper to reuse
- wrote login endpoint + 3 unit tests

## Blockers
- (none)

## Escalation
- (none)

## Signal
- (pending)
```

**Status values are exactly:** `pending`, `in-progress`, `done`, `blocked`. **Staleness:** if
`updated_at` is older than `stale_tracking_seconds` (default 900s) while status is `in-progress`,
the orchestrator treats the agent as possibly frozen and uses the direct-query fallback (ask the
agent directly via SendMessage, or re-spawn).

## 5. `audit.jsonl` — structured audit log (append-only, one event per line)

The report and retro are assembled from this log, so log every meaningful transition.

```json
{
  "ts": "2026-06-24T17:31:00Z",
  "run_id": "run-20260624-103000-add-auth",
  "agent_id": "login-builder",
  "task_id": "t1",
  "event": "spawn | status | signal | blocker | escalation | merge | checkpoint | report | learning | strategy",
  "strategy": "linear | loop | swarm | null",
  "model": "opus | sonnet | haiku | fable | null (the model the agent ran on; stamp it on spawn/signal)",
  "tokens": 84852,
  "duration_ms": 23332,
  "note": "free-text detail"
}
```

`agent_id`, `task_id`, `model`, `tokens`, `duration_ms` are nullable. `tokens`/`duration_ms` come from the
`Task`/`Workflow` completion notifications — capture them when an agent finishes (see
`reporting.md`).

## 6. `learnings.md` (tier-1) and `learnings/persistent.md` (tier-2)

Both are Markdown. Each learning is a single bullet tagged with its level so the strategy selector
can filter:

```markdown
- [orchestration] parallel code review conflicts on this codebase → sequence it
- [agent:test-runner] keeps forgetting to activate the venv → add `source .venv/bin/activate` to the brief
```

Tier-1 (`runs/<run-id>/learnings.md`) is fed back to the orchestrator and agents *during* the run.
Tier-2 (`learnings/persistent.md`) is written during the retro and read at the *next* invocation.
See `learning.md` for the two-tier model and the mirror into user memory.

## 7. `checkpoint.json` — pause/resume

A snapshot of orchestration state sufficient to resume without redoing completed work. Per-agent
status is recovered from the tracking files; this records the orchestrator-level view.

```json
{
  "run_id": "run-20260624-103000-add-auth",
  "strategy": "swarm",
  "reason": "user-pause | quota | crash",
  "workflow_run_id": "wf_… (if a Workflow is in flight, for resumeFromRunId), or null",
  "open_task_ids": ["t2", "t3"],
  "done_task_ids": ["t0", "t1"],
  "merge_status": "pending | in-progress | done | n/a",
  "saved_at": "2026-06-24T17:50:00Z"
}
```

## 8. Script interface convention

Every `scripts/*.py` helper is invokable as `python scripts/<name>.py <subcommand> [args]`, prints
JSON to stdout for read/query subcommands, exits non-zero on error, supports `--help`, and supports
`--selftest` (writes to a temp dir, round-trips, asserts, cleans up). All helpers accept
`--home <dir>` to override `$LOOPITA_HOME`. This keeps the orchestrator's calls uniform and lets
it grep results directly.

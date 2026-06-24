# Final report and retro query mode

Read this for the end-of-run report (spec §2.7) and for answering follow-up questions afterward.
The report is assembled entirely from `audit.jsonl` (conventions.md §5), so it is only as good as
what you logged *during* the run. The critical, easy-to-miss part is capturing per-agent `tokens`
and `duration_ms` — you get exactly one chance.

## Capture metrics as agents finish (do not skip)

`Task` and `Workflow` agents report their `tokens` and `duration_ms` in the **completion
notification** the orchestrator receives when the agent returns. That notification is the **only**
moment those numbers are available — they are not recoverable later from the tracking files. So the
instant an agent completes, log them:

```
python scripts/audit.py --home "$LOOPITA_HOME" log --run-id <run-id> \
  --agent-id <agent-id> --task-id <task-id> --event signal \
  --strategy <linear|loop|swarm> \
  --tokens <from notification> --duration-ms <from notification> \
  --note "<one-line outcome>" --ts <ISO-8601-UTC>
```

Do this for every agent in a swarm as each branch finishes — a `Workflow` returns a notification
per agent. If you defer it, the numbers are gone and the report's per-agent breakdown will be
empty. Also log the lifecycle events as they happen so the report can reconstruct the timeline;
`--event` is one of: `spawn`, `status`, `signal`, `blocker`, `escalation`, `merge`, `checkpoint`,
`report`, `learning`, `strategy`.

## Build the report

When the run completes (end-condition met, worktrees merged, work committed, signed off):

```
python scripts/report.py --home "$LOOPITA_HOME" build --run-id <run-id>
```

It reads `run.json`, `tasks.jsonl`, and `audit.jsonl` and assembles the report. Present it to the
user. The report contains:

- **Per-task** breakdown: title, `scope`, strategy/loop used, status, summed `tokens` + elapsed,
  and the `signal_summary`.
- **Per-agent** breakdown: `tokens` and `duration_ms` for each agent, under its task.
- **Technique per unit:** which primitive ran each task — `Task` (linear), `/loop` (loop), or
  `Workflow` (swarm) — and for loops, iteration count and end-condition.
- **Blockers and resolutions:** every `blocker`/`escalation` event paired with how it resolved
  (including any strategy replanning, from the `strategy` events).
- **Run totals:** total tokens, total wall-clock, agent count, parallelism peak.

Log the act of reporting:
`python scripts/audit.py --home "$LOOPITA_HOME" log --run-id <run-id> --event report --note "final report presented" --ts <ISO>`.

## Retro query mode

After the report, the user can interrogate the run. You answer from the audit log, not from
memory — the detail was offloaded there precisely so you didn't have to retain it. Both retro
commands filter by event type and/or agent; you interpret and summarize their JSON output for the
user's actual question.

- **Aggregated retro view** — for "what happened" / "what did agent X do" / "where did the blockers
  come from" questions, get the assembled per-agent + event rollup:
  ```
  python scripts/report.py --home "$LOOPITA_HOME" retro --run-id <run-id> \
    [--event <type>] [--agent-id <id>]
  ```
- **Raw event lookup** — for a quick scan of specific events:
  ```
  python scripts/audit.py --home "$LOOPITA_HOME" query --run-id <run-id> \
    [--event <type>] [--agent-id <id>]
  ```
  Returns matching `audit.jsonl` lines as JSON. For run-wide totals use
  `python scripts/audit.py --home "$LOOPITA_HOME" summary --run-id <run-id>`.

Prefer these queries over reconstructing from your own context — they're authoritative and they
keep your window lean even in the retro. If a question needs an agent's step-by-step detail rather
than aggregates, grep that agent's `tracking.md` (conventions.md §4) or use
`python scripts/tracking.py --home "$LOOPITA_HOME" get --run-id <run-id> --agent-id <id>`.

## Reporting and the learning retro

The reporting retro (answering the user) and the **learning retro** (distilling durable lessons,
`learning.md`) run back-to-back on the same material — `audit.jsonl` plus the tracking files. Build
the report first; the blockers/resolutions and strategy-replanning events it surfaces are exactly
the raw material the learning retro mines for tier-2 lessons.

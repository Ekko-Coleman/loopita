# Strategy Selection — linear vs. loop vs. swarm

Read this when you (the orchestrator) are deciding *how* to execute a task. Loopita maps every
task onto one of three native Claude Code primitives. Your job is to pick the right one from the
task's shape, record the choice, and re-pick if a sub-agent proves you wrong.

| Strategy | Native primitive | Use when |
|----------|-----------------|----------|
| **linear** | the **`Task`** tool (one scoped sub-agent) | A single coherent change; one writer; sequential by nature. |
| **loop**   | **`/loop`** + **`ScheduleWakeup`**, with an explicit end-condition state and per-loop time limit | Unknown number of iterations; "until <condition>"; polling/waiting; retry-until-green. |
| **swarm**  | the **`Workflow`** tool (`parallel()` / `pipeline()`, git-worktree isolation, token `budget`, `resumeFromRunId`) | The task decomposes into independent, non-overlapping slices that can run at once. |

The model is the **final arbiter.** These heuristics narrow the choice; your judgment of the
actual task overrides them. Learned heuristics (below) get weighed in but can also be overridden.

## Signals

Read the task and look for these signals. Most tasks emit more than one — weigh them.

**→ swarm (`Workflow`)**
- Decomposes cleanly into 2+ slices that touch **non-overlapping** files/dirs/components.
- Slices have no ordering dependency on each other (`depends_on` is empty between them).
- Throughput matters and quota headroom exists (respect `max_parallel_agents`, default 8).
- Example phrasings: "add endpoints for X, Y, and Z", "migrate each module", "write tests for these N files".

**→ linear (`Task`)**
- One coherent change to one area; splitting it would just create merge friction.
- Strong internal ordering — step B needs step A's output in the same edit surface.
- Small enough that one agent finishes in a single pass.
- Example phrasings: "refactor this function", "fix this bug", "add a field to this model and its callers".

**→ loop (`/loop` + `ScheduleWakeup`)**
- Iteration count is **unknown** up front; you stop on a *condition*, not a count.
- Polling, watching, or waiting for an external state (CI, a deploy, a queue draining).
- "Keep trying until it passes", "check every N minutes", "babysit until green".
- Always define the **explicit end-condition state** and a **per-loop time limit** (default 24h,
  from `config/defaults.json`); sequential loops each get their own budget. Pace against the
  5-hour quota window (see `pause-resume.md`).

Strategies **nest**: a loop body can fan out a swarm; a swarm pipeline's final stage can be a
linear merge `Task`. Pick the outermost shape first, then recurse per slice.

## The merge step (swarm only)

Parallel agents commit to their own worktrees. Reconcile them with **either** a final
`pipeline()` stage **or** a dedicated merge `Task` agent whose sole job is to merge the worktrees,
resolve conflicts, and hand the result back to you for sign-off. This is the spec's "merge agent."
Do not skip sign-off — review before the final commit.

## Recording the decision

When you pick, record it so the run is reproducible and auditable:
1. Set `strategy` in `run.json` via `python scripts/state.py set-run --run-id <id> --strategy
   <linear|loop|swarm> --updated-at <ISO>`, and per-task `strategy` when you add each task with
   `python scripts/state.py add-task --run-id <id> --task-id <tid> --title "…" --strategy <…>
   --created-at <ISO>`.
2. `python scripts/audit.py log --run-id <id> --event strategy --note "<why this strategy>" --ts <ISO>`.
3. If you applied a learned heuristic, note that in the audit `--note` so the retro can judge it.

## Worked examples

**1. "Add REST endpoints for users, orders, and invoices, each with tests."**
Three non-overlapping slices, no cross-dependency → **swarm** via `Workflow.parallel()`, one agent
per resource in its own worktree, plus a merge stage. Three `tasks.jsonl` rows, disjoint `scope`.

**2. "Refactor `auth/session.py` to use the new token helper and update its callers."**
One coherent change, tightly coupled edits, one writer → **linear** via a single `Task` agent.
Splitting callers across agents would just cause conflicts on shared files.

**3. "Keep running the test suite and fixing failures until CI is green."**
Unknown iteration count, stop-on-condition → **loop** via `/loop` + `ScheduleWakeup`. End-condition
state: "CI status == green". Per-loop limit 24h. Each iteration may itself spawn a fix `Task`.

**4. "Investigate why checkout is slow — look at the DB layer, the frontend, and the cache."**
Three independent investigation tracks → **swarm**; for a research task the final stage is a
*synthesis* `Task` (aggregate findings) rather than a code merge.

## Adaptive replanning

A sub-agent may escalate that the plan is wrong — the canonical case is a swarm slice reporting
"this is actually sequential, not parallel" via its tracking file's `escalation` field
(conventions.md §4). When you detect an escalation (poll `python scripts/tracking.py query
--run-id <id>`; per-agent detail via `tracking.py get`, frozen agents via `tracking.py stale`):

1. **Stop** spawning further conflicting slices; let in-flight non-conflicting ones finish.
2. **Re-read** the affected `tasks.jsonl` rows and the escalating agent's `tracking.md`.
3. **Re-select** the strategy for the affected subtree — usually swarm → linear/loop, adding
   `depends_on` edges to serialize what the agent found to be ordered.
4. **Resume** in-flight swarm work via `Workflow`'s `resumeFromRunId` rather than restarting
   (see `pause-resume.md`); rewrite the affected tasks with `python scripts/state.py set-task
   --run-id <id> --task-id <tid> --status … --updated-at <ISO>` (and re-add with the new
   `--strategy`/`--depends-on` if the decomposition changed).
5. **Log** it: `python scripts/audit.py log --run-id <id> --event strategy --note "replanned
   swarm→linear: <reason>" --ts <ISO>`, and add a tier-1 learning (`python scripts/learnings.py add
   --level orchestration --scope session --run-id <id> --text "…"`) so the rest of the run benefits.

Replanning is expected, not a failure. Catching a bad decomposition early and serializing it costs
far less than merging worktrees that were never independent.

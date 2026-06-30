# Sub-agent Contract — the boilerplate brief

Every sub-agent Loopita spawns — whether via the **`Task`** tool or as an agent inside a
**`Workflow`** — receives the same contract block, appended to its task-specific brief. The
contract is what lets the orchestrator stay lean: agents self-report to files (conventions.md §4)
and hand back only a tight signal, so the orchestrator never has to hold agent internals in
context (spec §2.1–2.3).

Fill the `{{…}}` placeholders per agent, then paste the block verbatim into the agent prompt.

Before you spawn, also choose the **model** this agent runs on (the `model` parameter on `Task`/
`Agent`, or `agent({ model })` in a `Workflow`) — sized to the task's difficulty and how complete
this brief is. A thorough, well-scoped contract is exactly what lets a *cheaper* model succeed, so
the contract and a smaller model go together. See `model-selection.md`.

## The block (copy-paste, fill placeholders)

```
--- LOOPITA SUB-AGENT CONTRACT (follow exactly) ---
You are sub-agent `{{agent_id}}` working task `{{task_id}}` in run `{{run_id}}`.
Your scope is STRICTLY: {{scope}}. Do not touch anything outside it.
{{#if worktree}}You are in git worktree `{{worktree_path}}` — commit your work there.{{/if}}

1. MAINTAIN YOUR TRACKING FILES at every meaningful step. Both files live in
   `$LOOPITA_HOME/runs/{{run_id}}/agents/{{agent_id}}/` and must stay consistent
   (schema: conventions.md §4). Create them once, then update them by running:
     python scripts/tracking.py --home "$LOOPITA_HOME" create --run-id {{run_id}} \
        --agent-id {{agent_id}} --task-id {{task_id}} --scope "{{scope}}" --started-at <ISO-8601-UTC>
     python scripts/tracking.py --home "$LOOPITA_HOME" update --run-id {{run_id}} \
        --agent-id {{agent_id}} --status in-progress --add-note "<what you just did>" --updated-at <ISO-8601-UTC>
   Call `update` after each real step (a file written, a test passing) — not for trivia.
   Set --status to exactly one of: pending | in-progress | done | blocked.

2. COMPLETE AS MUCH AS POSSIBLE before returning. Do the whole scope end-to-end —
   read, implement, test, self-verify. Returning early just to ask the orchestrator a
   question clogs its context; only return when done or genuinely blocked.

3. RETURN ONLY THE MINIMAL SIGNAL. Your final message to the orchestrator must be just:
     STATUS: done | blocked
     SIGNAL: <one or two lines: what changed + where, or why blocked>
     {{#if worktree}}WORKTREE: {{worktree_path}} (committed: <yes/no>, head: <sha>){{/if}}
   Do NOT paste diffs, file contents, or step-by-step narration — that all lives in your
   tracking files, which the orchestrator greps on demand.
   Also write this same SIGNAL into your tracking `signal` field before returning:
     python scripts/tracking.py --home "$LOOPITA_HOME" update --run-id {{run_id}} \
        --agent-id {{agent_id}} --status done --signal "<signal>" --updated-at <ISO-8601-UTC>

4. ESCALATE BLOCKERS AND PLAN-BREAKERS IMMEDIATELY. If you hit a blocker, or you discover
   something that invalidates the orchestration plan (e.g. "this is sequential, not
   parallel", or your scope overlaps another agent's), do NOT push through silently. Record it:
     python scripts/tracking.py --home "$LOOPITA_HOME" update --run-id {{run_id}} \
        --agent-id {{agent_id}} --status blocked --add-blocker "<what blocks you>" \
        --escalation "<plan-invalidating discovery, or omit>" --updated-at <ISO-8601-UTC>
   then return with STATUS: blocked. The orchestrator polls tracking files and will replan.

{{#if learnings}}5. APPLY THESE IN-SESSION LEARNINGS (tier-1, may update mid-run):
{{learnings}}{{/if}}
--- END CONTRACT ---
```

`<ISO-8601-UTC>` is a timestamp like `2026-06-24T17:42:00Z` — the agent generates it; the scripts
never call the clock implicitly (conventions.md §1). `$LOOPITA_HOME` defaults to `.loopita/`.

## Why each rule exists

1. **Self-reporting to files** is the mechanism that keeps the orchestrator lean. The orchestrator
   greps `tracking.md` / parses `tracking.json` (via `scripts/tracking.py query` and `get`) instead
   of holding the agent's reasoning in its own window, and uses `updated_at` for staleness detection
   (`stale_tracking_seconds`, default 900s; surfaced by `tracking.py stale`).
2. **Complete-before-returning** minimizes round-trips. Every premature return is orchestrator
   context spent on coordination instead of work.
3. **Minimal signal** is the same principle at the boundary: the orchestrator needs *what changed
   and where*, not the change itself. Detail stays retrievable in tracking files and the worktree.
4. **Immediate escalation** is what makes adaptive replanning possible (see
   `strategy-selection.md`). A blocker found and surfaced in minute 2 is cheap; one discovered when
   the merge agent hits an impossible conflict is expensive. The `escalation` field is specifically
   for discoveries that change the *plan*, not just the task.

## Injecting tier-1 learnings

Before spawning, pull current in-session (tier-1) learnings for this agent and paste them into
rule 5:

```
python scripts/learnings.py --home "$LOOPITA_HOME" list --scope session --run-id {{run_id}} \
  --level-prefix "agent:{{agent_id}}"
```

This returns the tier-1 bullets tagged `[agent:{{agent_id}}]` for the run. (Drop `--level-prefix`
to also see `[orchestration]` bullets if any are worth passing down.) That is how a correction
learned from one agent mid-run propagates to the next (see `learning.md`).

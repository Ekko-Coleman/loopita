# Learning — the two-tier model

Loopita improves both *within* a run and *across* runs (spec §2.6). Two tiers, two lifetimes:

| Tier | File | Lifetime | Written | Read |
|------|------|----------|---------|------|
| **Tier 1 — in-session** | `runs/<run-id>/learnings.md` | this run only | *during* the run | fed to orchestrator + agents in real time |
| **Tier 2 — persistent** | `learnings/persistent.md` (+ user memory mirror) | across runs | during the retro | at the *next* invocation's strategy step |

Both are Markdown bullets, each tagged with its level so the strategy selector can filter
(conventions.md §6):

```markdown
- [orchestration] parallel code review conflicts on this codebase → sequence it
- [agent:test-runner] keeps forgetting to activate the venv → add `source .venv/bin/activate` to the brief
```

## Tier 1 — dynamic, in-session

The point of tier-1 is **real-time propagation**: a mistake or discovery caught by one agent or by
you should immediately benefit the rest of the run, not wait for a retro.

**Add a learning the moment you observe it:**
```
python scripts/learnings.py --home "$LOOPITA_HOME" add --scope session --run-id <run-id> \
  --level "agent:test-runner" --text "forgot to activate venv; add the activate line to its brief"
```
Use `--level orchestration` for plan-level lessons, `--level "agent:<name>"` for agent-level ones.

**Feed them back in** (tier-1 lives in `runs/<run-id>/learnings.md`, so read it with `list`, not
`apply` — `apply` only ever returns *persistent* tier-2 learnings):
- *Into agent briefs* — before spawning (or re-spawning) an agent, run
  `python scripts/learnings.py list --scope session --run-id <run-id> --level-prefix "agent:<name>"`
  and paste the returned bullets into rule 5 of the sub-agent contract (`subagent-contract.md`).
  That is how a correction learned from one agent reaches the next agent of the same kind mid-run.
- *Into your own decisions* — run `python scripts/learnings.py list --scope session --run-id
  <run-id> --level-prefix orchestration` before each strategy choice and replanning step
  (`strategy-selection.md`); weigh the `[orchestration]` bullets, but you remain the final arbiter.

Log each addition: `python scripts/audit.py log --run-id <id> --event learning --note "<the bullet>"
--ts <ISO>` so the retro can trace which lessons fired during the run.

### Example tier-1 learnings
- `[orchestration]` "swarm slice on `routes/` and `routes/_shared.py` collided → split the shared
  file into its own sequential task next time we touch routes."
- `[agent:db-migrator]` "produced a migration without a down-step → require reversible migrations in
  the brief."
- `[agent:doc-writer]` "kept re-reading whole files into its return → reinforce the minimal-signal
  rule for this agent."

## Tier 2 — persistent, distilled in the retro

After the run completes and the report is built (`reporting.md`), run the **learning retro**:
review the full `audit.jsonl` and the tracking files, and distill the *durable* lessons — the ones
that will still be true on the next run against this codebase. Promote them to tier-2:

```
python scripts/learnings.py --home "$LOOPITA_HOME" add --scope persistent \
  --level orchestration --text "code review on this repo conflicts when parallelized → sequence it"
```

`--scope persistent` writes to `learnings/persistent.md` **and** mirrors the bullet into the user's
Claude memory store so it survives independently of the project tree. (In this environment the
memory dir is the per-project `…/memory/` directory described in the system prompt; keep the
mechanism general — the script handles the write, you supply the lesson.) Don't promote run-specific
noise — only lessons that generalize.

**Apply on the next invocation.** At the start of the next run, before choosing a strategy:
```
python scripts/learnings.py --home "$LOOPITA_HOME" apply
```
`apply` takes no other arguments — it returns the tier-2 bullets from `learnings/persistent.md` as
JSON. Bake the `[orchestration]` ones into your initial strategy decision and the `[agent:*]` ones
into the relevant agent briefs. As always, these are weighed, not obeyed — a persistent heuristic can be
overridden when the current task clearly differs.

### Example tier-2 learnings
- `[orchestration]` "this repo's integration tests share a fixture DB → never run test slices in
  parallel worktrees; loop them sequentially instead."
- `[orchestration]` "frontend + backend changes here almost always couple through the generated API
  client → treat them as one linear task, not two swarm slices."
- `[agent:test-runner]` "this project uses `uv`, not bare `pytest` → brief it to run `uv run pytest`."

## Dual targeting, in one line

Every learning aims at one of two surfaces, and the tag says which:
- **`[orchestration]`** → changes *how you plan* (linear/loop/swarm, sequencing, parallelism).
  Consumed by the strategy selector and replanning logic.
- **`[agent:NAME]`** → changes *what a spawned agent is told* (a prompt correction).
  Consumed when building that agent's contract brief.

Keep the tags exact — the scripts and the strategy selector filter on them.

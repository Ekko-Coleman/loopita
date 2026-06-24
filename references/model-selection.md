# Model Selection — pick the cheapest model that can do the job

Read this when you (the orchestrator) are about to spawn a sub-agent. Strategy selection decides
*how* the work is shaped (linear / loop / swarm); model selection is the **orthogonal** decision of
*which model* each sub-agent runs on. The two are independent — pick the strategy first, then a
model per agent within it.

**The core stance: do NOT default to Opus.** You (the orchestrator) may be running on a strong model
like Opus 4.8, and sub-agents **inherit your model unless you set one explicitly** — so spawning
without a `model` silently runs every slice on the most expensive tier. Instead, deliberately pick
the **cheapest model that can reliably complete the task given the brief you hand it**, and set it
at spawn time. Most work does not need Opus.

## How to set it

| Primitive | Where the model goes |
|-----------|----------------------|
| **linear / loop** (`Task` / `Agent`) | the spawn tool's **`model`** parameter (`opus` \| `sonnet` \| `haiku` \| `fable`) |
| **swarm** (`Workflow`) | **`agent(prompt, { model, effort })`** per call — set `model` per slice; use `effort: 'low'` for cheap mechanical stages |

If you omit `model`, the agent inherits your (the orchestrator's) model. This is a **deliberate
departure** from `Workflow`'s built-in "default to omitting `model`" advice: Loopita's whole point
is to make a per-task choice, so set it explicitly rather than inheriting.

## The tiers

Available models: **opus** (4.8) · **sonnet** (4.6) · **haiku** (4.5) · **fable** (5).

| Tier | Reach for it when the task is… | Examples |
|------|--------------------------------|----------|
| **haiku** | Mechanical, fully specified, low-ambiguity — the agent has everything it needs in the brief. | Rote pattern application across files, renames, formatting, boilerplate/scaffolding, running tests/builds and reporting results, reading/gathering/summarizing, simple doc edits. |
| **sonnet** | Real coding with moderate reasoning — the workhorse default. **Start here, not at Opus.** | Standard feature implementation, writing tests, straightforward bug fixes, most swarm slices, typical refactors. |
| **opus** | Genuinely hard: ambiguous, high-stakes, or needs deep reasoning. | Architectural/ambiguous design, subtle concurrency/security logic, gnarly debugging with an unclear root cause, complex multi-file merges with conflict potential, the final merge/sign-off where correctness is critical. |
| **fable** | Choose by judgment when its strengths fit; not over-prescribed here. | — |

## Rules of thumb

1. **Cheapest capable wins.** Estimate the task's difficulty and pick the lowest tier that can
   *reliably* finish it. Don't pay for Opus on work Haiku or Sonnet handles.
2. **Invest in the brief to drop a tier.** A rich, well-scoped brief — which the sub-agent contract
   (`subagent-contract.md`) already mandates — is what lets a *cheaper* model succeed. Context you
   provide up front is cheaper than reasoning the agent would otherwise have to do. Pair a thorough
   contract with a smaller model.
3. **Match the model to the unit, per strategy:**
   - **linear** — one model, chosen by the single change's difficulty.
   - **swarm** — **per slice.** Mechanical, parallel slices are usually haiku/sonnet; the
     **merge / sign-off** stage often warrants a stronger model (it's where correctness concentrates).
     Use `effort: 'low'` on cheap `Workflow` stages on top of a small model.
   - **loop** — the fix-agent by difficulty; "run tests, report, retry" iterations are cheap-model
     territory.
4. **Start cheap, escalate on evidence.** If a cheaper agent returns `blocked`, produces low-quality
   work, or escalates that the task is harder than scoped, re-spawn **that one task** at a higher
   tier — and record a tier-1 learning so the rest of the run (and future runs) benefit. This is
   cheaper on average than starting everything on Opus.

## Recording the choice

Model selection is recorded the same way strategy is, so the report and the two-tier learning loop
can evaluate whether your choices paid off:

1. When you add the task, record the planned model:
   `python scripts/state.py add-task --run-id <id> --task-id <tid> --title "…" --strategy <s>
   --model <opus|sonnet|haiku|fable> --created-at <ISO>`. If you escalate mid-run, patch it:
   `state.py set-task --run-id <id> --task-id <tid> --model <higher-tier> --updated-at <ISO>`.
2. When you spawn and when the agent finishes, stamp the model on the audit event:
   `python scripts/audit.py log --run-id <id> --agent-id <aid> --task-id <tid> --event spawn
   --model <m> --ts <ISO>` (and again on the `signal` event with `--tokens`/`--duration-ms`).
3. The run report (`report.py build`) surfaces a **Model** column on the Tasks and Agents tables, so
   the retro can ask "did the Haiku slices actually succeed?" and tier-2 learnings can refine future
   choices (e.g. `[orchestration] this codebase's migration slices succeed on haiku`).

## Worked examples

**1. "Rename `OldClient` to `ApiClient` across the codebase and update imports."**
Mechanical, fully specified, low ambiguity → **haiku**. Hand the agent the exact old/new names and
scope; it doesn't need to reason about design.

**2. "Add REST endpoints for users, orders, and invoices, each with tests."**
Swarm of three standard-coding slices → **sonnet** per slice. If a final merge stage reconciles a
shared router with real conflict potential, run that one on **opus**.

**3. "Figure out why checkout intermittently double-charges and fix it."**
Ambiguous root cause, money-sensitive, subtle concurrency → **opus**.

**4. "Keep running the suite and fixing failures until CI is green."**
Loop whose fix-agent does well-scoped, mechanical fixes → **sonnet** (drop to **haiku** if failures
are trivial/formatting); reserve **opus** only if a failure turns out to need deep debugging.

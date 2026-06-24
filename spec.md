# Loopita — Project Specification

**Project name:** Loopita
**Type:** Claude Code skill (greenfield, standalone Git repo)
**Status:** Draft v0.1
**Owner:** Mike

---

## 1. Overview

Loopita (a play on "loop" + the Spanish name *Lupita*, spelled `l-o-o-p-i-t-a`) is a meta-orchestration skill invoked at the top of a Claude Code session. It sits as a layer between the user and the underlying model, analyzes the task, and selects the best execution strategy — a single iterative pass, a loop with an explicit end condition, or a swarm of parallel sub-agents — then drives that strategy to completion while keeping its own context small.

The orchestrator never does heavy work itself. It decomposes the task, spawns scoped sub-agents, collects only the signal it needs back, persists state to temp files, and assembles the final result. It is quota-aware, supports clean pause/resume within a session, produces a detailed audit report on completion, and learns from each run.

**Primary use case:** software engineering (greenfield and brownfield). **Secondary:** research workflows.

**Invocation moment:** at the start of a task or while brainstorming one.

### Design principles

- **Orchestrator stays lean.** The main thread is a coordinator, not a worker. All substantive work happens in sub-agents.
- **Minimal context handoff.** Each sub-agent receives only the context it needs and returns only the signal the orchestrator needs.
- **Isolation.** Parallel work happens in isolated Git worktrees so agents never step on each other.
- **Observability by default.** Everything is logged for auditing and reporting.
- **Self-healing.** The system tracks per-agent progress and resumes cleanly after quota exhaustion, dropped connections, or stalled agents.
- **Learning.** Loopita improves across runs via a two-tier learning system.

---

## 2. Architecture Overview

### 2.1 Components

| Component | Role |
|-----------|------|
| **Orchestrator** | Runs on the main thread. Plans strategy, spawns agents, monitors context + quota, checkpoints, assembles results, runs the retro. |
| **Strategy selector** | Decides linear pass vs. loop vs. swarm based on task analysis and learned heuristics. The model is the final arbiter. |
| **Sub-agents** | Scoped workers. Receive a narrow brief, work in isolation, self-report progress to a tracking file, return only required signal. |
| **Worktree merge agent** | Specialized agent whose sole job is merging completed worktrees back, resolving conflicts, and handing the result to the orchestrator for sign-off. |
| **Persistence layer** | Temp files for task lists, per-agent state/audit logs, and orchestrator context snapshots. Queried via grep. |
| **Quota/context monitor** | After each response, checks context size against a threshold and checks API quota against the rolling window. Triggers compaction or pause as needed. |
| **Learnings store** | Two tiers: a dynamic in-session learnings file, and a long-term persistent memory written during the retro. |

### 2.2 State & communication model

- **Agent self-reporting.** Each sub-agent launches with boilerplate instructions to maintain its own tracking/audit file in a standard format and location. The orchestrator queries these files through a common interface and assumes they're kept current.
- **Escalation contract.** Sub-agents are instructed to (a) complete as much work as possible before returning, to avoid clogging orchestrator context, and (b) report back immediately on blockers, including discoveries that invalidate the orchestration plan (e.g. "this is actually sequential, not parallel").
- **Direct query fallback.** If a tracking file looks stale or an agent appears frozen, the orchestrator can query the agent directly.
- **Context discipline.** The orchestrator checks its context window after each response. Above a configurable threshold (default ~300k tokens), it reviews resident context and offloads/compacts to temp files it can later grep, rather than re-reading wholesale.

### 2.3 Loop semantics

When the orchestrator sets up a loop, it defines:

- An **explicit end-condition state** — the loop terminates when this is met.
- A **time limit** — default 24 hours **per loop** (user-configurable; user can ask Loopita to update defaults). Sequential loops each get their own budget.
- **Quota awareness** — if a loop is expected to run long or use many agents, the orchestrator tracks API quota and can pause and resume on the 5-hour rolling window reset.

### 2.4 Parallelization model

- Work is parallelized by default where it makes sense, using **Git worktrees** as the isolation primitive.
- Each agent owns a **specific, non-overlapping scope** and commits to its own worktree.
- The **merge agent** runs after parallel agents complete, reconciles worktrees, resolves conflicts, and passes the merged result to the orchestrator for review and sign-off.

### 2.5 Pause / resume model

Two distinct cases, both **within a single session** (no cross-session survival):

1. **Graceful pause (user-requested).** User asks Loopita to set a clean pause point. Loopita checkpoints all agent state, then resumes on `resume`.
2. **Crash recovery (transient failure).** Quota exhaustion, dropped connection, or a failed/stalled agent. Loopita recovers from the latest agent-logged state and continues.

Out of scope: surviving a fully closed session. If the user closes the session without a checkpoint, that state is lost.

### 2.6 Learning model (two tiers)

- **Tier 1 — Dynamic in-session learnings.** A learnings file updated *during* the run. The orchestrator and agents are fed these learnings as the loop/session progresses, so mistakes and discoveries propagate in real time.
- **Tier 2 — Long-term persistent learnings.** During a **retro phase** after completion, the orchestrator reviews the full conversation and audit logs, distills durable lessons, and writes them to a persistent store (project file, user memories, and/or a dedicated skill memory). On the next invocation, Loopita reads this store and applies the heuristics to its initial strategy decisions.

Learnings target both **orchestration level** ("parallel code review conflicts on this codebase → sequence it") and **agent level** ("Agent type Z keeps making mistake M → corrected prompt").

### 2.7 Reporting

On completion (end-condition met, agents merged, work committed), Loopita presents a **final report**:

- Token usage and elapsed time, broken down per task and per sub-agent.
- The technique/loop employed for each unit of work.
- Blockers encountered and how they were resolved.

The user can then **query Loopita as a retro** for additional detail.

---

## 3. Phased Implementation Plan

Each phase ends with a demonstrable proof of the concept it introduces. Later phases build on earlier ones.

### Phase 0 — Repo & skill scaffolding

**Goal:** A working, installable Claude Code skill skeleton in a new `loopita` repo.

**Deliverables**

- New Git repo `loopita` with standard structure (skill manifest/`SKILL.md`, scripts dir, docs).
- Minimal skill that registers and can be invoked, prints its purpose, and confirms it's active.
- Configuration scaffold (defaults: context threshold ~300k, per-loop time limit 24h, quota window 5h) — user-overridable.
- Conventions doc: temp-file layout, agent tracking-file format, logging schema.

**Proof:** invoking the skill in a Claude Code session activates Loopita and echoes its config.

---

### Phase 1 — Foundation: persistence, single sub-agent, tracking

**Goal:** Prove the orchestrator → scoped sub-agent → signal-back loop, plus the persistence/tracking layer. No parallelism yet.

**Deliverables**

- **Persistence layer:** read/write/grep over temp files for (a) the task list, (b) per-agent state/audit logs, (c) orchestrator context snapshots.
- **Agent spawn mechanism:** orchestrator spawns a single sub-agent with a narrow brief and the boilerplate self-reporting instructions.
- **Tracking-file interface:** standard format + a query interface the orchestrator uses to read agent progress; direct-query fallback when a file looks stale.
- **Signal contract:** agent returns only the orchestrator-required output; completes as much as possible before returning.
- **Basic linear strategy:** orchestrator handles a simple task as a single iterative pass through one agent.

**Proof:** give Loopita a small task; it spawns one agent, the agent logs progress and returns minimal signal, the orchestrator reads the log and reports completion — all while keeping main-thread context lean.

---

### Phase 2 — Context & quota self-management

**Goal:** The orchestrator manages its own context budget and becomes quota-aware.

**Deliverables**

- **Post-response context check:** after each response, measure context size; above threshold, review and compact/offload to temp files (grep-retrievable).
- **Compaction policy:** rules for what gets flushed vs. kept resident; retrieval-on-demand via grep rather than wholesale re-read.
- **Quota monitor:** track API usage against the 5-hour rolling window; detect approaching limits.
- **Loop setup primitives:** explicit end-condition state + per-loop time limit (default 24h, configurable).

**Proof:** drive context above threshold and watch the orchestrator compact to temp files and continue without losing necessary state; simulate approaching quota and confirm detection.

---

### Phase 3 — Parallelism: worktrees + merge agent

**Goal:** Parallel execution with isolation and reconciliation.

**Deliverables**

- **Worktree orchestration:** spawn N agents, each in its own Git worktree with a specific, non-overlapping scope.
- **Merge agent:** dedicated agent that merges completed worktrees, resolves conflicts, and hands the result to the orchestrator.
- **Orchestrator sign-off:** review step before final commit.
- **Strategy selector v1:** decide linear vs. loop vs. swarm based on task analysis (model as final arbiter).
- **Adaptive replanning:** handle a sub-agent signal that the plan must change (e.g. parallel → sequential) and adjust.

**Proof:** give Loopita a feature that decomposes into independent slices; it runs them in parallel worktrees, the merge agent reconciles, the orchestrator signs off and commits.

---

### Phase 4 — Pause / resume & crash recovery

**Goal:** Clean, within-session resumption.

**Deliverables**

- **Checkpointing:** serialize full orchestration state (per-agent: done / in-progress / pending, merge status) to temp files.
- **Graceful pause command:** user-requested clean pause point + `resume`.
- **Crash recovery:** resume from latest agent-logged state after quota exhaustion, dropped connection, or stalled/failed agent.
- **Quota pause/resume:** auto-pause when quota is about to be exhausted; auto-resume on rolling-window reset.

**Proof:** mid-workflow, (a) user pauses and later resumes cleanly; (b) simulate a dropped connection / quota hit and confirm Loopita resumes from the correct per-agent state without redoing completed work.

---

### Phase 5 — Reporting & auditing

**Goal:** Full visibility on completion.

**Deliverables**

- **Final report:** per-task and per-agent token usage + elapsed time; technique/loop used per unit; blockers and resolutions.
- **Retro query mode:** user can interrogate Loopita post-run for additional detail.
- **Structured audit log:** queryable record across agents and loops underpinning the report.

**Proof:** complete a multi-agent run and produce a clean report; ask follow-up retro questions and get accurate answers from the audit log.

---

### Phase 6 — Learning system (two tiers)

**Goal:** Continuous improvement across runs.

**Deliverables**

- **Tier 1 — dynamic learnings file:** updated during the run; fed to orchestrator and agents in real time.
- **Tier 2 — retro distillation:** post-run analysis of conversation + audit logs into durable lessons.
- **Persistent learnings store:** write durable lessons to project file / user memories / dedicated skill memory.
- **Apply-on-next-run:** read learnings at invocation and bake into initial strategy.
- **Dual-target learnings:** orchestration-level heuristics and agent-level prompt corrections.

**Proof:** run a workflow that hits a known failure mode; confirm Tier-1 propagation mid-run, a retro that records the lesson, and that the *next* invocation applies the heuristic.

---

### Phase 7 — Hardening & production patterns

**Goal:** Make it robust and pleasant to operate.

**Deliverables**

- Failure-mode handling for malformed agent logs, partial merges, orphaned worktrees, repeated agent stalls.
- Cleanup routines (temp files, stale worktrees).
- Tuning of thresholds/defaults from real usage.
- Research-workflow adaptation (parallel investigation tracks, synthesis instead of merge).
- Docs: usage guide, configuration reference, troubleshooting.

**Proof:** sustained real-world runs (SE and research) without manual cleanup; documented configuration and recovery behavior.

---

## 4. Phase 1 Task Breakdown (ready-to-implement detail)

> Phase 1 is the foundational milestone. This is the level of detail to bring each subsequent phase to before handing it to an implementing LLM.

**M1.1 — Persistence layer**

- Define the temp-file directory layout and naming convention.
- Implement write/read/append helpers for task list, agent logs, and context snapshots.
- Implement grep-based query over temp files.
- Define the JSON/markdown schema for each file type.

**M1.2 — Agent tracking-file format & interface**

- Specify the standard tracking-file schema: agent id, scope, status (`pending`/`in-progress`/`done`/`blocked`), progress notes, timestamps, output summary.
- Implement the orchestrator-side query interface that reads/parses these files.
- Implement the direct-query fallback path for stale/frozen agents.

**M1.3 — Sub-agent spawn + boilerplate contract**

- Implement spawning a single scoped sub-agent with a narrow brief.
- Write the boilerplate instruction block injected into every agent: maintain your tracking file, complete as much as possible before returning, return only required signal, escalate blockers immediately.
- Define the signal-return format the orchestrator consumes.

**M1.4 — Linear strategy execution**

- Implement the simplest end-to-end path: orchestrator takes a task, spawns one agent, monitors via tracking file, collects signal, reports completion.
- Keep orchestrator context minimal throughout (no agent internals retained).

**M1.5 — Phase 1 validation**

- Define a small canonical test task (SE flavored).
- Confirm: agent logs correctly, orchestrator reads logs, signal returns minimal, main-thread context stays lean.

**Phase 1 dependencies:** M1.1 → (M1.2, M1.3) → M1.4 → M1.5. Persistence first; tracking format and spawn can proceed together; linear execution ties them; validation last.

---

## 5. Open Questions / To Refine Later

- Exact compaction policy heuristics (Phase 2): what's always-flushed vs. always-resident.
- How strategy selector weighs learned heuristics vs. fresh model judgment (Phase 3/6).
- Precise location(s) of the long-term learnings store and conflict handling if multiple stores disagree (Phase 6).
- Research-workflow analog of the merge agent — synthesis/aggregation step (Phase 7).
- Concurrency ceiling: max parallel agents before diminishing returns / quota pressure.

---

## 6. Out of Scope (v1)

- Surviving a fully closed session (no cross-session persistence of live workflow state).
- Multi-user / shared orchestration.
- Non-Claude-Code execution environments.

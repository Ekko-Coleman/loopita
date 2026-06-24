# Loopita

> *loop* + *Lupita* — a meta-orchestration skill for Claude Code.

Loopita is a Claude Code **skill** you invoke at the start of a task. It sits between you and the
model as a lean coordinator: it analyzes the task, selects an execution strategy — a single
iterative pass, a loop with an explicit end condition, or a swarm of parallel sub-agents — and
drives that strategy to completion while keeping its own context small. It persists state to
files, reports a detailed audit on completion, and learns across runs.

## Design stance: a thin layer over the engine

Claude Code already provides the heavy machinery — the **`Workflow`** tool (parallel fan-out,
pipelines, git-worktree isolation, token budgets, resume), **`/loop` + `ScheduleWakeup`** (looping
with explicit exit conditions and quota-aware pacing), and **sub-agents via `Task`**. Loopita does
**not** reimplement those. It is a set of instructions (`SKILL.md` + `references/`) that teach the
orchestrating Claude *when and how* to drive them, plus small Python scripts that fill the gaps the
engine doesn't cover: durable task/agent/audit state, tracking-file query, report assembly, and a
two-tier learnings store.

A skill is markdown + scripts, **not a running daemon**. So "monitor context after each response"
and "track quota" are *instructions the orchestrator follows*, not autonomous background loops.
Loopita is honest about that boundary.

## Install

Loopita installs like any personal skill — symlink (or copy) this repo into your skills directory:

```bash
ln -s "$(pwd)" ~/.claude/skills/loopita
```

Then start a Claude Code session and describe a task; Loopita activates when the work warrants
orchestration (multi-step builds, long loops, parallelizable features, research sweeps).

## Layout

| Path | Role |
|------|------|
| `SKILL.md` | Orchestrator brain: invoke → select strategy → drive primitives → report → retro. |
| `config/defaults.json` | Defaults: context threshold (300k), per-loop limit (24h), quota window (5h). User-overridable. |
| `references/conventions.md` | **The contract** — runtime file layout + tracking/audit/task schemas. |
| `references/*.md` | Strategy selection, sub-agent contract, pause/resume, reporting, learning. |
| `scripts/*.py` | `state`, `tracking`, `audit`, `report`, `learnings` — stdlib-only helpers. |
| `evals/evals.json` | skill-creator test orchestration tasks. |

## Configuration

Edit `config/defaults.json`, or ask Loopita to update a default during a session. Runtime state is
written under `.loopita/` (git-ignored) in your project.

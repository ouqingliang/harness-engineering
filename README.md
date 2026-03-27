# Harness Engineering

This directory contains the harness runtime for the repository.

`Harness` means the execution shell around a small set of agents. Its job is to keep the loop moving without turning routine blockers into human work.

## Purpose

Harness Engineering exists to answer a different question from the product code under `src/center/`, `src/client/`, and `src/engineer/`.

- product code answers how the product behaves
- harness code answers how agents keep working over time without constant human interruption

The harness is responsible for:

- deciding which agent runs next
- routing `audit` back through `supervisor`
- passing compact handoffs to agents
- recording enough runtime state to resume after failure or restart
- handling ordinary blockers automatically
- sending only explicit decision gates to the human
- managing git worktrees for every document- or code-mutating agent
- refusing to mark work done before the required verification has actually passed
- recording frozen architecture facts in durable docs the harness can re-read
- scheduling cleanup at round boundaries and on a longer maintenance cadence

It is not responsible for:

- redefining the product architecture
- introducing a large protocol stack
- turning every agent interaction into a human workflow

## Top-Level Shape

```text
harness-engineering/
  AGENT.md
  README.md
  config.yaml
  main.py
  agents/
    supervisor/
    audit-agent/
    cleanup-agent/
    communication-agent/
    design-agent/
    execution-agent/
  lib/
  memory/
    index.md
    doc/
  protocols/
  runners/
```

## Core Roles

All role definitions live under `agents/`.

- `supervisor`
  - the only scheduler
- `design`
  - turns the current goal into a concrete slice
- `execution`
  - does the main implementation work
- `audit`
  - checks whether the slice is actually acceptable
- `cleanup`
  - handles round-close, recovery, and periodic maintenance cleanup
- `communication`
  - the only human-facing path and the only presentation layer for human decisions

## Runtime Entry

`main.py` is the only supported runtime entry.

Current commands:

- `python main.py inspect`
  - print the local agent topology
- `python main.py run --doc-root <path>`
  - load the UTF-8 planning and design docs under `<path>`
  - initialize `<memory_root>/.harness/`
  - start or resume the long-running supervisor loop
  - auto-answer ordinary blockers
  - keep the process alive until you stop it yourself
- `python main.py reply --memory-root <path> --gate-id <id> --message "<text>"`
  - write a human answer back into `.harness/answers/`
- `python main.py status --memory-root <path>`
  - inspect the current mission and runtime state

## Target Runtime Shape

The target runtime is supervisor-centered, not a fixed pipeline.

- `supervisor` is the only scheduler
- `design`, `execution`, and `audit` are all meant to be background-capable agents
- `audit` reports to `supervisor`, and `supervisor` decides whether to replan, retry, or accept
- `communication` is a side-channel used only after `supervisor` opens an explicit decision gate
- `cleanup` runs in three modes: round-close, recovery, and periodic maintenance
- every document- or code-mutating agent must work in a supervisor-managed worktree

The current implementation now uses that shape as the mainline runtime.

- `design`, `execution`, and `audit` all run through background launcher and polling paths
- `audit` reports verdicts back to `supervisor`, and `supervisor` decides whether to retry, replan, or accept
- document- and code-mutating agents run inside supervisor-managed worktrees
- human interaction stays behind explicit supervisor gates

Treat [memory/doc/harness-architecture.md](/C:/Users/oql/OneDrive/Study/AIMA-refactor/harness-engineering/memory/doc/harness-architecture.md) as the source of truth for the intended boundary.

## Runtime State

The active runtime namespace should be:

- `<memory_root>/.harness/`

That namespace is for runtime coordination only.
It should stay small and easy to reason about.

The core runtime uses these files and directories:

- `mission.json`
- `state.json`
- `handoffs/`
- `reports/`
- `questions/`
- `answers/`
- `artifacts/`
- `locks/`
- `launchers/`

If a simple file in `.harness/` is enough, prefer that over a larger abstraction.

## Text Encoding

Harness text files should be read and written as UTF-8.

This applies to:

- repository docs
- agent instructions
- handoffs
- reports
- questions
- answers
- other runtime text artifacts

## Human Escalation

The harness should stop and ask the human only when one of these classes appears:

- architecture contract change
- destructive data or file operation
- security or permission boundary change
- external side effect with cost or irreversible impact
- unresolved conflict between competing goals

Everything else should remain inside the harness.

## Verification Rule

This is a runtime rule, not just a harness-development rule.

When the harness works on any task:

- required verification must run before the task can be closed
- a full capability claim must include end-to-end testing for that capability
- `audit` should reopen work that lacks the required verification evidence

## One-Command Usage

Minimal local layout:

```text
your-project/
  docs/
    README.md
    architecture.md
    plan.md
  runtime/
```

- `docs/`
  - the UTF-8 planning and design documents the harness should read
- `runtime/`
  - a plain directory you give to `--memory-root`
  - the harness will create `runtime/.harness/` under it automatically

Run the harness from a project doc root:

```bash
python main.py run --doc-root path/to/project/docs --memory-root runtime-memory --reset
```

This command starts the human-facing behavior by default:

- it starts the local human reply page
- it prints the local URL
- it keeps running while waiting for a real human decision
- after the human replies, it continues automatically
- `completed` and `failed` are runtime states, not exit conditions
- the process stays alive for maintenance windows and future doc changes until you stop it manually

The communication surface exposes:

- `GET /`
- `GET /health`
- `GET /runtime`
- `GET /communication/messages`
- `GET /communication/gates`
- `POST /human/reply`
- `POST /communication/messages`
- `POST /communication/gates`
- `POST /communication/gates/{gate_id}/reply`

`GET /` is the human page.
Open the printed local URL in a browser, read the decision brief, and reply there directly.

The communication surface is for runtime inspection and human communication only.
`main.py run` remains the only supported runtime entry for advancing the harness state machine.
Human replies also go through `.harness/answers/`, so the runtime can resume after restart.

## Architecture Baseline

Harness should treat these documents as the current implementation baseline:

- `docs/designs/2026-03-25-task-centered-autonomous-ops-platform.md`
- `docs/designs/2026-03-25-center-task-and-conversation-model.md`
- `docs/designs/2026-03-25-center-subsystem-architecture-outline.md`
- `docs/designs/2026-03-25-client-and-worker-runtime-design.md`
- `docs/designs/2026-03-25-harness-engineering-integration.md`
- `docs/plans/2026-03-25-task-mainline-and-engineernode-removal.md`
- `harness-engineering/memory/doc/aima-refactor-implementation-baseline.md`

## Current Implementation

The runtime now includes:

- one scheduler under `lib/scheduler.py`
- one active runtime namespace under `<memory_root>/.harness/`
- one fixed handoff/report/question/answer path through JSON files
- one built-in low-level turn runner under `lib/runner_bridge.py`
- one supervisor-facing bridge under `lib/supervisor_bridge.py`
- one human-facing communication surface through `lib/communication_api.py` and `runners/codex_app_server.py`
- one shared background-agent launcher path for `design`, `execution`, and `audit`
- one multi-round work loop where accepted slices flow back into `design` until the selected planning doc has no remaining slices

The current implementation now supports:

- non-blocking `design`, `execution`, and `audit`
- supervisor-managed worktrees for mutating agents
- supervisor-routed audit verdicts and retry or replan decisions
- human escalation only through explicit gates

Current working model:

- `supervisor` owns the canonical repository checkout and the state machine
- modifying agents work inside supervisor-assigned git worktrees
- `audit` reports findings back to `supervisor` first
- `supervisor` then decides whether to reopen `execution` or send the slice back through `design`

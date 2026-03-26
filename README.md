# Harness Engineering

This directory is the home of the repository's Harness Engineering runtime.

Here, `Harness` means the execution shell around a small set of agents.
It is the part that keeps the loop running.

## Purpose

Harness Engineering exists to solve a different problem from the product code under `src/center/`, `src/client/`, and `src/engineer/`.

- product code answers: how the product behaves
- harness code answers: how agents keep working for a long time without constantly stopping to ask the human

The harness is responsible for:

- deciding which agent runs next
- passing a small handoff to that agent
- recording enough runtime state to resume after failure or restart
- handling ordinary blockers automatically
- sending only real decision gates to the human
- refusing to mark work done before the required verification has actually passed
- recording important frozen architecture facts in durable docs the harness can re-read
- scheduling cleanup both at round boundaries and on a longer maintenance cadence

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
  - load the UTF-8 planning/design docs under `<path>`
  - initialize `<memory_root>/.harness/`
  - start or resume the long-running supervisor loop
  - auto-answer ordinary blockers
  - keep the process alive until you stop it yourself
- `python main.py reply --memory-root <path> --gate-id <id> --message "<text>"`
  - write a human answer back into `.harness/answers/`
- `python main.py status --memory-root <path>`
  - inspect the current mission and runtime state

## Target Runtime Shape

The approved target runtime is supervisor-centered, not a fixed pipeline.

- `supervisor` owns the state machine and is the only component allowed to decide whether the human is needed
- `design`, `execution`, and `audit` form the main work loop
- `communication` is a side-channel used only after `supervisor` opens a decision brief
- `cleanup` runs in three modes: round-close, recovery, and periodic maintenance

The current code now follows this runtime shape.
The main remaining gap is that much of the role behavior still lives inside `lib/scheduler.py`.
Treat the architecture doc under [memory/doc/harness-architecture.md](/C:/Users/oql/OneDrive/Study/AIMA-refactor/harness-engineering/memory/doc/harness-architecture.md) as the source of truth for the intended boundary.

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

This command now does the human-facing behavior by default:

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
- one execution path that calls `codex exec` inside the target project root, with a prompt that explicitly tells the execution worker to use subagents for modification work
- one multi-round work loop where accepted slices flow back into `design` until the selected planning doc has no remaining slices

The current implementation still keeps the runtime deliberately thin.
It does not add a larger protocol layer or a second architecture stack around the loop.

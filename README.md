# Harness Engineering

This directory is the independent home for the repository's Harness Engineering system.

It is the control shell that will let multiple AI workers keep working for long stretches, pause only at explicit decision gates, and stay grounded in center-owned memory, audit, and artifacts.

## Purpose

Harness Engineering exists to solve a different problem from the current product code under `src/center/`, `src/client/`, and `src/engineer/`.

- product code answers: how do `Center` and `Client` deliver real support and task execution
- harness code answers: how do AI workers keep designing, implementing, reviewing, cleaning up, and escalating correctly over long-running work

The active design baseline in this directory is informed by:

- OpenAI, [Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/)
- OpenAI, [Unlocking the Codex harness: how we built the App Server](https://openai.com/index/unlocking-the-codex-harness/)
- OpenAI, [Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)
- OpenAI, [From model to agent: Equipping the Responses API with a computer environment](https://openai.com/index/equip-responses-api-computer-environment/)

## Top-Level Shape

```text
harness-engineering/
  AGENT.md
  README.md
  config.yaml
  main.py
  lib/
  supervisor/
  audit-agent/
  cleanup-agent/
  communication-agent/
  design-agent/
  execution-agent/
  memory/
    index.md
    doc/
  protocols/
  runners/
```

## Core Roles

- `supervisor`
  - owns orchestration order, state transitions, retries, decision gates, and stop conditions
- `design`
  - turns goals into architecture, contracts, plans, and work orders
- `execution`
  - performs the main implementation work against approved contracts
- `audit`
  - checks code, tests, risk, regressions, and acceptance evidence
- `cleanup`
  - compresses context, removes drift, fixes stale docs, and keeps the workspace reusable
- `communication`
  - speaks to the human only when a decision gate or significant status change requires it

## Control Model

The intended long-running loop is:

1. `supervisor` loads the active mission, memory root, and current workspace state.
2. `design` writes or updates the current implementation contract.
3. `execution` works the contract.
4. `audit` verifies results and either accepts or reopens work.
5. `cleanup` compresses artifacts, removes stale state, and updates durable memory.
6. `communication` opens a human gate only for important decisions.

## Decision Gates

The harness should stop and ask the human only when one of these classes appears:

- architecture contract change
- destructive data or file operation
- security or permission boundary change
- external side effect with cost or irreversible impact
- unresolved conflict between competing product goals

Everything else should remain inside the autonomous loop.

## Memory Boundary

This directory carries the harness method, role contracts, and local scaffolding.

The long-term execution memory for real project work should remain center-owned.
That means the deployed harness will point its active `memory_root` to a center-managed namespace rather than treating this directory as the system of record for task history.

## Current Scope

This first scaffold does three things:

- defines the harness architecture and agent roles
- creates a dedicated directory layout separate from the old `EngineerNode` model
- provides a minimal supervisor CLI that can inspect and validate the local agent topology

The intended runtime namespace for active execution state is:

- `<memory_root>/.harness/`

It does not yet implement the full long-running runtime, App Server bridge, or center synchronization loop.

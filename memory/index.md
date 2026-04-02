# Harness Memory Index

This directory holds the durable method memory for `harness-engineering/`.

## Documents

### Architecture

- `doc/architecture/harness-architecture.md`
  - approved architecture baseline for the supervisor-centered runtime
- `doc/architecture/harness-architecture-redesign.md`
  - frozen redesign baseline for the target harness coordination model, worker contract, and storage direction
- `doc/architecture/harness-architecture-detailed/README.md`
  - navigation for the detailed architecture doc set, including its thin supervisor-centered, artifact-first design philosophy
- `doc/architecture/harness-architecture-detailed/01-runtime-overview.md`
  - runtime boundary, ownership model, and design philosophy
- `doc/architecture/harness-architecture-detailed/02-supervisor-runtime.md`
  - supervisor coordination, state flow, and routing outcomes
- `doc/architecture/harness-architecture-detailed/03-worker-session-contract.md`
  - shared worker contract for research, design, execution, verification, decision, and cleanup work
- `doc/architecture/harness-architecture-detailed/04-human-gate-and-communication-surface.md`
  - human communication surface and gate relationship
- `doc/architecture/harness-architecture-detailed/agents/decision-agent.md`
  - decision-agent responsibilities
- `doc/architecture/harness-architecture-detailed/agents/research-agent.md`
  - research-agent role
- `doc/architecture/harness-architecture-detailed/agents/design-agent.md`
  - design-agent role
- `doc/architecture/harness-architecture-detailed/agents/execution-agent.md`
  - execution-agent role
- `doc/architecture/harness-architecture-detailed/agents/verification-agent.md`
  - verification-agent role
- `doc/architecture/harness-architecture-detailed/agents/cleanup-agent.md`
  - cleanup-agent role

### Baselines

- `doc/baselines/aima-refactor-implementation-baseline.md`
  - frozen product-architecture baseline for harness work

### Implementation

- `doc/implementation/harness-long-running-runtime-implementation.md`
  - implementation plan for the supervisor-centered runtime

### Research

- `doc/research/memory_paper/paper_list.md`
  - research-paper reading list and supporting references

## Daily Notes

- `daily-notes/2026-03-26-harness-engineering-status.md`
  - harness status and design drift notes
- `daily-notes/2026-03-26-supervisor-state-machine-runtime-update.md`
  - supervisor state-machine runtime notes
- `daily-notes/2026-03-26-supervisor-state-machine-implementation.md`
  - supervisor work-loop baseline notes
- `daily-notes/2026-03-26-long-running-execution-worker-update.md`
  - long-running execution worker notes

## Rule

Keep durable harness knowledge here.
Do not leave architecture truth only in transient chat or run logs.

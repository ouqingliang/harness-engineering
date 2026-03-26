# AIMA Refactor Implementation Baseline

## Purpose

This document records the architecture decisions that `Harness Engineering` should treat as the implementation baseline for `AIMA-refactor`.

All future implementation work should start from this record unless a newer approved document supersedes it.

## Frozen Architecture Decisions

- `Center` is the only control and truth center
- `Task` is the only business mainline
- each `Task` owns one unified `Conversation`
- `Engineer` is a long-lived AI worker identity
- `Harness Engineering` is the implementation method, not a business object
- `EngineerNode`, `SupportTicket`, and `SupportSession` are legacy removal scope

## Client Runtime Direction

- do not freeze the current `src/client/` implementation shape as the target architecture
- use `AIMA-service-new/apps/cli` as the reference client architecture
- the target client layering is:
  - `bootstrap`
  - `api`
  - `manifest`
  - `state`
  - `session`
  - `owner_runtime`
  - `runtime`

## Worker Runtime Direction

- worker means the active `Engineer` runtime operated through harness
- the worker runtime must support realtime message injection from `Center`
- the worker runtime must support remote command interception and replay through `Center`

## Realtime Interaction Requirement

This requirement is mandatory:

- `Client` must be able to send realtime messages into the active worker task
- `Center` must record those messages in `Conversation`
- `Engineer Runtime Gateway` must inject those messages into the active worker thread
- worker follow-up questions must be deliverable back to `Client` through `Center`

## Memory Service Direction

`Memory Service` must use the `auto-meta-agent` architecture as the baseline.

Required shape:

- durable memory tree under a center-owned `memory_root`
- runtime coordination under `<memory_root>/.agents/`
- per-agent namespaces for:
  - `state/`
  - `work-orders/`
  - `reports/`
  - `launchers/`
- promotion flow from transient execution output into durable memory

`Harness Engineering` may use a `.harness/` runtime namespace for its own execution shell, but center-owned memory remains the long-term source of truth.

## Center Subsystems That Still Need Detailed Design

- `Client Gateway`
- `Engineer Runtime Gateway`
- `Memory Service`
- `Monitoring Web`

These should be expanded before implementation slices begin in those areas.

## Execution Governance

- all implementation tasks should be executed through `Harness Engineering`
- design, execution, audit, cleanup, and communication should remain role-separated
- only decision gates should come back to the human

## Primary Documents

Harness should read these documents first:

1. `docs/designs/2026-03-25-task-centered-autonomous-ops-platform.md`
2. `docs/designs/2026-03-25-center-task-and-conversation-model.md`
3. `docs/designs/2026-03-25-center-subsystem-architecture-outline.md`
4. `docs/designs/2026-03-25-client-and-worker-runtime-design.md`
5. `docs/designs/2026-03-25-harness-engineering-integration.md`
6. `docs/plans/2026-03-25-task-mainline-and-engineernode-removal.md`

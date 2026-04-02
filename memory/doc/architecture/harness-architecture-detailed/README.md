# Harness Architecture Detailed Set

> Document type: detailed architecture docs
> Purpose: expand the frozen redesign baseline into detailed runtime, coordination, and worker-role documentation
> Scope: `harness-engineering/`

## Purpose

This directory expands the frozen baseline in [`../harness-architecture-redesign.md`](../harness-architecture-redesign.md) into a detailed design set for the target worker set: decision, design, execution, verification, and cleanup.

The baseline file is the source of truth for the target architecture and is not rewritten here. These files provide the supporting detail for that baseline.

## Scope

This set covers the target harness boundary, supervisor control plane, human communication surface, shared worker contract, and specialist worker roles.

It does not define a competing architecture, a migration plan, or implementation shortcuts.

## Design Philosophy

This architecture is intentionally thin, supervisor-centered, artifact-first, and resistant to protocol bloat.

It keeps the unified session contract small, pushes durable evidence into file-backed artifacts, and treats the communication surface as a narrow human I/O layer rather than a second control plane.

## Document Map

- [`01-runtime-overview.md`](./01-runtime-overview.md)
  - defines the runtime boundary and ownership model
- [`02-supervisor-runtime.md`](./02-supervisor-runtime.md)
  - defines supervisor coordination, state flow, and routing outcomes
- [`03-worker-session-contract.md`](./03-worker-session-contract.md)
  - defines the shared worker contract for decision, design, execution, verification, and cleanup work
- [`04-human-gate-and-communication-surface.md`](./04-human-gate-and-communication-surface.md)
  - defines the human communication surface and gate relationship
- [`agents/decision-agent.md`](./agents/decision-agent.md)
  - defines decision-agent responsibilities
- [`agents/design-agent.md`](./agents/design-agent.md)
  - defines the design-agent role
- [`agents/execution-agent.md`](./agents/execution-agent.md)
  - defines the execution-agent role
- [`agents/verification-agent.md`](./agents/verification-agent.md)
  - defines the verification-agent role
- [`agents/cleanup-agent.md`](./agents/cleanup-agent.md)
  - defines the cleanup-agent role
- [`../../../index.md`](../../../index.md)
  - indexes this detailed design set in the durable memory catalog

## Relationship Between Files

`01-runtime-overview.md` covers the runtime boundary and ownership model.

`02-supervisor-runtime.md` covers supervisor coordination, state flow, and routing.

`03-worker-session-contract.md` covers the shared worker contract.

`04-human-gate-and-communication-surface.md` covers the human communication surface and gate relationship.

The `agents/` directory covers the role-specific responsibilities for each specialist worker.

## Frozen Baseline Rule

The redesign baseline is frozen.

- Do not reinterpret the baseline as a moving target.
- Do not reintroduce legacy role names as current-state target roles.
- Do not add alternate communication paths when the baseline already names the supervisor as the control plane.
- Do not turn the human communication surface into a worker lane.

## Writing Convention

Use stable object names, short declarative sections, and explicit ownership.

Prefer wording that can be reused in plans, implementation notes, and review discussions without needing translation.

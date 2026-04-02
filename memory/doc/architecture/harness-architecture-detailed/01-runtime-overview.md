# Runtime Overview

> Document type: detailed architecture design
> Purpose: define the harness runtime boundary and the baseline storage sketch

## Design Philosophy

This runtime is intentionally thin, supervisor-centered, artifact-first, and resistant to protocol bloat.

Its job is to keep the unified session contract small, keep durable evidence file-backed, and keep the communication surface narrow.

## Target Boundary

The runtime is supervisor-centered. It coordinates worker sessions, event publication, artifact indexing, and human-gate routing.

The runtime does not own the project code under test.

## Ownership

The supervisor owns control, session state, and routing.

Workers own role-local execution and durable artifacts.

The human I/O surface is runtime-owned and presents supervisor-owned decisions.

## Runtime Flow

1. The supervisor loads `mission.json` and the current runtime state from `.harness/`.
2. The supervisor selects a role and creates or reuses a session for that role.
3. The supervisor writes the control message into the role inbox and records the event on the event bus.
4. The worker performs the requested slice inside its worktree and writes durable artifacts.
5. The worker returns a thin task notification to the supervisor.
6. The supervisor decides whether to `continue` the same session, route to another worker, open a human gate, or `terminate` the session.
7. The supervisor records the resulting state transition and artifact references.

## Storage Layout

The baseline storage sketch is:

```text
.harness/
  events/
    supervisor-inbox.jsonl
  sessions/
    research/
    design/
    execution/
    verification/
    decision/
  inbox/
    research/
    design/
    execution/
    verification/
    decision/
  artifacts/
    research/
    design/
    execution/
    verification/
    decision/
  gates/
  briefs/
  worktrees/
  state.json
  mission.json
```

This sketch keeps the primary coordination lanes explicit. It does not freeze extra lanes or transport-level details.

## Deliberately Out Of Scope

This document does not define direct worker-to-worker messaging, a separate communication role, a separate audit role, transport-level implementation details, UI design for the human I/O surface, or project-specific code changes under test.

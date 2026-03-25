# Harness Architecture

## Purpose

This document defines the independent Harness Engineering system for this repository.

It does not redefine product runtime behavior inside `center/`, `client/`, or `engineer/`.
It defines how AI workers should be orchestrated so they can keep shipping code, reviewing work, cleaning drift, and escalating only at explicit decision gates.

## Boundary

`Harness Engineering` is the orchestration shell around AI work.

- it owns worker role separation
- it owns orchestration policy
- it owns decision gate policy
- it owns task artifact discipline
- it does not replace `Center` as product truth

## Main Actors

### Supervisor

Owner:
- harness runtime

Responsibilities:
- load active mission and current state
- decide which specialist agent runs next
- stop duplicate or conflicting work
- open a human gate only for approved decision classes
- record run status and handoff state

### Design Agent

Owner:
- architecture and contract generation

Responsibilities:
- turn a goal into a scoped design
- define contracts before parallel execution
- update implementation plans and work orders

Outputs:
- design notes
- contracts
- execution plans

### Execution Agent

Owner:
- main implementation work

Responsibilities:
- follow the current contract
- make code changes
- run targeted verification
- write execution artifacts for downstream review

Outputs:
- code changes
- targeted verification evidence
- implementation reports

### Audit Agent

Owner:
- acceptance quality and risk control

Responsibilities:
- perform review-first validation
- identify regressions, missing tests, and contract violations
- accept or reopen work

Outputs:
- findings
- acceptance status
- risk notes

### Cleanup Agent

Owner:
- drift control and context compression

Responsibilities:
- remove stale run state
- compress context into durable memory
- clean outdated docs and temporary artifacts

Outputs:
- cleanup reports
- updated durable memory

### Communication Agent

Owner:
- human-facing interaction

Responsibilities:
- summarize progress for the human
- surface decision gates
- ask only for important choices
- preserve resolved decisions as durable artifacts

Outputs:
- decision requests
- status briefs
- resolution records

## Runtime Objects

The harness should treat these as first-class runtime objects:

- `Mission`
  - the long-lived goal the harness is pursuing
- `WorkOrder`
  - one scoped unit handed to a specialist agent
- `DecisionGate`
  - a blocked point that requires human judgment
- `RunArtifact`
  - logs, diffs, screenshots, reports, and structured output produced during a run
- `MemoryEntry`
  - durable knowledge promoted out of transient execution

## Orchestration Loop

1. supervisor reads the mission and current memory
2. design creates or refreshes the active contract
3. execution works the contract
4. audit validates the result
5. cleanup compresses state and removes drift
6. communication speaks to the human only if a decision gate or milestone requires it

The active runtime namespace should be:

- `<memory_root>/.harness/`

That namespace should contain:

- `work-orders/`
- `state/`
- `reports/`
- `artifacts/`
- `launchers/`

## Decision Gate Policy

The harness must escalate only for:

- architecture contract changes
- destructive file or data operations
- security boundary changes
- external side effects with cost or irreversible impact
- unresolved conflicts between project goals

## Memory Policy

The deployed memory root should be center-owned.
This repository-local folder stores harness method memory and scaffolding, not the final system of record for production task history.

## Current Implementation Decision

The first implementation carrier is a dedicated top-level directory:

- `harness-engineering/`

The harness should remain separate from the old `EngineerNode` structure.
Any reusable code from the old runtime must be migrated into the harness under new contracts rather than keeping the old object model alive.

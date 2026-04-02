# Supervisor Runtime

> Document type: detailed architecture design
> Purpose: restate the frozen supervisor runtime baseline without adding extra protocol or state machinery

## Purpose

This document expands the frozen redesign baseline for the `supervisor` runtime.

It keeps only the runtime truths that the baseline freezes:

- `supervisor` is the sole scheduler
- the runtime is asynchronous and event-driven
- routing outcomes stay thin
- `supervisor` owns worktree, session, gate, and brief lifecycle
- `supervisor` has hard boundaries and must not absorb specialist work

This document does not add a larger event taxonomy, a richer round FSM, or role-specific routing heuristics beyond the baseline.

## Design Idea

The runtime keeps one scheduler and a deliberately thin public coordination contract.

- `supervisor` is the only scheduler and the only control-plane writer
- public worker control stays small instead of growing chat-like coordination semantics
- `continue` means continue the same session; recovery details stay below the public contract
- durable artifacts plus runtime events carry coordination state instead of hidden side memory
- human communication stays at the surface boundary, not as another worker lane

## Frozen Runtime Truths

The supervisor is explicitly asynchronous.

It does not block on a single worker conversation and it does not act like a chat coordinator. It consumes runtime events, updates durable control state, publishes the next control action, and then waits for more input.

The supervisor is also the only control-plane writer for the runtime. Workers and the communication surface can emit events, but they do not advance round truth on their own.

That means:

- worker completion, failure, and blocker reports enter the runtime as events
- human messages enter the runtime as events through the communication surface
- restart and recovery depend on persisted state, not hidden in-memory progress
- no worker may route another worker or mutate runtime state directly

## What The Supervisor Owns

The supervisor owns the runtime truth for:

- round progression
- worker session lifecycle
- worktree assignment and reuse
- artifact references used for routing
- gate publication and resolution
- brief publication
- routing decisions between specialist roles

This ownership stays narrow.

The supervisor does not become the primary author of research, design, execution, verification, or decision semantics. It owns control truth, not specialist truth.

The baseline still assumes durable files for artifacts and sessions. This document relies on that persisted runtime substrate without freezing any extra storage detail beyond the redesign baseline.

## High-Level Scheduler Loop

The scheduler loop should stay simple:

1. Read the latest durable runtime state.
2. Consume new inbound runtime events from workers or the communication surface.
3. Decide the next routing outcome for the affected round.
4. Persist the updated runtime state.
5. Emit the next thin control action, gate update, or wait state.

When worker action is required, the supervisor sends only the thin control messages frozen by the baseline: `spawn`, `continue`, or `terminate`.

At the architecture layer, `continue` remains the only public "keep going" action. Any internal distinction between continuing a live session and recovering an old transcript stays below this document.

## Thin Routing Outcomes

The supervisor should keep routing outcomes thin and explicit.

The baseline outcomes are:

- `accept`
- `reopen_execution`
- `replan_design`
- `route_to_decision`

These are supervisor decisions, not worker-declared states.

Workers report facts, evidence, and blockers. The supervisor turns those inputs into one of the outcomes above and then decides the next control message or gate action.

## Human And Decision Routing

The supervisor decides whether a blocker or human message should go to `decision-agent`.

The path stays simple:

1. a specialist reports a blocker or question, or a human message arrives
2. the supervisor decides whether decision semantics are needed
3. if needed, the supervisor routes to `decision-agent`
4. the supervisor then routes back to design, execution, completion, failure, or a human gate

The communication surface is therefore a runtime boundary, not a parallel scheduler.

## What The Supervisor Must Never Do

- never perform design, execution, verification, research, or decision work as a hidden side lane
- never edit repo code directly
- never allow worker-to-worker direct routing
- never let a worker own round, session, gate, worktree, or brief truth
- never treat free-form human text as already-normalized planning instruction when semantic judgment is still needed
- never expose `resume` as a separate architecture-level verb
- never recreate `communication-agent` semantics inside the supervisor

## Relationship To Other Detailed Docs

[`03-worker-session-contract.md`](./03-worker-session-contract.md) defines the shared worker-facing session contract.

[`04-human-gate-and-communication-surface.md`](./04-human-gate-and-communication-surface.md) defines the runtime-owned communication surface that captures human free text and returns it to the supervisor.

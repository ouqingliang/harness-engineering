# Human Gate And Communication Surface

> Document type: detailed architecture design
> Purpose: restate the frozen human-communication baseline without recreating a communication worker lane

## Purpose

This document expands the frozen redesign baseline for human interaction in the harness runtime.

It keeps only the baseline truths:

- communication is a runtime-owned surface, not a target worker role
- human free text is stored before interpretation
- unsolicited human messages are allowed
- the supervisor decides whether a message should go to `decision-agent`

This document does not freeze a larger gate-state machine or a more detailed storage schema than the baseline requires.

## Design Idea

Human communication is a runtime surface, not a worker lane.

- the `supervisor` remains the only scheduler; the surface only captures and presents runtime-owned communication
- human messages become durable records and supervisor-facing events before interpretation
- replies do not enter worker sessions directly; the supervisor decides whether to involve `decision-agent`
- keep this boundary thin and avoid rebuilding chat orchestration or hidden state here

## Communication Surface Boundary

The communication surface is the human I/O boundary of the runtime.

It is responsible for:

- displaying supervisor-owned gates and briefs
- accepting human free-text input
- durably storing raw communication records
- emitting runtime events that reference those records back to the supervisor

It is not responsible for:

- deciding when a gate should open
- interpreting blocker or reply semantics
- routing messages directly to workers
- changing round or session truth by itself

The communication surface stays thin. It is storage and delivery for runtime-owned human communication.

## Communication Is Not A Worker Role

`communication-agent` is not part of the target architecture.

Communication remains a surface because the responsibilities must stay split:

- gate and brief lifecycle belong to `supervisor`
- semantic interpretation belongs to `decision-agent` when needed
- message capture and rendering belong to the communication surface

This preserves the single-scheduler model and avoids creating a hidden peer worker lane for human interaction.

## Durable Records

The runtime keeps durable records for gates, briefs, and human messages.

This document intentionally does not prescribe a deeper per-gate directory contract. The frozen requirement is ownership and ordering:

- the supervisor owns gate and brief truth
- the communication surface stores raw human-visible messages durably
- supervisor-facing events point back to those durable records

The storage layout should stay as simple as the baseline.

## Gate And Reply Flow

The human path stays simple:

1. a specialist reports a blocker or question
2. the supervisor decides whether to route it to `decision-agent`
3. if human input is needed, the supervisor publishes a gate and brief
4. the communication surface renders that runtime-owned gate
5. the human replies in free text
6. the communication surface stores the raw reply before interpretation
7. the surface emits a supervisor-facing event
8. the supervisor decides whether the reply should go to `decision-agent`
9. the supervisor then routes the next action

The communication surface never decides the next worker route by itself.

## Raw Free Text Comes First

Human text is durable evidence.

The communication surface must:

- store the original human text before any interpretation
- preserve that raw text instead of overwriting it with a normalized summary
- keep later interpretation separate from the stored message

This applies to both gate replies and messages that arrive outside a gate.

## Unsolicited Human Messages

Humans may send messages even when no gate is open.

Those messages are still valid runtime input:

1. the communication surface stores the raw message
2. the surface emits a supervisor-facing event
3. the supervisor decides whether the message is a gate reply, a new external input, or something that should remain only as logged evidence
4. if semantic interpretation is needed, the supervisor routes to `decision-agent`

The system should not require an already-open gate before a human can speak.

## Hard Boundaries

- do not reintroduce `communication-agent` as a target role
- do not send human messages directly into worker sessions
- do not interpret human free text inside the communication surface
- do not let the surface mutate round, session, or gate truth on its own
- do not replace raw messages with normalized instructions in place
- do not add storage or state machinery here beyond what the frozen baseline requires

## Relationship To Other Detailed Docs

[`02-supervisor-runtime.md`](./02-supervisor-runtime.md) defines supervisor ownership of routing, worktrees, sessions, gates, and briefs.

[`03-worker-session-contract.md`](./03-worker-session-contract.md) defines the thin worker control contract that the supervisor uses after a gate or decision result is processed.

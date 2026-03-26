# 2026-03-26 Supervisor State Machine Runtime Update

> Document type: daily note
> Purpose: record the verified runtime truth after moving the harness away from the fixed pipeline

## Locked Changes

As of 2026-03-26, the harness runtime in this directory now behaves as follows:

- `supervisor` remains implemented inside `HarnessScheduler`, and owns dispatch truth
- the main work loop is `design -> execution -> audit`
- `communication-agent` is a supervisor-only side channel
- only `supervisor` decides whether a question becomes a human gate
- `cleanup-agent` supports `round-close`, `recovery`, and `maintenance`
- the HTTP surface now exposes a human reply page plus communication and runtime inspection
- `main.py run` remains the only supported entry for advancing the runtime

## Current Cooperation Model

The current specialist role boundaries are:

- `design-agent`
  - produces the current round contract or raises a question
- `execution-agent`
  - implements the contract and records verification evidence
- `audit-agent`
  - returns `accepted`, `reopen_execution`, or `replan_design`
- `communication-agent`
  - speaks to the human only when `supervisor` provides a communication brief
- `cleanup-agent`
  - runs only when `supervisor` schedules `round-close`, `recovery`, or `maintenance`

Specialist agents do not speak to the human directly.
Specialist agents do not call `communication-agent` directly.

## Verified Runtime Behavior

The runtime was verified with:

- `python -m unittest discover -s tests -v`
- `python main.py run --doc-root memory\\doc --memory-root runtime-memory-e2e --reset --no-browser`
- `python main.py run --doc-root memory\\doc --memory-root runtime-memory-serve --reset --no-browser`
- `python scripts\\harness\\run-soak.py --iterations 1`

Verified outcomes:

- the runtime completes a normal round through the supervisor state machine
- decision gates stop in `waiting_human`, expose a local human reply page, and resume after reply
- `cleanup-agent` runs in `round-close`, `recovery`, and `maintenance` mode
- maintenance can be triggered again after a mission is already `completed`
- stale `pending_gate_id` state can now be recovered instead of remaining stuck forever

## Remaining Gaps

This runtime should still not be described as final architecture completion.

Main remaining gaps:

- `lib/scheduler.py` still contains much of the specialist role behavior
- `cleanup-agent` maintenance is scheduler-driven, not an independent long-lived service
- runtime hygiene currently finds and reports issues conservatively; it is not a full destructive cleanup system
- stronger crash idempotency and longer soak hardening still need more work

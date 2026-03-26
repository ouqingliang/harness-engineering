# 2026-03-26 Supervisor State Machine Implementation

> Document type: daily note
> Status: current implementation baseline
> Scope: `harness-engineering/`

## What Changed

The runtime no longer treats `communication -> design -> execution -> audit -> cleanup`
as the mainline sequence.

The implemented control model is now:

- `supervisor` owns all dispatch decisions
- `design-agent`, `execution-agent`, and `audit-agent` form the main work loop
- `communication-agent` is a supervisor-only side channel
- `cleanup-agent` runs in `round-close`, `recovery`, or `maintenance` mode

## What Is Now Implemented

- `main.py` initializes new runs at `design`, not `communication`
- `lib/scheduler.py` dispatches by state-machine rules instead of fixed next-agent order
- only `supervisor` can escalate to human input
- explicit human escalation now creates a structured `communication_brief`
- `communication-agent` either opens a gate from that brief or records a human reply
- human reply returns control to the blocked work agent through `supervisor`
- `cleanup-agent` can be scheduled for:
  - `round-close` after `audit-agent` accepts a round
  - `recovery` when runtime state is marked stale
  - `maintenance` when the configured interval elapses
- maintenance cadence is configurable through `cleanup_maintenance_interval_seconds`

## Current Code Truth

The main control truth is still centralized in `lib/scheduler.py`.

This means the runtime behavior now follows the supervisor-centered architecture,
but the role logic is not yet fully separated into independent runner modules.

The most important design correction is already in place:

- specialist agents do not decide human escalation
- specialist agents do not call `communication-agent` directly
- `communication-agent` does not decide routing
- `cleanup-agent` is no longer modeled as a mandatory final stage in every pass

## Verification

Verified on 2026-03-26 with:

- `python -m unittest discover -s tests -v`
- `python main.py run --doc-root memory\\doc --memory-root runtime-memory-e2e --reset --no-browser`
- `python scripts\\harness\\run-soak.py --iterations 1`

Observed result:

- unit and integration tests passed
- direct completion flow reached `completed`
- gate and human-reply flow reached `waiting_human -> completed`
- maintenance and recovery cleanup paths were covered by tests

## Remaining Gaps

This implementation should not yet be described as final architecture completion.

Still missing or incomplete:

- specialist role behavior is still embedded in `lib/scheduler.py`
- `cleanup-agent` hygiene is still conservative and does not perform deep repository remediation
- long-running post-completion maintenance is not a separate daemon or service loop
- the runtime is validated as a runnable local harness, not as a hardened long-soak production system

## Next Baseline

Future work should start from this truth:

- supervisor-owned dispatch is now the code mainline
- communication is a side channel, not the work lane entrypoint
- cleanup has distinct modes and cadence
- further work should decompose scheduler-owned role logic, not reintroduce a fixed pipeline

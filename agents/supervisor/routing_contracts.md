# Routing Contracts

The supervisor routes work through durable JSON work orders and supervisor-facing events.

## Required Handoffs

- `design-agent` -> `execution-agent`
  - design contract, implementation slice, acceptance criteria
- `execution-agent` -> `audit-agent`
  - code changes, verification evidence, unresolved risks
- `audit-agent` -> `supervisor`
  - verification verdict and one of the frozen routing outcomes
- `worker` -> `supervisor`
  - worker completion, blocker, or human-gate event

## Supervisor Event Contract

- worker completion is reported as `worker_completed`
- worker blockers are reported as `worker_blocked`
- human-gate openings are reported as `human_gate_opened`
- audit verdicts are reported as `supervisor_route_outcome`
- the supervisor owns the final routing outcome and records it as one of `accept`, `reopen_execution`, `replan_design`, or `route_to_decision`

## Runtime Namespace

All active handoff state should live under `<memory_root>/.harness/`.

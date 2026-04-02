# Routing Contracts

The supervisor routes work through durable JSON work orders and supervisor-facing events.

## Required Handoffs

- `supervisor` -> `decision-agent`
  - blocker text, semantic ambiguity, human-reply context
- `decision-agent` -> `supervisor`
  - decision note, short brief, reply interpretation
- `design-agent` -> `execution-agent`
  - design contract, implementation slice, acceptance criteria
- `execution-agent` -> `verification-agent`
  - code changes, verification evidence, unresolved risks
- `verification-agent` -> `supervisor`
  - verification verdict and evidence

## Supervisor Event Contract

- verification verdicts are reported as `supervisor_route_outcome`
- the supervisor owns the final routing outcome and records it as one of `accept`, `reopen_execution`, `replan_design`, or `route_to_decision`

## Runtime Namespace

All active handoff state should live under `<memory_root>/.harness/`.

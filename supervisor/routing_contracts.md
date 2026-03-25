# Routing Contracts

The supervisor should route work through durable JSON work orders.

## Required Handoffs

- `communication-agent` -> `design-agent`
  - mission framing, constraints, human decisions
- `design-agent` -> `execution-agent`
  - design contract, implementation slice, acceptance criteria
- `execution-agent` -> `audit-agent`
  - code changes, verification evidence, unresolved risks
- `audit-agent` -> `cleanup-agent`
  - accepted findings state or reopen decision
- `cleanup-agent` -> `communication-agent`
  - durable summary and next-run resume state

## Runtime Namespace

All active handoff state should live under `<memory_root>/.harness/`.

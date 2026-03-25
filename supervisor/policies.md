# Supervisor Policies

The supervisor is the only orchestrator.

## Default Order

1. `communication-agent`
2. `design-agent`
3. `execution-agent`
4. `audit-agent`
5. `cleanup-agent`

## Rules

- do not let agents self-poll forever
- do not skip design before execution on a new slice
- do not accept execution without audit evidence
- do not open the human loop except at explicit decision gates

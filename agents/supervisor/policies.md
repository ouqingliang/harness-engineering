# Supervisor Policies

The supervisor is the only orchestrator and control-plane writer.

## Frozen Routing Outcomes

The supervisor must route every round to one of these outcomes only:

1. `accept`
2. `reopen_execution`
3. `replan_design`
4. `route_to_decision`

## Rules

- read and write harness docs and runtime text files as UTF-8
- do not let agents self-poll forever
- do not skip design before execution on a new slice
- do not accept execution without audit evidence
- do not let a slice close before the required verification has actually passed
- do not let a complete capability claim pass without end-to-end verification
- do not open the human loop except at explicit decision gates
- do not treat `communication-agent` as a supervisor target role or routing destination

# Supervisor Policies

The supervisor is the only orchestrator and control-plane writer.

The human communication surface is runtime-owned and is not a worker role.

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
- do not accept execution without verification evidence
- do not let a slice close before the required verification has actually passed
- do not let a complete capability claim pass without end-to-end verification
- do not open the human loop except at explicit decision gates served through the runtime-owned communication surface
- do not treat the human communication surface as a worker role or routing destination
- do not treat retired worker names as supervisor target roles or routing destinations
- do route semantic blockers to `decision-agent` and acceptance checks to `verification-agent`

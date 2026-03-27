# Daily Note: Parallel Supervisor Background Execution Update

## Purpose

This note records the runtime change completed on 2026-03-26 to remove the synchronous execution bottleneck from Harness Engineering and expose the resulting parallel supervisor state clearly.

## What Changed

- `execution-agent` no longer blocks the whole supervisor loop while `codex exec` is running.
- execution work now goes through a background launcher:
  - the supervisor writes the execution request
  - a dedicated launcher process runs the saved request
  - the supervisor later polls and harvests the result
- the runtime now records three execution-oriented queues:
  - `running_execution_runs`
  - `completed_execution_queue`
  - `planned_slice_queue`
- `design-agent` can keep the next slice prefetched while the current execution run is still active.
- `audit-agent` no longer depends on a purely synchronous `execution -> audit` handoff.
  - it now consumes a concrete completed execution artifact from `completed_execution_queue`
- the 8765 monitor page no longer implies that the whole system has only one working agent.
  - it now shows:
    - `Agent Status`
    - `Supervisor Focus`
    - `Running Agents`
    - `Queued Work`
    - `Recent Events`
- the runtime snapshot now exposes `agent_statuses`
  - each agent reports its current status and a human-readable summary
  - example states now include:
    - `planning`
    - `prefetching`
    - `prefetched`
    - `running`
    - `waiting_audit`
    - `auditing`
    - `waiting_human`
    - `standby`
- the background execution path is now implemented through:
  - `lib/scheduler.py`
  - `runners/codex_execution_launcher.py`
  - `lib/supervisor_bridge.py`
  - `runners/codex_app_server.py`

## Verified Runtime Behavior

The runtime was verified with:

- `python -m unittest discover -s tests -v`
- a real CLI smoke run with a fake sleeping `codex` executable

Verified outcomes:

- the scheduler exposes a mid-run snapshot where:
  - `agent_statuses` shows what `design-agent` and `execution-agent` are doing
  - `running_agents` includes `execution`
  - `queued_slices` includes a prefetched next slice
  - the mission remains `running`
- the same smoke run later reaches `completed`
- existing gate, resume, cleanup, and long-running CLI behavior still pass after the execution launcher change

## Current Truth

The runtime should now be described as:

- a long-running supervisor loop
- with non-blocking background execution
- with prefetchable next-slice design work
- with audit consuming explicit completed execution results and reporting findings to `supervisor` first
- with `supervisor` deciding whether a failed slice goes back to `design` or `execution`
- with a monitor page that shows queued and running work instead of only a single top-level active role
- with every modifying agent except `supervisor` working inside a supervisor-managed git worktree

The canonical repository checkout stays with `supervisor`.
Worktrees are the mutable surfaces for `design`, `execution`, `audit`, and other modifying agents.

## Remaining Gap

This should still not be described as the final multi-worker architecture.

Remaining limitation:

- the runtime now overlaps supervisor-side planning with background execution, but it still does not run multiple independent code-modifying execution streams in parallel against the target repository
- role logic still remains largely centralized in `lib/scheduler.py`

Future work should start from this truth instead of the earlier synchronous execution model.

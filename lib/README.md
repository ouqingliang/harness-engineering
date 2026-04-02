# Library Runtime

This directory holds the current small helpers for the harness runtime.

Current modules:

- `scheduler.py`
  - the main loop helper
- `runtime_state.py`
  - load and save `.harness/state.json`
- `handoff.py`
  - read and write handoff files
- `report.py`
  - read and write agent reports
- `question_router.py`
  - ordinary blocker interception
- `runner_bridge.py`
  - thin wrapper around runner calls
- `communication_api.py`
  - the runtime-owned human communication surface
- `supervisor_bridge.py`
  - thin adapter that exposes supervisor runtime state to the human communication surface

Current rule:

- keep these helpers thin
- do not turn this directory into a second architecture layer

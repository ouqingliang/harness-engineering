# Runners

This directory holds the current runner surface for agents.

The current runner surface does a few things:

- expose the local human reply page at `GET /`
- expose the local HTTP communication surface
- expose runtime inspection for the active supervisor scheduler
- return communication state as UTF-8 JSON

Current boundaries:

- `main.py run` is still the only supported way to advance the harness loop
- the HTTP surface is for communication and inspection, not for replacing the scheduler entrypoint
- low-level turn running remains an internal helper under `lib/runner_bridge.py`

Current rule:

- the runner is an adapter
- the runner is not the architecture

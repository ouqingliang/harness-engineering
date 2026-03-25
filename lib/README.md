# Library Plan

This directory will hold harness-specific shared modules.

Planned modules:

- `work_orders.py`
  - shared work-order schemas and serialization helpers
- `app_server_bridge.py`
  - Codex App Server session bridge, event handling, and item capture
- `remote_command_proxy.py`
  - forwarding layer for remote Client execution and command-result return
- `memory_client.py`
  - center-owned memory namespace client
- `audit_sink.py`
  - acceptance and findings write path
- `artifact_store.py`
  - structured artifact persistence helpers

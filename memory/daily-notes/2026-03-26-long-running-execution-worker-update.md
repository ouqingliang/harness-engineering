# Daily Note: Long-Running Execution Worker Update

## Purpose

This note records the runtime change made on 2026-03-26 to move Harness Engineering closer to the approved task-centered autonomous loop.

## What Changed

- `main.py run` now behaves as the default long-running supervisor loop.
- `completed` and `failed` remain runtime states, but they no longer cause the CLI process to exit on their own.
- the process keeps serving the human page and stays available for maintenance or later doc changes until it is stopped manually.
- `execution-agent` no longer acts like a verification-only placeholder.
- `execution-agent` now calls `codex exec` inside the inferred project root and gives that execution worker an explicit prompt to use subagents for modification work.
- the execution artifact now records:
  - the codex request
  - the codex result
  - the canonical execution summary consumed by `audit-agent`
- `audit-agent` now requires both:
  - execution-worker evidence
  - verification evidence
- repeated external-project reopens no longer mark the runtime failed immediately.
- repeated reopen now becomes a supervisor decision brief routed through `communication-agent`.

## Round Semantics

- an accepted round no longer ends the whole loop immediately.
- after `audit-agent` accepts a slice, `cleanup-agent` performs `round-close`.
- the supervisor then returns to `design-agent` for the next remaining slice.
- the mission reaches `completed` only when the selected planning document has no remaining slices.

## Baseline Selection

The runtime now treats these docs as the primary implementation baseline when they exist:

- `docs/designs/2026-03-25-task-centered-autonomous-ops-platform.md`
- `docs/designs/2026-03-25-harness-engineering-integration.md`
- `docs/designs/2026-03-25-center-subsystem-architecture-outline.md`
- `docs/plans/2026-03-25-task-mainline-and-engineernode-removal.md`

The planning document remains the source for phase slicing and verification commands.
The design documents remain the source for architecture boundaries and role semantics.

## Current Limitation

- `execution-agent` now performs real repo work through `codex exec`, but the role logic still lives mainly inside `lib/scheduler.py`.
- this is a runtime behavior improvement, not the final role-runner split.

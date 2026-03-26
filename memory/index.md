# Harness Memory Index

This directory holds the durable method memory for `harness-engineering/`.

## Documents

- `doc/harness-architecture.md`
  - approved target architecture baseline for the supervisor-centered runtime
- `doc/harness-long-running-runtime-implementation.md`
  - implementation plan aligned to the supervisor state machine, communication side-channel, and cleanup cadence
- `doc/aima-refactor-implementation-baseline.md`
  - frozen product-architecture baseline that harness should follow for future implementation work

## Daily Notes

- `daily-notes/2026-03-26-harness-engineering-status.md`
  - current code status, runnable scope, missing pieces, and design drift review
- `daily-notes/2026-03-26-supervisor-state-machine-runtime-update.md`
  - current supervisor-state-machine implementation status, validated behavior, and remaining gaps after the runtime refactor
- `daily-notes/2026-03-26-supervisor-state-machine-implementation.md`
  - concise implementation baseline for the supervisor-controlled work loop, communication lane, and cleanup cadence
- `daily-notes/2026-03-26-long-running-execution-worker-update.md`
  - runtime update covering long-running `run`, codex-backed execution work, and multi-round slice progression

## Rule

Keep durable harness knowledge here.
Do not leave architecture truth only in transient chat or run logs.

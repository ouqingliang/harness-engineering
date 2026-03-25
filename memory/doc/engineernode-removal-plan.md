# EngineerNode Removal Plan

## Purpose

This document freezes the removal direction for the old `EngineerNode` model.

`EngineerNode` is no longer a live target architecture object.
The replacement path is the new Harness Engineering runtime and its worker contracts.

## Live-Code Deletion Surface

The old model currently spans:

- active repository instructions and README surfaces
- `engineer/node/**`
- `center` model, schema, API, and session ownership code
- deployment scripts that still build or launch the node runtime
- unit and e2e tests that assert `EngineerNode` behavior

## Migration Rule

Do not delete `EngineerNode` piecemeal.

Delete it in this order:

1. freeze the replacement harness worker contracts
2. stop writing `EngineerNode` into active instructions and docs
3. replace `center` request and response contracts
4. replace `center` data model ownership
5. migrate any reusable runtime code into `harness-engineering/`
6. delete `engineer/node/**`
7. rewrite tests around the new worker model

## Reusable Fragments

These old runtime patterns may still be migrated:

- register -> heartbeat -> poll -> ack loop shape
- thin center HTTP client helpers
- env-backed runtime config loading
- command dispatcher patterns

These must not survive as active concepts:

- `EngineerNode`
- `EngineerNodeCommand`
- `support_sessions.engineer_node_id`
- node-scoped session capacity as the active execution truth

## Archive Rule

Historical references under `memory/**` may remain as archive truth.
They should not be treated as the current architecture baseline.

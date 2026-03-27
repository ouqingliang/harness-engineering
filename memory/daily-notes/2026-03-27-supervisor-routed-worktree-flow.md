# Daily Note: Supervisor-Routed Worktree Flow

## Purpose

This note corrects the workflow truth on 2026-03-27.

It records the current state and the target boundary without claiming the workflow is already complete.

## Current Truth

- `design`, `execution`, and `audit` all have non-blocking background runtime paths
- `audit` verdicts route back through `supervisor`
- mutating agents work inside supervisor-managed worktrees
- human interaction enters only through explicit supervisor gates

## Target Architecture

- `supervisor` is the only scheduler
- `design`, `execution`, and `audit` must all be background-capable agents
- `audit` routes only through `supervisor`
- all document- or code-mutating agents must work in supervisor-managed worktrees
- human interaction only enters through an explicit supervisor gate

## Actor Boundaries

### `supervisor`

`supervisor` owns:

- the canonical repository checkout
- runtime state
- round progression
- worktree assignment
- audit routing
- human gate decisions

No other agent decides the global route.

### `design`

`design` produces the current slice contract.

It must be able to run in the background and must not block `supervisor`.

### `execution`

`execution` implements the current slice inside a supervisor-managed worktree.

This background path is already implemented.

### `audit`

`audit` reviews the execution artifact and returns a verdict.

It must be able to run in the background and must not block `supervisor`.

### human

The human is outside the normal loop.

The human is only entered when `supervisor` opens an explicit decision gate.

## Routing Truth

The only valid control chain is:

- `supervisor -> design`
- `supervisor -> execution`
- `supervisor -> audit`
- `audit -> supervisor`
- `supervisor -> design/execution`

Meaning:

- `audit` never routes directly to `design` or `execution`
- `design` and `execution` never bypass `supervisor`
- ordinary blockers stay inside the harness
- only explicit gates reach the human

## Locked Truth

- the system is supervisor-centered, not a fixed pipeline
- `design`, `execution`, and `audit` all run through background launcher paths
- all mutating agents must use supervisor-managed worktrees
- the human-facing path is gate-driven, not worker-driven
- `audit` routes to `supervisor` first, and `supervisor` decides retry, replan, or accept

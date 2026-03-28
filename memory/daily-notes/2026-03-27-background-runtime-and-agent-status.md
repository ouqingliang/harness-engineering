# Daily Note: Background Runtime and Agent Status

## Purpose

This note records the verified runtime status of `harness-engineering` on 2026-03-27 after the background worker failure investigation.

The goal of this note is to separate:

- issues that were actually fixed
- behaviors that are currently normal for the present implementation
- remaining gaps that still block the full long-running loop from being considered complete

## Verified Fixes

### 1. Background worker false failure on reused or mismatched pid

The immediate failure:

- `background worker pid <pid> now belongs to a different process`

was not a real business failure.
It was a runtime false positive.

The verified root causes were:

- `running` launcher updates could drop `heartbeat_at`
- launcher identity checking relied on weak executable-name matching
- the scheduler could fail a launcher before a valid result artifact was written

The runtime behavior is now corrected as follows:

- `running` state updates preserve existing heartbeat data for the same request/result pair
- launcher state records `pid_identity` instead of relying only on executable name
- result artifact existence takes precedence over stale-pid suspicion
- background launcher state now carries consistent pid and heartbeat metadata from `design`, `execution`, `audit`, and the launcher wrapper itself

Verification completed:

- `python -m pytest .\tests\test_runtime_files.py -q`
- `python -m pytest .\tests\test_scheduler_verification.py -q`
- fresh repro with:
  - `python main.py run --doc-root C:\Users\oql\OneDrive\Study\AIMA-refactor\docs --memory-root C:\Users\oql\OneDrive\Study\AIMA-refactor\memory --no-browser --reset`

Observed result:

- the run no longer failed in round 0 with `different process`
- runtime state stayed `running` through repeated scheduler turns

### 2. Execution session reuse for the same unfinished task

The execution lane now preserves the distinction between:

- a new task that should start a new session
- the same unfinished task that should resume the prior session

The implemented behavior is:

- execution records `session_id`
- supervisor retry and continue paths preserve `resume_session_id`
- same-task empty or readiness-only Codex replies are treated as `requested_task_again`
- the next execution attempt resumes the same Codex session before considering a fresh start

This is verified in the execution/session tests and in a real short run where the same execution session id was reused across repeated attempts.

## Verified Current Behavior

### 1. `design` finishes very quickly

This was investigated and is currently normal for the present implementation.

`design` is not yet a long-running session-backed Codex agent.
It is a local contract generator.

Current `design` behavior is:

- read the prepared design request
- compute `design_contract` and `next_contract` locally
- write the result artifact
- mark the launcher completed

So a very short `design` run does not currently mean the design worker crashed.
It means the current implementation is synchronous and planner-like.

This should be treated as current implementation truth, not as proof that the target long-running design-agent architecture is finished.

## Remaining Gaps

### 1. The main business loop is still not complete

The background runtime no longer fails immediately, but the long-running loop is not yet complete end-to-end.

The main remaining gap is:

- `execution` can keep cycling without making real slice progress

What is happening now is:

- the harness remains alive
- execution session reuse works
- but the execution agent may still spend turns on startup alignment or readiness-only behavior rather than delivering code changes for the slice

So the runtime shell is healthier, but the product work loop is not yet fully closed.

### 2. `design` and `audit` are not yet session-backed long-running agents

The runtime now treats agent launching and background state consistently, but only `execution` currently has real Codex session reuse behavior.

`design` and `audit` still behave as one-shot workers.

That means the harness currently has:

- background-capable launchers for all three lanes
- session reuse semantics primarily in `execution`
- planner-style `design`
- rule/evidence-style `audit`

This is acceptable as current implementation truth.
It is not yet the full target model where all worker lanes can pause and resume in agent-native sessions.

## Locked Status

As of 2026-03-27, the correct status is:

- background worker pid false-failure handling is fixed
- execution session reuse for same-task continuation is implemented
- quick `design` completion is currently normal because `design` is synchronous
- the full long-running task loop is still not complete because execution can continue spinning without real slice progress

Future work should start from this boundary:

- do not re-open the stale-pid failure as the main blocker
- treat `design` quick completion as expected until the design lane is intentionally converted into a session-backed agent
- focus next on why the execution lane continues to consume turns without advancing the slice

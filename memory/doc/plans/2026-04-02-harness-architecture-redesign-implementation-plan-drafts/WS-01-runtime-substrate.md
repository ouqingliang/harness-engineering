# WS-01 Runtime Substrate And Shared Session Contract Implementation Plan

> Document type: implementation plan
> Purpose: land the frozen `.harness` substrate and shared session-contract primitives before supervisor flow and role-migration work starts depending on them
> Scope: `harness-engineering/`

**Goal:** Build the frozen runtime storage layout, the shared worker session-contract primitives, and the foundational regression coverage that other workstreams can consume without editing the same scheduler and communication entrypoints in parallel.

**Architecture:** WS-01 owns runtime primitives, not supervisor policy. It creates the durable `.harness` layout, UTF-8-safe file helpers, and the thin shared session-contract/value-object layer. It may update launcher-adjacent persistence where the substrate is produced, but it does not own supervisor routing outcomes, communication-surface semantics, role-migration semantics, or broad scheduler rewiring.

**Tech Stack:** Python, UTF-8 JSON and JSONL files, `unittest`, existing runtime and launcher helpers.

## Ownership Boundary

**WS-01 owns:**
- runtime path definitions and layout bootstrap
- shared contract/value-object modules for worker sessions
- launcher-adjacent persistence needed to create and update substrate records
- foundational runtime and contract tests

**WS-01 does not own:**
- supervisor routing outcomes or asynchronous event-consumption policy
- `communication-agent` to communication-surface migration semantics
- `audit` to `verification` and `communication` to `decision` role migration
- HTTP surface behavior in `runners/codex_app_server.py`
- broad routing changes in `lib/scheduler.py`, `lib/scheduler_components/turns.py`, or `main.py`

**Handoff To Other Workstreams:**
- WS-02 consumes the runtime layout, event file helpers, and gate/brief persistence primitives to implement the asynchronous supervisor and human communication surface.
- WS-03 consumes the shared session-contract primitives and frozen runtime directories while migrating role names and topology semantics.

## Dependency Order

1. Freeze the runtime path surface and UTF-8 helpers first so every later workstream can read and write the same `.harness` substrate.
2. Add the shared worker session-contract/value-object layer next so launcher and scheduler code can converge on one thin vocabulary.
3. Update launcher-adjacent persistence only where needed to emit the new substrate records without taking ownership of supervisor routing.
4. Lock the foundational regression tests last so later workstreams can safely refactor on top of a stable substrate.

### Task 1: Freeze the runtime layout and UTF-8 file helpers

**Files:**
- Modify: `harness-engineering/lib/runtime_state.py`
- Modify: `harness-engineering/lib/scheduler_components/background_runtime.py`
- Modify: `harness-engineering/tests/test_runtime_files.py`
- Add: `harness-engineering/tests/test_runtime_layout.py`

**Steps:**
- [ ] Replace the current `RuntimePaths` shape in `lib/runtime_state.py` with the frozen baseline layout: `.harness/events/`, `.harness/sessions/`, `.harness/inbox/`, `.harness/artifacts/`, `.harness/gates/`, `.harness/briefs/`, `.harness/worktrees/`, plus `mission.json` and `state.json`.
- [ ] Make `ensure_runtime_layout()` create only the frozen active directories and stop creating the old launcher-centric public runtime roots as part of the mainline path.
- [ ] Add UTF-8-safe helpers for reading and writing session metadata, inbox messages, gate records, brief records, and JSONL event rows without prescribing higher-level supervisor behavior.
- [ ] Keep any launcher-private scratch or lock files private to the launcher side; do not treat them as part of the shared runtime contract.
- [ ] Rewrite `tests/test_runtime_files.py` to assert the new runtime directories, the mission/state round-trip, and the UTF-8-safe substrate helpers instead of the removed old path assumptions.
- [ ] Add `tests/test_runtime_layout.py` to pin the frozen `.harness` shape and prove the baseline layout is created with UTF-8 clean reads and writes.

**Verification:**
- Run: `python -m unittest tests.test_runtime_files -v`
- Run: `python -m unittest tests.test_runtime_layout -v`

### Task 2: Add the shared session-contract primitive layer

**Files:**
- Add: `harness-engineering/lib/runtime_contract.py`
- Modify: `harness-engineering/lib/runner_bridge.py`
- Modify: `harness-engineering/lib/scheduler_components/execution.py`
- Add: `harness-engineering/tests/test_worker_session_contract.py`

**Steps:**
- [ ] Create `lib/runtime_contract.py` as the shared source for the public worker-session vocabulary: thin control actions, thin task-notification fields, and any small value objects or coercion helpers needed by multiple callers.
- [ ] Encode the public control side as `spawn`, `continue`, and `terminate`, and keep `resume` as an internal recovery detail only so the architecture layer never advertises it as a separate verb.
- [ ] Encode the worker response side as a compact task-notification envelope with only the required `session`, `status`, and `summary` fields plus the optional `result` and `output-file` fields.
- [ ] Update `lib/runner_bridge.py` only far enough to consume and emit the shared contract primitives; do not use WS-01 to redesign supervisor routing, human-gate policy, or target-role semantics.
- [ ] Update `lib/scheduler_components/execution.py` only where execution launcher requests or results must map onto the shared session contract, including same-session continuation as a substrate primitive rather than a role-migration decision.
- [ ] Add `tests/test_worker_session_contract.py` to assert the contract vocabulary, same-session continuation primitives, and UTF-8-clean notification payloads.

**Verification:**
- Run: `python -m unittest tests.test_worker_session_contract -v`

### Task 3: Move launcher-adjacent persistence onto the frozen substrate

**Files:**
- Modify: `harness-engineering/runners/codex_agent_launcher.py`
- Modify: `harness-engineering/runners/codex_execution_launcher.py`
- Modify: `harness-engineering/lib/scheduler_components/background_runtime.py`
- Modify: `harness-engineering/tests/test_runtime_files.py`

**Steps:**
- [ ] Update both launcher entrypoints and the background runtime helper so launcher-adjacent state is written into the new session, inbox, artifact, gate, brief, and event records where the frozen substrate requires them.
- [ ] Remove dependence on the old launcher-centric public directories such as `handoffs/`, `reports/`, `questions/`, and `answers/` from the active mainline runtime path.
- [ ] Keep launcher-private state private; WS-01 should not redefine the higher-level communication surface or the asynchronous supervisor event loop.
- [ ] Extend `tests/test_runtime_files.py` so substrate-producing launcher paths prove the new records exist and remain UTF-8 readable after restart-oriented round trips.

**Verification:**
- Run: `python -m unittest tests.test_runtime_files -v`

### Task 4: Lock the foundational restart and contract regressions

**Files:**
- Modify: `harness-engineering/tests/test_resume_loop.py`
- Modify: `harness-engineering/tests/test_end_to_end_loop.py`

**Steps:**
- [ ] Update `tests/test_resume_loop.py` only far enough to prove the substrate keeps the same worker session identity available for continuation and restart recovery.
- [ ] Update `tests/test_end_to_end_loop.py` only for frozen runtime-layout and shared-session-contract assertions; leave supervisor routing-outcome and role-migration assertions to WS-02 and WS-03.
- [ ] Keep these tests focused on substrate evidence so later workstreams can safely extend behavior without fighting mixed ownership in the same assertions.

**Verification:**
- Run: `python -m unittest tests.test_resume_loop -v`
- Run: `python -m unittest tests.test_end_to_end_loop -v`
- Run: `python -m unittest discover -s tests -p "test_*.py" -v`

## Done Criteria

- The runtime bootstrap creates the frozen `.harness` layout and the active public path no longer depends on the old launcher-centric storage tree.
- `lib/runtime_contract.py` defines the shared thin worker-session vocabulary used by launcher-adjacent code.
- Launcher-adjacent persistence produces frozen session, inbox, artifact, gate, brief, and event records without taking ownership of supervisor routing semantics.
- The foundational runtime and contract tests pass and leave WS-02 and WS-03 free to change supervisor flow and role semantics on top of a stable substrate.

## Known Risks

- `lib/runner_bridge.py` is still a shared integration point, so WS-01 must keep its edits limited to contract primitives or it will collide with WS-02 and WS-03.
- Removing the old public runtime directories too early can break restart paths if any launcher or test fixture still reads them implicitly.
- `tests/test_resume_loop.py` and `tests/test_end_to_end_loop.py` are shared regression anchors, so WS-01 should keep them substrate-focused and avoid asserting supervisor-policy details that belong to later streams.
- If WS-01 skips UTF-8-safe helpers at the substrate layer, later workstreams will reintroduce encoding drift while adding supervisor and communication behavior.

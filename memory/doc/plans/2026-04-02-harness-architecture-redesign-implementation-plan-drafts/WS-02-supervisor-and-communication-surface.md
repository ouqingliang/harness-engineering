# WS-02 Supervisor And Communication Surface Implementation Plan

> Document type: implementation plan
> Purpose: implement the asynchronous supervisor, human gate path, and runtime communication surface for the frozen harness architecture
> Scope: `harness-engineering/`

**Goal:** Make the supervisor asynchronous and event-driven, preserve raw human free text as durable runtime evidence, and remove `communication-agent` as a target-role semantic from routing and resume handling.

**Architecture:** The supervisor remains the only scheduler and control-plane writer. Worker reports and human replies become supervisor-facing events. Human text is stored before interpretation. `decision-agent` remains the semantic boundary for blockers and human replies. The communication surface stays a runtime-owned I/O boundary, not a worker lane.

**Tech Stack:** Python, UTF-8 JSON/text files, `unittest`, `http.server`, existing scheduler/runtime helpers.

## Dependency Order

1. Freeze the supervisor docs and routing outcomes first.
2. Add event-backed human reply persistence and the supervisor inbox next.
3. Remove `communication-agent` target-role semantics from the scheduler and runner bridge.
4. Update tests last so the new routing and human-gate behavior is locked with regression coverage.

### Task 1: Freeze supervisor routing outcomes and event semantics

**Files:**
- Modify: `harness-engineering/agents/supervisor/policies.md`
- Modify: `harness-engineering/agents/supervisor/routing_contracts.md`
- Modify: `harness-engineering/agents/supervisor/escalation_rules.md`
- Modify: `harness-engineering/lib/scheduler.py`
- Modify: `harness-engineering/lib/scheduler_components/turns.py`
- Add: `harness-engineering/tests/test_supervisor_routing.py`
- Modify: `harness-engineering/tests/test_scheduler_round.py`
- Modify: `harness-engineering/tests/test_scheduler_verification.py`

**Steps:**
- [ ] Replace the legacy `communication-agent` handoff language in the supervisor docs with the frozen routing outcomes: `accept`, `reopen_execution`, `replan_design`, and `route_to_decision`.
- [ ] Update `lib/scheduler.py` so the supervisor consumes inbound runtime events, records the routing outcome for each blocker, and keeps the control-plane state thin and explicit.
- [ ] Update `lib/scheduler_components/turns.py` so worker completion, blocker, and human-gate reports are published as supervisor-facing events with thin payloads instead of being routed through a communication worker lane.
- [ ] Keep `decision-agent` as the semantic boundary for ambiguous blockers and human replies, with the supervisor owning the final routing outcome.
- [ ] Add `tests/test_supervisor_routing.py` for the thin routing-outcome contract, including `route_to_decision` and the no-direct-worker-routing boundary.
- [ ] Extend `tests/test_scheduler_round.py` and `tests/test_scheduler_verification.py` to assert that event publication order and routing outcomes stay stable when a blocker is escalated and then resumed.

**Verification:**
- Run: `python -m unittest tests.test_supervisor_routing -v`
- Run: `python -m unittest tests.test_scheduler_round -v`
- Run: `python -m unittest tests.test_scheduler_verification -v`

### Task 2: Persist raw human text and keep the communication surface runtime-owned

**Files:**
- Modify: `harness-engineering/lib/runtime_state.py`
- Modify: `harness-engineering/lib/communication_api.py`
- Modify: `harness-engineering/runners/codex_app_server.py`
- Modify: `harness-engineering/lib/runner_bridge.py`
- Modify: `harness-engineering/tests/test_runner.py`
- Modify: `harness-engineering/tests/test_resume_loop.py`
- Modify: `harness-engineering/tests/test_runtime_files.py`
- Add: `harness-engineering/tests/test_communication_surface.py`

**Steps:**
- [ ] Add the runtime events layout to `lib/runtime_state.py` so `.harness/events/supervisor-inbox.jsonl` is created and tracked alongside the existing durable runtime directories.
- [ ] Update `lib/communication_api.py` so `append_message()` and `reply_to_gate()` preserve the original free-text body, write durable raw records, and emit supervisor-facing event records before any later interpretation.
- [ ] Update `runners/codex_app_server.py` so `/human/reply`, `/communication/messages`, and `/communication/gates` stay a narrow human I/O surface that stores raw text and exposes runtime state, but never pretends to be a worker scheduler.
- [ ] Remove the `communication-agent` special case from `lib/runner_bridge.py`; the bridge should no longer synthesize a target role called `communication` or open gates as if a worker lane owns them.
- [ ] Extend `tests/test_runner.py`, `tests/test_resume_loop.py`, and `tests/test_runtime_files.py` to prove the human page persists raw text, the reply survives a round trip, and the runtime layout still round-trips as UTF-8.
- [ ] Add `tests/test_communication_surface.py` for the raw-message persistence and gate-reply storage contract.

**Verification:**
- Run: `python -m unittest tests.test_runner -v`
- Run: `python -m unittest tests.test_resume_loop -v`
- Run: `python -m unittest tests.test_runtime_files -v`
- Run: `python -m unittest tests.test_communication_surface -v`

### Task 3: Remove communication-agent-as-target-role semantics and lock decision-agent routing

**Files:**
- Modify: `harness-engineering/lib/question_router.py`
- Modify: `harness-engineering/lib/auto_answer.py`
- Modify: `harness-engineering/lib/scheduler.py`
- Modify: `harness-engineering/lib/scheduler_components/turns.py`
- Modify: `harness-engineering/tests/test_question_routing.py`
- Modify: `harness-engineering/tests/test_auto_answer.py`
- Modify: `harness-engineering/tests/test_end_to_end_loop.py`
- Modify: `harness-engineering/tests/test_scheduler_round.py`
- Modify: `harness-engineering/tests/test_scheduler_verification.py`
- Add: `harness-engineering/tests/test_human_gate_flow.py`

**Steps:**
- [ ] Keep ordinary blockers auto-answerable in `lib/question_router.py` and `lib/auto_answer.py`, but route semantic ambiguities through `decision-agent` semantics instead of the communication surface.
- [ ] Remove any scheduler logic that treats `communication-agent` as a target role or human-facing escalation lane; the communication surface should only capture and store the reply.
- [ ] Update `tests/test_question_routing.py` and `tests/test_auto_answer.py` so the gate boundary is explicit and the decision-agent boundary remains the only semantic escalation path.
- [ ] Update `tests/test_end_to_end_loop.py`, `tests/test_scheduler_round.py`, and `tests/test_scheduler_verification.py` so the full run still completes without a communication worker role and still resumes correctly after a human reply.
- [ ] Add `tests/test_human_gate_flow.py` to cover the complete gate lifecycle: blocker report, supervisor event publication, raw reply persistence, decision-agent boundary, and resume.

**Verification:**
- Run: `python -m unittest tests.test_question_routing -v`
- Run: `python -m unittest tests.test_auto_answer -v`
- Run: `python -m unittest tests.test_end_to_end_loop -v`
- Run: `python -m unittest tests.test_scheduler_round -v`
- Run: `python -m unittest tests.test_scheduler_verification -v`
- Run: `python -m unittest tests.test_human_gate_flow -v`
- Run: `python -m unittest discover -s tests -p "test_*.py" -v`

## Done Criteria

- The supervisor is documented and implemented as asynchronous and event-driven, with thin routing outcomes and no hidden worker-to-worker control path.
- Human replies are stored as raw UTF-8 text before any interpretation, and the communication surface remains a runtime-owned boundary rather than a worker lane.
- `decision-agent` is the only semantic escalation boundary; `communication-agent` no longer appears as a target role in supervisor routing.
- The targeted `unittest` matrix and the full discovery run pass after the routing and communication-surface changes.

## Known Risks

- The new event layout touches `runtime_state.py`, `communication_api.py`, and the scheduler at the same time, so file ordering matters to avoid partial writes that break resume behavior.
- `runners/codex_app_server.py` and `lib/runner_bridge.py` both touch the human surface, so it is easy to persist the text but forget to publish the matching supervisor-facing event.
- If the `communication-agent` lane is removed before the regression tests are updated, snapshot expectations in `tests/test_runner.py`, `tests/test_resume_loop.py`, and `tests/test_scheduler_verification.py` will fail in ways that look like regressions but are really stale assertions.
- This workstream overlaps with role-migration cleanup, so the coordinator should avoid reintroducing `communication-agent` as an active route while the separate worker-role migration is still in flight.

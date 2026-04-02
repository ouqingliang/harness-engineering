# WS-03 Role Migration and Test Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the harness role model from `communication`/`audit` to `decision`/`verification`, keep cleanup tied to verification and recovery boundaries, and lock the migration with regression coverage over loader, scheduler, and end-to-end behavior.

**Architecture:** `decision-agent` becomes the thin blocker-triage lane for human-needed semantic judgments, while `verification-agent` becomes the non-mutating acceptance lane that reports evidence back to `supervisor`. The human communication surface stays runtime-owned and is not a worker role. Cleanup remains a post-verification and maintenance boundary, not a substitute for verification.

**Tech Stack:** Python, JSON agent specs, `unittest`, scheduler runtime helpers, harness docs.

---

## Dependency Order

1. Update role docs and agent specs so the target names and ownership model exist before runtime code starts depending on them.
2. Rewire `main.py`, `lib/scheduler.py`, `lib/runner_bridge.py`, and `lib/scheduler_components/` to load and route `decision` and `verification`.
3. Retire the old `communication` and `audit` worker semantics after the new paths are live.
4. Update the regression matrix last so the tests assert the new contracts instead of the old ones.

### Task 1: Update role specs and harness docs to the frozen baseline

**Files:**
- Create: `harness-engineering/agents/decision-agent/agent.json`
- Create: `harness-engineering/agents/decision-agent/system.md`
- Create: `harness-engineering/agents/verification-agent/agent.json`
- Create: `harness-engineering/agents/verification-agent/system.md`
- Modify: `harness-engineering/README.md`
- Modify: `harness-engineering/lib/README.md`
- Modify: `harness-engineering/agents/design-agent/agent.json`
- Modify: `harness-engineering/agents/design-agent/system.md`
- Modify: `harness-engineering/agents/execution-agent/agent.json`
- Modify: `harness-engineering/agents/execution-agent/system.md`
- Modify: `harness-engineering/agents/cleanup-agent/agent.json`
- Modify: `harness-engineering/agents/cleanup-agent/system.md`
- Modify: `harness-engineering/agents/supervisor/policies.md`
- Modify: `harness-engineering/agents/supervisor/routing_contracts.md`
- Modify: `harness-engineering/agents/supervisor/escalation_rules.md`
- Modify: `harness-engineering/memory/doc/architecture/harness-architecture-detailed/README.md`
- Modify: `harness-engineering/memory/doc/architecture/harness-architecture-detailed/agents/decision-agent.md`
- Modify: `harness-engineering/memory/doc/architecture/harness-architecture-detailed/agents/verification-agent.md`
- Modify: `harness-engineering/memory/doc/architecture/harness-architecture-detailed/agents/cleanup-agent.md`
- Modify: `harness-engineering/memory/doc/architecture/harness-architecture-detailed/agents/design-agent.md`
- Modify: `harness-engineering/memory/doc/architecture/harness-architecture-detailed/agents/execution-agent.md`

**Steps:**
- [ ] Write the new `decision-agent` and `verification-agent` specs so their `id`, `name`, `task.title`, `task.goal`, and dependency text match the frozen baseline language.
- [ ] Replace every repo-facing mention of `audit-agent` with `verification-agent` in role descriptions, default order, routing contracts, and cleanup boundaries.
- [ ] Replace every repo-facing mention of `communication-agent` as a worker lane with the runtime-owned human communication surface language.
- [ ] Update execution and design docs to say `verification` findings instead of `audit` findings, and update cleanup docs to say cleanup follows verification acceptance or maintenance/recovery.
- [ ] Keep the architecture docs aligned with the baseline wording so the detailed docs, the repo README, and the agent specs all describe the same worker set.

**Verification:**
- Run from `harness-engineering/`: `python main.py inspect --format json` and confirm the rendered role list includes `decision` and `verification`, not `communication` or `audit`.
- Run from `harness-engineering/`: `python -m unittest tests.test_runtime_files -v` to confirm the runtime docs and state helpers still round-trip after the wording change.

### Task 2: Rewire the scheduler and component layer to the new role names

**Files:**
- Modify: `harness-engineering/main.py`
- Modify: `harness-engineering/lib/scheduler.py`
- Modify: `harness-engineering/lib/runner_bridge.py`
- Modify: `harness-engineering/lib/communication_api.py`
- Modify: `harness-engineering/lib/scheduler_components/__init__.py`
- Modify: `harness-engineering/lib/scheduler_components/turns.py`
- Modify: `harness-engineering/lib/scheduler_components/verification.py`
- Create: `harness-engineering/lib/scheduler_components/decision.py`
- Retire: `harness-engineering/lib/scheduler_components/audit.py`

**Steps:**
- [ ] Update the agent loader so `main.py` discovers the new `decision-agent` and `verification-agent` specs and validates the new dependency graph without requiring `communication`.
- [ ] Replace `self.communication_agent_id` and `self.audit_agent_id` in `lib/scheduler.py` with `self.decision_agent_id` and `self.verification_agent_id`, then update every branch that schedules, resumes, consumes, or clears those roles.
- [ ] Move the acceptance lane from `audit` to `verification` by wiring `lib/scheduler_components/turns.py` to launch the new verification role runner and by keeping `lib/scheduler_components/verification.py` focused on verification command helpers.
- [ ] Add a new `decision` role runner in `lib/scheduler_components/decision.py` for blocker triage so semantic questions no longer fall through a communication-worker branch.
- [ ] Remove any `communication` worker assumptions from `lib/runner_bridge.py` and `lib/communication_api.py` so the surface remains human I/O only.
- [ ] Delete `lib/scheduler_components/audit.py` only after the new verification runner path is stable and all import sites have been moved.

**Verification:**
- Run from `harness-engineering/`: `python -m unittest tests.test_scheduler_verification -v` to prove execution now routes through verification evidence instead of audit semantics.
- Run from `harness-engineering/`: `python -m unittest tests.test_scheduler_round -v` to prove cleanup and round-close behavior still follow the new role order.

### Task 3: Retire old worker semantics from the agent directory

**Files:**
- Delete: `harness-engineering/agents/audit-agent/agent.json`
- Delete: `harness-engineering/agents/audit-agent/system.md`
- Delete: `harness-engineering/agents/communication-agent/agent.json`
- Delete: `harness-engineering/agents/communication-agent/system.md`
- Modify: `harness-engineering/agents/cleanup-agent/agent.json`
- Modify: `harness-engineering/agents/supervisor/policies.md`
- Modify: `harness-engineering/agents/supervisor/routing_contracts.md`

**Steps:**
- [ ] Remove the retired worker specs only after the new `decision` and `verification` specs are present and loading cleanly.
- [ ] Update the supervisor policy and routing docs so the default order, handoff chain, and escalation path no longer mention `communication-agent` or `audit-agent`.
- [ ] Keep cleanup positioned after verification acceptance, with recovery and maintenance as the only other cleanup entry points.

**Verification:**
- Run from `harness-engineering/`: `python main.py inspect` and confirm the old role ids are absent from the displayed topology.
- Run from `harness-engineering/`: `python -m unittest tests.test_question_routing -v` to confirm human gates are now framed as supervisor-owned runtime events rather than a worker lane.

### Task 4: Expand the regression matrix around role migration and cleanup boundaries

**Files:**
- Modify: `harness-engineering/tests/test_auto_answer.py`
- Modify: `harness-engineering/tests/test_question_routing.py`
- Modify: `harness-engineering/tests/test_resume_loop.py`
- Modify: `harness-engineering/tests/test_end_to_end_loop.py`
- Modify: `harness-engineering/tests/test_scheduler_round.py`
- Modify: `harness-engineering/tests/test_scheduler_verification.py`
- Modify: `harness-engineering/tests/test_runner.py`
- Add: `harness-engineering/tests/test_role_migration.py`

**Steps:**
- [ ] Update the existing test fixtures so they seed `decision` and `verification` agent ids instead of `communication` and `audit`.
- [ ] Add a topology test that asserts `main.py inspect --format json` returns the frozen worker set in the expected order and dependency chain.
- [ ] Add a scheduler test that proves an ambiguous blocker routes through the decision path instead of the old communication-worker path.
- [ ] Add a verification test that proves execution records verification commands and that the scheduler consumes those evidence artifacts before cleanup.
- [ ] Add an end-to-end loop test that completes one round with the new role ids and no residual `audit` or `communication` worker references in the reports.
- [ ] Add a resume test that proves the runtime can recover a pending decision or verification state without reintroducing the retired names.

**Verification:**
- Run from `harness-engineering/`: `python -m unittest tests.test_role_migration -v`.
- Run from `harness-engineering/`: `python -m unittest discover -s tests -p "test_*.py" -v` for the full harness matrix.

## Done Criteria

- `python main.py inspect --format json` from `harness-engineering/` shows `decision`, `design`, `execution`, `verification`, and `cleanup` with no `communication` or `audit` worker ids.
- The role specs, supervisor docs, README files, and scheduler code all describe the same worker model and the same cleanup boundary.
- The harness test suite passes with the new role names, including the new role-migration coverage and the updated end-to-end loop tests.
- Cleanup only runs after verification acceptance, maintenance, or recovery, not as a replacement for verification.

## Known Risks

- `lib/communication_api.py` and `lib/runner_bridge.py` may still assume `communication` is a worker id, so a partial rename could leave the human surface and the worker graph out of sync.
- `audit` is used in several scheduler tests and launcher fixtures, so renaming the role without updating monkeypatch targets in the same pass can produce false negatives.
- The new `decision` lane changes both topology and human-gate behavior, so a mismatch between the docs and the scheduler branches could cause blockers to auto-answer instead of escalating.
- Cleanup depends on verification acceptance state, so a bad migration can accidentally schedule cleanup too early or leave stale worktrees behind.

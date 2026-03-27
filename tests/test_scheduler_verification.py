from __future__ import annotations

import json
import tempfile
from pathlib import Path
import subprocess
from unittest.mock import patch
import unittest

from lib.communication_api import CommunicationStore
from lib.runner_bridge import RunnerTurn
from lib.runtime_state import (
    HarnessConfig,
    Mission,
    RuntimeState,
    ensure_runtime_root,
    save_mission,
    save_state,
    utc_now,
)
from lib.scheduler_components.audit import run_saved_audit_request
from lib.scheduler_components.design import run_saved_design_request
from lib.scheduler import HarnessScheduler
from main import build_or_update_mission, load_all_specs, validate_specs


def _fake_execution_result(*, needs_human: bool = False) -> dict[str, object]:
    return {
        "ok": True,
        "exit_code": 0,
        "command": ["codex.cmd", "exec"],
        "stdout": "",
        "stderr": "",
        "parsed_output": {
            "status": "implemented",
            "summary": "Implemented the planned slice.",
            "changed_paths": ["src/center/app/models/task.py"],
            "verification_notes": [],
            "needs_human": needs_human,
            "human_question": "Need a real decision." if needs_human else "",
            "why_not_auto_answered": "A decision gate was encountered." if needs_human else "",
            "required_reply_shape": "Reply with continue or replan." if needs_human else "",
            "decision_tags": ["goal_conflict"] if needs_human else [],
            "options": [{"label": "Continue", "value": "continue", "description": "Keep the slice."}] if needs_human else [],
            "notes": ["Execution used subagents for modification work."],
        },
        "pre_git_status": {"entries": []},
        "post_git_status": {"entries": ["M src/center/app/models/task.py"]},
    }


def _verification_run(spec: dict[str, object], *, returncode: int = 0) -> dict[str, object]:
    return {
        "command": spec.get("command", []),
        "command_display": spec.get("command_display", "pytest"),
        "cwd": spec.get("cwd", ""),
        "env": spec.get("env", {}),
        "source": spec.get("source", "mapping"),
        "started_at": "2026-03-26T00:00:00Z",
        "completed_at": "2026-03-26T00:00:01Z",
        "returncode": returncode,
        "stdout": "ok\n" if returncode == 0 else "",
        "stderr": "" if returncode == 0 else "boom\n",
    }


def _launch_execution_immediately(**kwargs: object) -> dict[str, object]:
    result_path = Path(str(kwargs["result_path"]))
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(_fake_execution_result(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "pid": 1234,
        "command": ["python", "codex_execution_launcher.py"],
        "started_at": utc_now(),
    }


def _launch_background_immediately(**kwargs: object) -> dict[str, object]:
    common_kwargs = {
        "request_path": Path(str(kwargs["request_path"])),
        "result_path": Path(str(kwargs["result_path"])),
        "launcher_state_path": Path(str(kwargs["launcher_state_path"])),
        "launcher_run_path": Path(str(kwargs["launcher_run_path"])),
    }
    agent_id = str(kwargs["agent_id"])
    if agent_id == "design":
        run_saved_design_request(**common_kwargs)
    elif agent_id == "audit":
        run_saved_audit_request(**common_kwargs)
    else:
        raise AssertionError(f"unexpected background agent: {agent_id}")
    return {
        "ok": True,
        "pid": 1234,
        "command": ["python", "codex_agent_launcher.py", "--agent-id", agent_id],
        "started_at": utc_now(),
    }


def _make_scheduler(temp_dir: str) -> tuple[HarnessScheduler, Path, Path, Path]:
    root = Path(temp_dir)
    project_root = root / "AIMA-refactor"
    doc_root = project_root / "docs"
    doc_root.mkdir(parents=True)
    (doc_root / "README.md").write_text("# Demo\n\nVerification evidence test.\n", encoding="utf-8")
    _init_git_repo(project_root)

    memory_root = root / "memory"
    paths = ensure_runtime_root(memory_root)
    mission = Mission(
        doc_root=str(doc_root),
        goal="verify execution and audit evidence",
        status="active",
        round=0,
        extra={},
    )
    state = RuntimeState(
        active_agent="execution",
        last_successful_agent="design",
        retry_count=0,
        last_run_at="2026-03-26T00:00:00Z",
        current_round=0,
        extra={"status": "running"},
    )
    scheduler = HarnessScheduler(
        specs=[
            {"id": "communication", "name": "Communication Agent", "order": 10, "dependencies": (), "goal": "Handle human-facing communication."},
            {"id": "design", "name": "Design Agent", "order": 20, "dependencies": ("communication",), "goal": "Define the next approved slice."},
            {"id": "execution", "name": "Execution Agent", "order": 30, "dependencies": ("design",), "goal": "Implement the approved slice."},
            {"id": "audit", "name": "Audit Agent", "order": 40, "dependencies": ("execution",), "goal": "Audit the implementation slice."},
        ],
        paths=paths,
        mission=mission,
        state=state,
    )
    return scheduler, paths, doc_root, project_root


def _make_replan_scheduler(temp_dir: str) -> tuple[HarnessScheduler, Path, Path, Path, Path, dict[str, object]]:
    root = Path(temp_dir)
    project_root = root / "AIMA-refactor"
    doc_root = project_root / "docs"
    doc_root.mkdir(parents=True)
    (doc_root / "README.md").write_text(
        "# Demo Project\n\nThis repo is organized around task-centered planning.\n",
        encoding="utf-8",
    )
    plan_path = doc_root / "plans" / "2026-03-25-task-mainline-and-engineernode-removal.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        "\n".join(
            [
                "# Plan: Task Mainline and EngineerNode Removal",
                "",
                "### Phase 1: Stabilize the baseline",
                "Goals",
                "- Keep the baseline stable.",
                "File Targets",
                "- src/center/app/models/**",
                "Done Criteria",
                "- Baseline is stable.",
                "",
                "### Phase 2: Replace the center data mainline",
                "Goals",
                "- make `Task` and `Conversation` the center-facing core model",
                "- stop introducing new endpoints around `SupportSession`",
                "File Targets",
                "- `src/center/app/models/**`",
                "- `src/center/app/schemas/**`",
                "- `src/center/app/api/**`",
                "- `src/center/alembic/**`",
                "Done Criteria",
                "- new task APIs exist",
                "- conversation timeline is modeled directly under `Task`",
                "- new code no longer depends on `EngineerNode`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _init_git_repo(project_root)

    memory_root = root / "memory"
    config = HarnessConfig.from_mapping(
        {"memory_root": str(memory_root), "doc_root": str(doc_root), "goal": "supervisor decision replay"}
    )
    paths = ensure_runtime_root(memory_root)
    mission = build_or_update_mission(config, doc_root=doc_root)
    decision = {
        "id": "answer-replan-001",
        "gate_id": "gate-stall-001",
        "choice": "replan",
        "answer": (
            "replan\n"
            "Create a blocker slice for the Windows path issue.\n"
            "Do not reopen the old Phase 2 contract unchanged."
        ),
        "constraints": [
            "Create a blocker slice for the Windows path issue.",
            "Do not reopen the old Phase 2 contract unchanged.",
        ],
        "current_context": {},
    }
    state = RuntimeState(
        active_agent="design",
        last_successful_agent="",
        retry_count=0,
        last_run_at=utc_now(),
        current_round=0,
        extra={
            "status": "running",
            "pending_gate_id": "gate-stall-001",
            "blocked_agent": "design",
            "resume_agent": "design",
            "pending_supervisor_decision": decision,
        },
    )
    mission.extra["human_decisions"] = [decision]
    save_mission(paths.memory_root, mission)
    save_state(paths.memory_root, state)

    specs = load_all_specs()
    validate_specs(specs)
    scheduler = HarnessScheduler(specs=specs, paths=paths, mission=mission, state=state)

    stalled_contract_path = paths.artifacts_dir / "cycle-stalled" / "00-design-contract.json"
    stalled_contract_path.parent.mkdir(parents=True, exist_ok=True)
    stalled_contract_path.write_text(
        json.dumps(
            {
                "goal": mission.goal,
                "doc_summary": mission.goal,
                "doc_count": mission.extra.get("doc_count", 0),
                "doc_root": str(doc_root),
                "project_root": str(project_root),
                "execution_scope": "external_project",
                "selected_primary_doc": "plans/2026-03-25-task-mainline-and-engineernode-removal.md",
                "selected_planning_doc": "plans/2026-03-25-task-mainline-and-engineernode-removal.md",
                "baseline_docs": [],
                "selected_phase": {
                    "title": "Phase 2: Replace the center data mainline",
                    "goals": [
                        "make `Task` and `Conversation` the center-facing core model",
                        "stop introducing new endpoints around `SupportSession`",
                    ],
                    "file_targets": [
                        "`src/center/app/models/**`",
                        "`src/center/app/schemas/**`",
                        "`src/center/app/api/**`",
                        "`src/center/alembic/**`",
                    ],
                    "done_criteria": [
                        "new task APIs exist",
                        "conversation timeline is modeled directly under `Task`",
                        "new code no longer depends on `EngineerNode`",
                    ],
                },
                "slice_key": "plans/2026-03-25-task-mainline-and-engineernode-removal.md::phase 2: replace the center data mainline",
                "work_status": "ready",
                "remaining_phase_count": 2,
                "proposed_slice": f"Advance Phase 2: Replace the center data mainline under {project_root}.",
                "work_items": [
                    "make `Task` and `Conversation` the center-facing core model",
                    "stop introducing new endpoints around `SupportSession`",
                ],
                "target_paths": [
                    "`src/center/app/models/**`",
                    "`src/center/app/schemas/**`",
                    "`src/center/app/api/**`",
                    "`src/center/alembic/**`",
                ],
                "acceptance_criteria": [
                    "new task APIs exist",
                    "conversation timeline is modeled directly under `Task`",
                    "new code no longer depends on `EngineerNode`",
                ],
                "verification_expectation": [],
                "maintenance_findings": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    decision["current_context"] = {"design_contract": json.loads(stalled_contract_path.read_text(encoding="utf-8"))}
    return scheduler, paths, doc_root, project_root, stalled_contract_path, decision


def _init_git_repo(project_root: Path) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    commands = [
        ["git", "init"],
        ["git", "config", "user.email", "harness-tests@example.com"],
        ["git", "config", "user.name", "Harness Tests"],
        ["git", "add", "."],
        ["git", "commit", "--allow-empty", "-m", "init"],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=str(project_root), capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout or f"failed: {' '.join(command)}")


class SchedulerVerificationTests(unittest.TestCase):
    def test_execution_records_subagent_and_verification_evidence_and_audit_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler, paths, doc_root, project_root = _make_scheduler(temp_dir)
            design_artifact = paths.artifacts_dir / "cycle-test" / "00-design-contract.json"
            design_artifact.parent.mkdir(parents=True, exist_ok=True)
            expected_commands = [["python", "-m", "pytest", "tests", "-q"]]
            design_artifact.write_text(
                json.dumps(
                    {
                        "project_root": str(project_root),
                        "selected_primary_doc": "docs/README.md",
                        "selected_planning_doc": "plans/demo.md",
                        "baseline_docs": ["designs/2026-03-25-task-centered-autonomous-ops-platform.md"],
                        "execution_scope": "external_project",
                        "selected_phase": {"title": "Phase 2"},
                        "slice_key": "plans/demo.md::phase 2",
                        "verification_expectation": expected_commands,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            execution_turn = RunnerTurn(
                cycle_id="cycle-test",
                sequence=2,
                agent_spec={"id": "execution", "name": "Execution Agent"},
                handoff={
                    "inputs": {
                        "latest_artifacts": {"design": [str(design_artifact)]},
                        "selected_primary_doc": str(doc_root / "README.md"),
                        "human_decisions": [],
                    }
                },
                runtime_paths={},
                mission=scheduler.mission.to_mapping(),
                state=scheduler.state.to_mapping(),
                handoff_path=paths.handoffs_dir / "cycle-test-02-execution.json",
                report_path=paths.reports_dir / "cycle-test-02-execution.json",
                communication_store=CommunicationStore(paths.harness_root),
            )

            with patch("lib.scheduler._launch_execution_subagent", side_effect=_launch_execution_immediately), patch(
                "lib.scheduler._run_verification_command",
                side_effect=lambda spec: _verification_run(spec, returncode=0),
            ):
                execution_report = scheduler._execute_turn(execution_turn)

            execution_artifact_path = Path(execution_report["artifacts"][-1])
            execution_payload = json.loads(execution_artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(execution_payload["verification_status"], "passed")
            self.assertEqual(execution_payload["execution_subagent"]["exit_code"], 0)
            self.assertEqual(execution_payload["execution_output"]["status"], "implemented")

            audit_turn = RunnerTurn(
                cycle_id="cycle-test",
                sequence=3,
                agent_spec={"id": "audit", "name": "Audit Agent"},
                handoff={
                    "inputs": {
                        "latest_artifacts": {"execution": [str(execution_artifact_path)]},
                    }
                },
                runtime_paths={},
                mission=scheduler.mission.to_mapping(),
                state=scheduler.state.to_mapping(),
                handoff_path=paths.handoffs_dir / "cycle-test-03-audit.json",
                report_path=paths.reports_dir / "cycle-test-03-audit.json",
                communication_store=CommunicationStore(paths.harness_root),
            )

            with patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ):
                audit_report = scheduler._execute_turn(audit_turn)
            audit_artifact_path = Path(audit_report["artifacts"][-1])
            audit_payload = json.loads(audit_artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(audit_report["status"], "accepted")
            self.assertTrue(audit_payload["accepted"])
            self.assertEqual(audit_payload["findings"], [])

    def test_audit_reopens_when_verification_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler, paths, _, project_root = _make_scheduler(temp_dir)
            design_artifact = paths.artifacts_dir / "cycle-test" / "00-design-contract.json"
            design_artifact.parent.mkdir(parents=True, exist_ok=True)
            design_artifact.write_text(
                json.dumps(
                    {
                        "project_root": str(project_root),
                        "selected_primary_doc": "docs/README.md",
                        "selected_planning_doc": "plans/demo.md",
                        "baseline_docs": ["designs/2026-03-25-task-centered-autonomous-ops-platform.md"],
                        "execution_scope": "external_project",
                        "selected_phase": {"title": "Phase 2"},
                        "slice_key": "plans/demo.md::phase 2",
                        "verification_expectation": [["python", "-m", "pytest", "tests", "-q"]],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            execution_turn = RunnerTurn(
                cycle_id="cycle-test",
                sequence=2,
                agent_spec={"id": "execution", "name": "Execution Agent"},
                handoff={"inputs": {"latest_artifacts": {"design": [str(design_artifact)]}, "human_decisions": []}},
                runtime_paths={},
                mission=scheduler.mission.to_mapping(),
                state=scheduler.state.to_mapping(),
                handoff_path=paths.handoffs_dir / "cycle-test-02-execution.json",
                report_path=paths.reports_dir / "cycle-test-02-execution.json",
                communication_store=CommunicationStore(paths.harness_root),
            )

            with patch("lib.scheduler._launch_execution_subagent", side_effect=_launch_execution_immediately), patch(
                "lib.scheduler._run_verification_command",
                side_effect=lambda spec: _verification_run(spec, returncode=3),
            ):
                execution_report = scheduler._execute_turn(execution_turn)

            execution_artifact_path = Path(execution_report["artifacts"][-1])
            audit_turn = RunnerTurn(
                cycle_id="cycle-test",
                sequence=3,
                agent_spec={"id": "audit", "name": "Audit Agent"},
                handoff={"inputs": {"latest_artifacts": {"execution": [str(execution_artifact_path)]}}},
                runtime_paths={},
                mission=scheduler.mission.to_mapping(),
                state=scheduler.state.to_mapping(),
                handoff_path=paths.handoffs_dir / "cycle-test-03-audit.json",
                report_path=paths.reports_dir / "cycle-test-03-audit.json",
                communication_store=CommunicationStore(paths.harness_root),
            )

            with patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ):
                audit_report = scheduler._execute_turn(audit_turn)
            audit_artifact_path = Path(audit_report["artifacts"][-1])
            audit_payload = json.loads(audit_artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(audit_report["status"], "reopen_execution")
            self.assertFalse(audit_payload["accepted"])
            self.assertTrue(any("returned 3" in finding for finding in audit_payload["findings"]))

    def test_external_project_repeated_reopen_auto_replans_without_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler, paths, _, project_root = _make_scheduler(temp_dir)
            design_artifact = paths.artifacts_dir / "cycle-test" / "00-design-contract.json"
            design_artifact.parent.mkdir(parents=True, exist_ok=True)
            design_artifact.write_text(
                json.dumps(
                    {
                        "project_root": str(project_root),
                        "selected_primary_doc": "docs/README.md",
                        "selected_planning_doc": "plans/demo.md",
                        "baseline_docs": ["designs/2026-03-25-task-centered-autonomous-ops-platform.md"],
                        "execution_scope": "external_project",
                        "selected_phase": {"title": "Phase 2"},
                        "slice_key": "plans/demo.md::phase 2",
                        "verification_expectation": [["python", "-m", "pytest", "tests", "-q"]],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            scheduler.state.extra["latest_artifacts"] = {"design": [str(design_artifact)]}
            scheduler.state.active_agent = "execution"

            with patch("lib.scheduler._launch_execution_subagent", side_effect=_launch_execution_immediately), patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ), patch(
                "lib.scheduler._run_verification_command",
                side_effect=lambda spec: _verification_run(spec, returncode=7),
            ):
                result = scheduler.run_until_stable(max_turns=10)

            self.assertEqual(result.status, "running")
            self.assertFalse(result.pending_gate_id)
            history = scheduler.mission.extra.get("supervisor_decisions", [])
            pending = scheduler.mission.extra.get("pending_supervisor_decision", {})
            self.assertTrue(
                any(isinstance(item, dict) and item.get("choice") == "replan" and item.get("auto_generated") for item in history)
                or (isinstance(pending, dict) and pending.get("choice") == "replan" and pending.get("auto_generated"))
            )

    def test_replan_reply_changes_the_next_design_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler, paths, _, project_root, stalled_contract_path, decision = _make_replan_scheduler(temp_dir)
            stalled_contract = json.loads(stalled_contract_path.read_text(encoding="utf-8"))

            design_turn = RunnerTurn(
                cycle_id="cycle-replan",
                sequence=0,
                agent_spec={"id": "design", "name": "Design Agent"},
                handoff={
                    "inputs": {
                        "latest_artifacts": {"design": [str(stalled_contract_path)]},
                        "human_decisions": [decision],
                        "pending_supervisor_decision": decision,
                        "selected_primary_doc": "plans/2026-03-25-task-mainline-and-engineernode-removal.md",
                    }
                },
                runtime_paths={},
                mission=scheduler.mission.to_mapping(),
                state=scheduler.state.to_mapping(),
                handoff_path=paths.handoffs_dir / "cycle-replan-00-design.json",
                report_path=paths.reports_dir / "cycle-replan-00-design.json",
                communication_store=CommunicationStore(paths.harness_root),
            )

            with patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ):
                design_report = scheduler._execute_turn(design_turn)
            design_artifact_path = Path(design_report["artifacts"][-1])
            design_payload = json.loads(design_artifact_path.read_text(encoding="utf-8"))

            self.assertNotEqual(design_payload["slice_key"], stalled_contract["slice_key"])
            self.assertNotEqual(
                design_payload["selected_phase"].get("title"),
                stalled_contract["selected_phase"].get("title"),
            )
            self.assertIn("blocker", json.dumps(design_payload, ensure_ascii=False).lower())
            self.assertEqual(
                design_payload["origin_phase_title"],
                "Phase 2: Replace the center data mainline",
            )
            self.assertTrue(design_payload["is_blocker_slice"])

    def test_replan_reply_is_consumed_after_the_design_turn_applies_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler, paths, _, _, stalled_contract_path, decision = _make_replan_scheduler(temp_dir)

            scheduler.state.extra["pending_supervisor_decision"] = decision

            design_turn = RunnerTurn(
                cycle_id="cycle-replan",
                sequence=0,
                agent_spec={"id": "design", "name": "Design Agent"},
                handoff={
                    "inputs": {
                        "latest_artifacts": {"design": [str(stalled_contract_path)]},
                        "human_decisions": [decision],
                        "pending_supervisor_decision": decision,
                        "selected_primary_doc": "plans/2026-03-25-task-mainline-and-engineernode-removal.md",
                    }
                },
                runtime_paths={},
                mission=scheduler.mission.to_mapping(),
                state=scheduler.state.to_mapping(),
                handoff_path=paths.handoffs_dir / "cycle-replan-00-design.json",
                report_path=paths.reports_dir / "cycle-replan-00-design.json",
                communication_store=CommunicationStore(paths.harness_root),
            )

            with patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ):
                design_report = scheduler._execute_turn(design_turn)
            scheduler._advance_after_report(
                "design",
                {
                    "cycle_id": "cycle-replan",
                    "handoff_path": str(design_turn.handoff_path),
                    "report_path": str(design_turn.report_path),
                    "state_after": {"cycle_id": "cycle-replan", "sequence": 1},
                    "report": design_report,
                },
            )

            self.assertNotIn("pending_supervisor_decision", scheduler.state.extra)
            self.assertEqual(len(scheduler.mission.extra.get("human_decisions", [])), 1)

    def test_repeated_blocker_failure_auto_replans_without_opening_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler, paths, _, project_root, _, _ = _make_replan_scheduler(temp_dir)
            scheduler.state.extra.pop("pending_gate_id", None)
            scheduler.state.extra.pop("blocked_agent", None)
            scheduler.state.extra.pop("resume_agent", None)
            scheduler.state.extra.pop("pending_supervisor_decision", None)
            scheduler.mission.extra.pop("pending_supervisor_decision", None)
            scheduler.mission.extra["human_decisions"] = []
            blocker_contract = {
                "project_root": str(project_root),
                "selected_primary_doc": "plans/2026-03-25-task-mainline-and-engineernode-removal.md",
                "selected_planning_doc": "plans/2026-03-25-task-mainline-and-engineernode-removal.md",
                "execution_scope": "external_project",
                "selected_phase": {"title": "Blocker slice: unblock Phase 2: Replace the center data mainline"},
                "origin_phase_title": "Phase 2: Replace the center data mainline",
                "is_blocker_slice": True,
                "slice_key": "plans/demo.md::phase 2::blocker::001",
                "verification_expectation": [["python", "-m", "pytest", "tests", "-q"]],
            }
            design_artifact = paths.artifacts_dir / "cycle-test" / "00-design-contract.json"
            design_artifact.parent.mkdir(parents=True, exist_ok=True)
            design_artifact.write_text(json.dumps(blocker_contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            scheduler.state.extra["latest_artifacts"] = {"design": [str(design_artifact)]}
            scheduler.state.active_agent = "execution"

            with patch("lib.scheduler._launch_execution_subagent", side_effect=_launch_execution_immediately), patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ), patch(
                "lib.scheduler._run_verification_command",
                side_effect=lambda spec: _verification_run(spec, returncode=7),
            ):
                result = scheduler.run_until_stable(max_turns=10)

            self.assertEqual(result.status, "running")
            self.assertFalse(result.pending_gate_id)
            history = scheduler.mission.extra.get("supervisor_decisions", [])
            pending = scheduler.mission.extra.get("pending_supervisor_decision", {})
            self.assertTrue(
                any(isinstance(item, dict) and item.get("choice") == "replan" and item.get("auto_generated") for item in history)
                or (isinstance(pending, dict) and pending.get("choice") == "replan" and pending.get("auto_generated"))
            )


if __name__ == "__main__":
    unittest.main()

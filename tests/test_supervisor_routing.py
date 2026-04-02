from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.runtime_state import (
    HarnessConfig,
    RuntimeState,
    ensure_runtime_root,
    save_mission,
    save_state,
    utc_now,
)
from lib.scheduler import HarnessScheduler
from main import build_or_update_mission, load_all_specs, validate_specs


def _init_git_repo(project_root: Path) -> None:
    commands = [
        ["git", "init"],
        ["git", "config", "user.email", "harness-tests@example.com"],
        ["git", "config", "user.name", "Harness Tests"],
        ["git", "add", "."],
        ["git", "commit", "-m", "init"],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=str(project_root), capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout or f"failed: {' '.join(command)}")


class SupervisorRoutingTests(unittest.TestCase):
    def _make_scheduler(self, temp_dir: str) -> tuple[HarnessScheduler, Path, Path, Path]:
        root = Path(temp_dir)
        project_root = root / "AIMA-refactor"
        doc_root = project_root / "docs"
        doc_root.mkdir(parents=True)
        (doc_root / "README.md").write_text("# Demo\n\nSupervisor routing tests.\n", encoding="utf-8")
        _init_git_repo(project_root)

        memory_root = root / "memory"
        config = HarnessConfig.from_mapping(
            {
                "memory_root": str(memory_root),
                "doc_root": str(doc_root),
                "goal": "freeze supervisor routing outcomes",
                "decision_gate_tags": ["goal_conflict"],
            }
        )
        paths = ensure_runtime_root(memory_root)
        mission = build_or_update_mission(config, doc_root=doc_root)
        state = RuntimeState(
            active_agent="design",
            last_successful_agent="",
            retry_count=0,
            last_run_at=utc_now(),
            current_round=0,
            extra={"status": "running"},
        )
        save_mission(paths.memory_root, mission)
        save_state(paths.memory_root, state)

        specs = load_all_specs()
        validate_specs(specs)
        scheduler = HarnessScheduler(specs=specs, paths=paths, mission=mission, state=state)
        return scheduler, paths, doc_root, project_root

    def test_gate_flow_emits_supervisor_events_before_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler, _, _, _ = self._make_scheduler(temp_dir)

            route = scheduler._route_questions(
                "design",
                {
                    "questions": [
                        {
                            "question_id": "q-gate-001",
                            "agent": "design",
                            "question": "Should we stop for the conflict gate?",
                            "blocking": True,
                            "importance": "high",
                            "tags": ["goal_conflict"],
                            "context": {"marker": "decision-gate"},
                        }
                    ]
                },
            )

            self.assertEqual(route, "gate")
            self.assertEqual(scheduler.state.extra["blocked_agent"], "design")
            self.assertEqual(scheduler.state.extra["resume_agent"], "design")
            self.assertTrue(scheduler.state.extra["pending_gate_id"])
            self.assertEqual(scheduler.state.active_agent, "")
            self.assertNotEqual(scheduler.state.active_agent, scheduler.communication_agent_id)

            recent_events = scheduler.state.extra["recent_events"]
            self.assertEqual([event["kind"] for event in recent_events[-2:]], ["worker_blocked", "human_gate_opened"])
            self.assertEqual(recent_events[-2]["outcome"], "route_to_decision")
            self.assertEqual(recent_events[-1]["outcome"], "route_to_decision")
            self.assertEqual(recent_events[-1]["subject"], "design")

    def test_audit_accept_records_frozen_route_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler, paths, _, project_root = self._make_scheduler(temp_dir)
            scheduler.cleanup_agent_id = None

            verdict_path = paths.artifacts_dir / "cycle-audit" / "01-audit-verdict.json"
            verdict_path.parent.mkdir(parents=True, exist_ok=True)
            execution_artifact_path = paths.artifacts_dir / "cycle-audit" / "00-execution.json"
            execution_artifact_path.write_text(
                json.dumps(
                    {
                        "design_contract": {
                            "project_root": str(project_root),
                            "canonical_project_root": str(project_root),
                            "slice_key": "docs/README.md::phase-1",
                            "selected_phase": {"title": "Phase 1"},
                            "selected_primary_doc": "docs/README.md",
                            "execution_scope": "harness_internal",
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            verdict_path.write_text(
                json.dumps(
                    {
                        "audit_status": "accepted",
                        "findings": [],
                        "execution_artifact_path": str(execution_artifact_path),
                        "design_contract": {
                            "project_root": str(project_root),
                            "canonical_project_root": str(project_root),
                            "slice_key": "docs/README.md::phase-1",
                            "selected_phase": {"title": "Phase 1"},
                            "selected_primary_doc": "docs/README.md",
                            "execution_scope": "harness_internal",
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            report = {
                "cycle_id": "cycle-audit",
                "handoff_path": str(paths.briefs_dir / "cycle-audit-02-audit.json"),
                "report_path": str(paths.briefs_dir / "cycle-audit-02-audit.json"),
                "state_after": {"cycle_id": "cycle-audit", "sequence": 3},
                "report": {
                    "status": "completed",
                    "audit_status": "accepted",
                    "summary": "Audit accepted the round.",
                    "artifacts": [str(verdict_path)],
                },
            }

            with patch.object(scheduler, "_promote_execution_worktree", return_value=[]), patch.object(
                scheduler,
                "_release_execution_worktree",
                return_value=None,
            ):
                scheduler._advance_after_report("audit", report)

            self.assertEqual(scheduler.state.extra["supervisor_route_outcome"], "accept")
            self.assertEqual(scheduler.state.extra["supervisor_route_subject"], "audit")
            self.assertEqual(scheduler.state.extra["recent_events"][-1]["kind"], "supervisor_route_outcome")
            self.assertEqual(scheduler.state.extra["recent_events"][-1]["outcome"], "accept")
            self.assertEqual(scheduler.state.current_round, 1)
            self.assertEqual(scheduler.mission.round, 1)
            self.assertEqual(scheduler.state.active_agent, scheduler.design_agent_id)

    def test_audit_replan_and_reopen_record_frozen_route_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scheduler, paths, _, project_root = self._make_scheduler(temp_dir)

            execution_artifact_path = paths.artifacts_dir / "cycle-audit" / "00-execution.json"
            execution_artifact_path.parent.mkdir(parents=True, exist_ok=True)
            execution_artifact_path.write_text(
                json.dumps(
                    {
                        "design_contract": {
                            "project_root": str(project_root),
                            "canonical_project_root": str(project_root),
                            "slice_key": "docs/README.md::phase-1",
                            "selected_phase": {"title": "Phase 1"},
                            "selected_primary_doc": "docs/README.md",
                            "execution_scope": "harness_internal",
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            cases = [
                ("replan_design", scheduler.design_agent_id, "replan_design"),
                ("reopen_execution", scheduler.execution_agent_id, "reopen_execution"),
            ]

            for audit_status, expected_agent, expected_outcome in cases:
                with self.subTest(audit_status=audit_status):
                    verdict_path = paths.artifacts_dir / "cycle-audit" / f"01-{audit_status}.json"
                    verdict_path.write_text(
                        json.dumps(
                            {
                                "audit_status": audit_status,
                                "findings": ["Need a new plan."],
                                "execution_artifact_path": str(execution_artifact_path),
                                "design_contract": {
                                    "project_root": str(project_root),
                                    "canonical_project_root": str(project_root),
                                    "slice_key": "docs/README.md::phase-1",
                                    "selected_phase": {"title": "Phase 1"},
                                    "selected_primary_doc": "docs/README.md",
                                    "execution_scope": "harness_internal",
                                },
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    report = {
                        "cycle_id": "cycle-audit",
                        "handoff_path": str(paths.briefs_dir / "cycle-audit-02-audit.json"),
                        "report_path": str(paths.briefs_dir / "cycle-audit-02-audit.json"),
                        "state_after": {"cycle_id": "cycle-audit", "sequence": 3},
                        "report": {
                            "status": "completed",
                            "audit_status": audit_status,
                            "summary": f"Audit requested {audit_status}.",
                            "artifacts": [str(verdict_path)],
                        },
                    }

                    with patch.object(scheduler, "_release_execution_worktree", return_value=None), patch.object(
                        scheduler,
                        "_promote_execution_worktree",
                        return_value=[],
                    ):
                        scheduler._advance_after_report("audit", report)

                    self.assertEqual(scheduler.state.extra["supervisor_route_outcome"], expected_outcome)
                    self.assertEqual(scheduler.state.extra["supervisor_route_subject"], "audit")
                    self.assertEqual(scheduler.state.extra["recent_events"][-1]["kind"], "supervisor_route_outcome")
                    self.assertEqual(scheduler.state.extra["recent_events"][-1]["outcome"], expected_outcome)
                    self.assertEqual(scheduler.state.active_agent, expected_agent)


if __name__ == "__main__":
    unittest.main()

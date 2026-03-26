from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from lib.runtime_state import (
    HarnessConfig,
    RuntimeState,
    ensure_runtime_root,
    load_mission,
    load_state,
    save_mission,
    save_state,
    utc_now,
)
from lib.scheduler import HarnessScheduler
from main import build_or_update_mission, load_all_specs, validate_specs


def _fake_execution_result() -> dict[str, object]:
    return {
        "ok": True,
        "exit_code": 0,
        "command": ["codex.cmd", "exec"],
        "stdout": "",
        "stderr": "",
        "parsed_output": {
            "status": "implemented",
            "summary": "Implemented the approved slice.",
            "changed_paths": ["README.md"],
            "verification_notes": [],
            "needs_human": False,
            "human_question": "",
            "why_not_auto_answered": "",
            "required_reply_shape": "",
            "decision_tags": [],
            "options": [],
            "notes": ["Execution used subagents for modification work."],
        },
        "pre_git_status": {"entries": []},
        "post_git_status": {"entries": ["M README.md"]},
    }


def _fake_verification_run(spec: dict[str, object]) -> dict[str, object]:
    return {
        "command": spec.get("command", []),
        "command_display": spec.get("command_display", "pytest"),
        "cwd": spec.get("cwd", ""),
        "env": spec.get("env", {}),
        "source": spec.get("source", "mapping"),
        "started_at": utc_now(),
        "completed_at": utc_now(),
        "returncode": 0,
        "stdout": "ok\n",
        "stderr": "",
    }


class SchedulerRoundTests(unittest.TestCase):
    def _seed_runtime(self, temp_dir: Path) -> tuple[Path, Path, HarnessScheduler]:
        doc_root = temp_dir / "docs"
        doc_root.mkdir()
        (doc_root / "README.md").write_text("# Demo Project\n\nThis is the primary project doc.\n", encoding="utf-8")

        memory_root = temp_dir / "memory"
        config = HarnessConfig.from_mapping(
            {"memory_root": str(memory_root), "doc_root": str(doc_root), "goal": "round persistence"}
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
        return doc_root, memory_root, scheduler

    def test_cycle_state_persists_across_resume_between_agents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, memory_root, scheduler = self._seed_runtime(temp_path)

            first_pass = scheduler.run_until_stable(max_turns=1)
            self.assertEqual(len(first_pass.steps), 1)

            first_step = first_pass.steps[0]
            first_cycle_id = first_step["cycle_id"]
            self.assertEqual(first_step["state_after"]["sequence"], 1)

            persisted_state = load_state(memory_root)
            self.assertEqual(persisted_state.extra["cycle_id"], first_cycle_id)
            self.assertEqual(persisted_state.extra["sequence"], 1)

            resumed_mission = load_mission(memory_root)
            resumed_scheduler = HarnessScheduler(
                specs=scheduler.specs,
                paths=scheduler.paths,
                mission=resumed_mission,
                state=persisted_state,
            )
            second_pass = resumed_scheduler.run_until_stable(max_turns=1)
            self.assertEqual(len(second_pass.steps), 1)

            second_step = second_pass.steps[0]
            self.assertEqual(second_step["cycle_id"], first_cycle_id)
            self.assertEqual(second_step["state_after"]["sequence"], 2)

            resumed_state = load_state(memory_root)
            self.assertEqual(resumed_state.extra["cycle_id"], first_cycle_id)
            self.assertEqual(resumed_state.extra["sequence"], 2)

    def test_cleanup_round_close_clears_turn_identity_and_keeps_mission_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, memory_root, scheduler = self._seed_runtime(temp_path)

            with patch("lib.scheduler._run_execution_subagent", return_value=_fake_execution_result()), patch(
                "lib.scheduler._run_verification_command",
                side_effect=_fake_verification_run,
            ):
                first_pass = scheduler.run_until_stable(max_turns=12)
            self.assertEqual(first_pass.status, "completed")
            self.assertGreater(len(first_pass.steps), 1)

            self.assertGreaterEqual(len({step["cycle_id"] for step in first_pass.steps}), 2)

            completed_state = load_state(memory_root)
            self.assertNotIn("cycle_id", completed_state.extra)
            self.assertNotIn("sequence", completed_state.extra)
            self.assertEqual(completed_state.current_round, 1)

            second_pass = scheduler.run_until_stable(max_turns=1)
            self.assertEqual(second_pass.status, "completed")
            self.assertEqual(len(second_pass.steps), 0)

    def test_rerun_reuses_cycle_but_advances_sequence_after_auto_answer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, _, scheduler = self._seed_runtime(temp_path)
            scheduler.state.active_agent = "design"
            scheduler._set_runner_turn_state(cycle_id="cycle-rerun", sequence=0)

            scheduler.runner.run_agent = Mock(
                side_effect=[
                    {
                        "cycle_id": "cycle-rerun",
                        "handoff_path": str(scheduler.paths.handoffs_dir / "cycle-rerun-00-design.json"),
                        "report_path": str(scheduler.paths.reports_dir / "cycle-rerun-00-design.json"),
                        "state_after": {"cycle_id": "cycle-rerun", "sequence": 1},
                        "report": {
                            "status": "blocked",
                            "summary": "Need a routine path answer.",
                            "questions": [
                                {
                                    "question_id": "q-rerun-001",
                                    "agent": "design",
                                    "question": "Which path should we use?",
                                    "blocking": False,
                                    "importance": "low",
                                    "tags": ["path"],
                                    "context": {"candidate_paths": ["docs/a.md", "docs/b.md"]},
                                }
                            ],
                            "artifacts": [],
                            "next_hint": "design",
                        },
                    },
                    {
                        "cycle_id": "cycle-rerun",
                        "handoff_path": str(scheduler.paths.handoffs_dir / "cycle-rerun-01-design.json"),
                        "report_path": str(scheduler.paths.reports_dir / "cycle-rerun-01-design.json"),
                        "state_after": {"cycle_id": "cycle-rerun", "sequence": 2},
                        "report": {
                            "status": "completed",
                            "summary": "Accepted the auto-answered path.",
                            "artifacts": [],
                            "next_hint": "execution",
                        },
                    },
                ]
            )

            steps = scheduler._run_agent_until_stable("design", max_attempts=2)

            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0]["cycle_id"], "cycle-rerun")
            self.assertEqual(steps[1]["cycle_id"], "cycle-rerun")
            self.assertEqual(scheduler.state.extra["sequence"], 2)

            first_call_state = scheduler.runner.run_agent.call_args_list[0].kwargs["state"]
            second_call_state = scheduler.runner.run_agent.call_args_list[1].kwargs["state"]
            self.assertEqual(first_call_state.get("sequence"), 0)
            self.assertEqual(second_call_state.get("sequence"), 1)

    def test_maintenance_due_runs_cleanup_before_next_work_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, memory_root, scheduler = self._seed_runtime(temp_path)
            scheduler.state.active_agent = "design"
            scheduler.state.extra["last_cleanup_maintenance_at"] = "2026-03-25T00:00:00Z"
            scheduler.mission.extra["maintenance_findings"] = ["stale finding"]

            with patch("lib.scheduler._run_execution_subagent", return_value=_fake_execution_result()), patch(
                "lib.scheduler._run_verification_command",
                side_effect=_fake_verification_run,
            ):
                result = scheduler.run_until_stable(max_turns=1)

            self.assertEqual(len(result.steps), 1)
            self.assertEqual(result.steps[0]["agent"]["id"], "cleanup")
            self.assertEqual(result.steps[0]["report"]["cleanup_mode"], "maintenance")
            persisted_state = load_state(memory_root)
            persisted_mission = load_mission(memory_root)
            self.assertEqual(persisted_state.active_agent, "design")
            self.assertNotEqual(persisted_state.extra["last_cleanup_maintenance_at"], "2026-03-25T00:00:00Z")
            self.assertEqual(persisted_mission.extra["maintenance_findings"], [])

    def test_recovery_request_runs_cleanup_recovery_before_work_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, memory_root, scheduler = self._seed_runtime(temp_path)
            scheduler.state.active_agent = "design"
            scheduler.state.extra["recovery_requested"] = True
            scheduler.state.extra["cycle_id"] = "cycle-stale"
            scheduler.state.extra["sequence"] = 3

            result = scheduler.run_until_stable(max_turns=1)

            self.assertEqual(len(result.steps), 1)
            self.assertEqual(result.steps[0]["agent"]["id"], "cleanup")
            self.assertEqual(result.steps[0]["report"]["cleanup_mode"], "recovery")
            persisted_state = load_state(memory_root)
            self.assertEqual(persisted_state.active_agent, "design")
            self.assertNotIn("recovery_requested", persisted_state.extra)
            self.assertNotIn("cycle_id", persisted_state.extra)
            self.assertNotIn("sequence", persisted_state.extra)

    def test_completed_runtime_can_run_maintenance_without_reopening_work_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, memory_root, scheduler = self._seed_runtime(temp_path)
            scheduler.state.active_agent = ""
            scheduler.state.extra["status"] = "completed"
            scheduler.mission.status = "completed"
            scheduler.state.extra["last_cleanup_maintenance_at"] = "2026-03-25T00:00:00Z"

            result = scheduler.run_until_stable(max_turns=2)

            self.assertEqual(result.status, "completed")
            self.assertEqual(len(result.steps), 1)
            self.assertEqual(result.steps[0]["agent"]["id"], "cleanup")
            self.assertEqual(result.steps[0]["report"]["cleanup_mode"], "maintenance")
            persisted_state = load_state(memory_root)
            self.assertEqual(persisted_state.active_agent, "")
            self.assertEqual(persisted_state.extra["status"], "completed")

    def test_waiting_human_with_missing_gate_runs_recovery_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, memory_root, scheduler = self._seed_runtime(temp_path)
            scheduler.state.active_agent = ""
            scheduler.state.extra["status"] = "waiting_human"
            scheduler.mission.status = "waiting_human"
            scheduler.state.extra["pending_gate_id"] = "gate-missing"
            scheduler.state.extra["blocked_agent"] = "design"
            scheduler.state.extra["resume_agent"] = "design"

            result = scheduler.run_until_stable(max_turns=1)

            self.assertEqual(result.status, "running")
            self.assertEqual(len(result.steps), 1)
            self.assertEqual(result.steps[0]["agent"]["id"], "cleanup")
            self.assertEqual(result.steps[0]["report"]["cleanup_mode"], "recovery")
            persisted_state = load_state(memory_root)
            self.assertEqual(persisted_state.active_agent, "design")
            self.assertEqual(persisted_state.extra["status"], "running")
            self.assertEqual(persisted_state.extra.get("pending_gate_id", ""), "")


if __name__ == "__main__":
    unittest.main()

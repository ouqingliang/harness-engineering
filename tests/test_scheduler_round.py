from __future__ import annotations

import json
from pathlib import Path
import subprocess
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
from lib.scheduler_components.audit import run_saved_audit_request
from lib.scheduler_components.design import run_saved_design_request
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
    elif agent_id in {"verification", "audit"}:
        run_saved_audit_request(**common_kwargs)
    else:
        raise AssertionError(f"unexpected background agent: {agent_id}")
    return {
        "ok": True,
        "pid": 1234,
        "command": ["python", "codex_agent_launcher.py", "--agent-id", agent_id],
        "started_at": utc_now(),
    }


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


class SchedulerRoundTests(unittest.TestCase):
    def _seed_runtime(self, temp_dir: Path) -> tuple[Path, Path, HarnessScheduler]:
        doc_root = temp_dir / "docs"
        doc_root.mkdir()
        (doc_root / "README.md").write_text("# Demo Project\n\nThis is the primary project doc.\n", encoding="utf-8")
        _init_git_repo(temp_dir)

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

            with patch(
                "lib.scheduler._launch_execution_subagent",
                side_effect=_launch_execution_immediately,
            ), patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ):
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
            with patch(
                "lib.scheduler._launch_execution_subagent",
                return_value={"ok": True, "pid": 4567, "command": ["python"], "started_at": utc_now()},
            ), patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ):
                second_pass = resumed_scheduler.run_until_stable(max_turns=1)
            self.assertEqual(len(second_pass.steps), 1)

            second_step = second_pass.steps[0]
            self.assertEqual(second_step["cycle_id"], first_cycle_id)
            self.assertEqual(second_step["state_after"]["sequence"], 2)

            resumed_state = load_state(memory_root)
            if second_pass.status == "completed" or "cycle_id" not in resumed_state.extra:
                self.assertNotIn("cycle_id", resumed_state.extra)
                self.assertNotIn("sequence", resumed_state.extra)
            else:
                self.assertEqual(resumed_state.extra["cycle_id"], first_cycle_id)
                self.assertEqual(resumed_state.extra["sequence"], 2)

    def test_cleanup_round_close_clears_turn_identity_and_keeps_mission_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, memory_root, scheduler = self._seed_runtime(temp_path)

            with patch("lib.scheduler._launch_execution_subagent", side_effect=_launch_execution_immediately), patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ), patch(
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
                        "handoff_path": str(scheduler.paths.briefs_dir / "cycle-rerun-00-design.json"),
                        "report_path": str(scheduler.paths.briefs_dir / "cycle-rerun-00-design.json"),
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
                        "handoff_path": str(scheduler.paths.briefs_dir / "cycle-rerun-01-design.json"),
                        "report_path": str(scheduler.paths.briefs_dir / "cycle-rerun-01-design.json"),
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

    def test_agent_status_snapshot_does_not_duplicate_worktree_slice_or_brief_in_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, _, scheduler = self._seed_runtime(temp_path)

            scheduler._upsert_running_agent(
                {
                    "agent_id": "design",
                    "slice_key": "plans/demo.md::phase 1",
                    "phase_title": "design",
                    "status": "running",
                    "worktree_path": "C:/tmp/design-worktree",
                    "brief": "primary_doc=plans/demo.md",
                }
            )

            snapshot = scheduler.snapshot()
            design_status = next(item for item in snapshot["agent_statuses"] if item["id"] == "design")

            self.assertEqual(design_status["worktree"], "C:/tmp/design-worktree")
            self.assertEqual(design_status["current_slice"], "design")
            self.assertEqual(design_status["current_brief"], "primary_doc=plans/demo.md")
            self.assertNotIn("worktree=C:/tmp/design-worktree", design_status["details"])
            self.assertNotIn("slice=design", design_status["details"])
            self.assertNotIn("brief=primary_doc=plans/demo.md", design_status["details"])

    def test_dead_background_design_run_fails_instead_of_hanging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, memory_root, scheduler = self._seed_runtime(temp_path)
            stale_slice_key = f"design::{scheduler._selected_primary_doc() or 'README.md'}"
            launcher_dir = scheduler.paths.artifacts_dir / "launchers" / "design"
            launcher_dir.mkdir(parents=True, exist_ok=True)
            launcher_state_path = launcher_dir / "state.json"
            launcher_state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "agent_id": "design",
                        "active_run_id": "cycle-stale-00",
                        "pid": 999999,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            request_path = scheduler.paths.artifacts_dir / "cycle-stale" / "00-design-request.json"
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text("{}\n", encoding="utf-8")
            worktree_path = scheduler.paths.worktrees_dir / "design-stale"
            worktree_path.mkdir(parents=True, exist_ok=True)

            scheduler._upsert_running_agent(
                {
                    "agent_id": "design",
                    "slice_key": stale_slice_key,
                    "phase_title": "design",
                    "status": "running",
                    "request_path": str(request_path),
                    "result_path": str(request_path.with_name("00-design-result.json")),
                    "launcher_state_path": str(launcher_state_path),
                    "launcher_run_path": str(launcher_dir / "runs" / "cycle-stale-00.json"),
                    "project_root": str(temp_path),
                    "worktree_path": str(worktree_path),
                    "brief": "primary_doc=plans/demo.md",
                }
            )
            scheduler.state.active_agent = "design"

            with patch("lib.scheduler_components.background_runtime._pid_is_alive", return_value=False):
                result = scheduler.run_until_stable(max_turns=1)

            self.assertEqual(result.status, "failed")
            self.assertEqual(scheduler.mission.status, "failed")
            self.assertEqual(load_state(memory_root).extra["status"], "failed")
            self.assertIsNone(scheduler._current_running_agent("design"))

    def test_maintenance_due_runs_cleanup_before_next_work_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, memory_root, scheduler = self._seed_runtime(temp_path)
            scheduler.state.active_agent = "design"
            scheduler.state.extra["last_cleanup_maintenance_at"] = "2026-03-25T00:00:00Z"
            scheduler.mission.extra["maintenance_findings"] = ["stale finding"]

            with patch("lib.scheduler._launch_execution_subagent", side_effect=_launch_execution_immediately), patch(
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

            with patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ):
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

            with patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ):
                result = scheduler.run_until_stable(max_turns=1)

            self.assertEqual(result.status, "running")
            self.assertEqual(len(result.steps), 1)
            self.assertEqual(result.steps[0]["agent"]["id"], "cleanup")
            self.assertEqual(result.steps[0]["report"]["cleanup_mode"], "recovery")
            persisted_state = load_state(memory_root)
            self.assertEqual(persisted_state.active_agent, "design")
            self.assertEqual(persisted_state.extra["status"], "running")
            self.assertEqual(persisted_state.extra.get("pending_gate_id", ""), "")

    def test_route_to_decision_publishes_worker_blocked_before_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _, _, scheduler = self._seed_runtime(temp_path)

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
            self.assertEqual(scheduler.decision_agent_id, "decision")
            self.assertEqual(scheduler.state.extra["communication_brief"]["blocked_agent"], "design")
            self.assertEqual(scheduler.state.extra["communication_brief"]["decision_id"], "q-gate-001")

    def test_design_prefetches_the_next_slice_into_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            doc_root = temp_path / "docs"
            doc_root.mkdir()
            (doc_root / "plan.md").write_text(
                "\n".join(
                    [
                        "# Demo Plan",
                        "",
                        "### Phase 1: First slice",
                        "Goals",
                        "- do the first thing",
                        "File Targets",
                        "- src/demo/one.py",
                        "Done Criteria",
                        "- first is done",
                        "",
                        "### Phase 2: Second slice",
                        "Goals",
                        "- do the second thing",
                        "File Targets",
                        "- src/demo/two.py",
                        "Done Criteria",
                        "- second is done",
                    ]
                ),
                encoding="utf-8",
            )
            _init_git_repo(temp_path)
            memory_root = temp_path / "memory"
            config = HarnessConfig.from_mapping(
                {"memory_root": str(memory_root), "doc_root": str(doc_root), "goal": "prefetch next slice"}
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

            with patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ):
                result = scheduler.run_until_stable(max_turns=1)

            self.assertEqual(result.steps[0]["agent"]["id"], "design")
            current_design_artifact = Path(result.steps[0]["report"]["artifacts"][-1])
            current_design_payload = json.loads(current_design_artifact.read_text(encoding="utf-8"))
            queue = scheduler.mission.extra.get("planned_slice_queue", [])
            self.assertEqual(len(queue), 1)
            self.assertNotEqual(
                queue[0]["selected_phase"]["title"],
                current_design_payload["selected_phase"]["title"],
            )

    def test_background_execution_allows_design_prefetch_before_execution_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            doc_root = temp_path / "docs"
            doc_root.mkdir()
            (doc_root / "plan.md").write_text(
                "\n".join(
                    [
                        "# Demo Plan",
                        "",
                        "### Phase 1: First slice",
                        "Goals",
                        "- do the first thing",
                        "File Targets",
                        "- src/demo/one.py",
                        "Done Criteria",
                        "- first is done",
                        "",
                        "### Phase 2: Second slice",
                        "Goals",
                        "- do the second thing",
                        "File Targets",
                        "- src/demo/two.py",
                        "Done Criteria",
                        "- second is done",
                    ]
                ),
                encoding="utf-8",
            )
            _init_git_repo(temp_path)
            memory_root = temp_path / "memory"
            config = HarnessConfig.from_mapping(
                {"memory_root": str(memory_root), "doc_root": str(doc_root), "goal": "prefetch while execution runs"}
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

            with patch(
                "lib.scheduler._launch_execution_subagent",
                return_value={"ok": True, "pid": 4567, "command": ["python"], "started_at": utc_now()},
            ), patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ):
                result = scheduler.run_until_stable(max_turns=3)

            self.assertEqual(result.status, "running")
            self.assertEqual([step["agent"]["id"] for step in result.steps], ["design", "execution"])
            self.assertEqual(len(scheduler._running_execution_runs()), 1)
            self.assertEqual(len(scheduler.mission.extra.get("planned_slice_queue", [])), 1)


if __name__ == "__main__":
    unittest.main()

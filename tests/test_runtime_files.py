from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

from lib.handoff import Handoff, handoff_path, read_handoff, save_handoff
from lib.question_router import Answer, Question, answer_path, read_answer, read_question, save_answer, save_question
from lib.report import Report, read_report, report_path, save_report
from lib.runtime_state import (
    HarnessConfig,
    Mission,
    RuntimeState,
    ensure_runtime_layout,
    ensure_runtime_root,
    load_mission,
    load_or_build_mission,
    load_or_init_state,
    load_state,
    save_mission,
    save_state,
)
from lib.scheduler_components.background_runtime import HARNESS_ROOT, launch_background_agent, load_launcher_status


class RuntimeFileTests(unittest.TestCase):
    def test_layout_and_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            paths = ensure_runtime_root(memory_root)
            self.assertTrue(paths.harness_root.exists())
            self.assertTrue(paths.handoffs_dir.exists())
            self.assertTrue(paths.reports_dir.exists())
            self.assertTrue(paths.questions_dir.exists())
            self.assertTrue(paths.answers_dir.exists())
            self.assertTrue(paths.artifacts_dir.exists())
            self.assertTrue(paths.locks_dir.exists())
            self.assertTrue(paths.launchers_dir.exists())
            self.assertTrue(paths.worktrees_dir.exists())

            mission = Mission(doc_root="C:/docs/项目主目录", goal="一键运行 harness engineering", status="active", round=3, extra={"note": "中文内容"})
            state = RuntimeState(active_agent="execution-agent", last_successful_agent="design-agent", retry_count=2, last_run_at="2026-03-25T12:34:56Z", current_round=3, extra={"comment": "稳定"})
            save_mission(memory_root, mission)
            save_state(memory_root, state)

            fresh_root = Path(temp_dir) / "fresh-memory"
            self.assertEqual(load_or_build_mission(fresh_root, "C:/docs/项目主目录").doc_root, "C:/docs/项目主目录")
            self.assertEqual(load_or_init_state(fresh_root).current_round, 0)

            config = HarnessConfig.from_mapping(
                {
                    "memory_root": "runtime-memory",
                    "doc_root": "C:/docs/项目主目录",
                    "goal": "一键运行 harness engineering",
                    "sleep_seconds": "2.5",
                    "decision_gate_tags": ["architecture_change", "goal_conflict"],
                    "default_launcher": "codex_app_server",
                    "note": "中文",
                }
            )
            self.assertEqual(config.sleep_seconds, 2.5)
            self.assertEqual(config.decision_gate_tags, ("architecture_change", "goal_conflict"))
            self.assertEqual(config.extra["note"], "中文")

            loaded_mission = load_mission(memory_root)
            loaded_state = load_state(memory_root)
            self.assertEqual(loaded_mission.goal, mission.goal)
            self.assertEqual(loaded_state.active_agent, state.active_agent)

            handoff = Handoff(
                from_agent="design-agent",
                to_agent="execution-agent",
                goal="实现主线 runtime primitives",
                inputs={"candidate_paths": ["lib/runtime_state.py", "lib/handoff.py"]},
                done_when="helpers can round-trip JSON",
                extra={"hint": "keep UTF-8"},
            )
            report = Report(
                agent="execution-agent",
                status="done",
                summary="完成 UTF-8 读写",
                artifacts=["tests/test_runtime_files.py"],
                next_hint="继续接 runner",
                extra={"evidence": "passed"},
            )
            question = Question(
                question_id="q-001",
                agent="execution-agent",
                question="应该优先使用哪个路径？",
                blocking=False,
                importance="low",
                tags=["path"],
                context={"candidate_paths": ["lib/runtime_state.py"]},
                extra={"note": "ordinary"},
            )
            answer = Answer(question_id="q-001", answer="lib/runtime_state.py", source="supervisor:auto", extra={"reason": "candidate path exists"})

            handoff_file = save_handoff(memory_root, "design-to-execution", handoff)
            report_file = save_report(memory_root, "execution", report)
            question_file = save_question(memory_root, "q-001", question)
            answer_file = save_answer(memory_root, "q-001", answer)

            self.assertEqual(read_handoff(handoff_file).goal, handoff.goal)
            self.assertEqual(read_report(report_file).summary, report.summary)
            self.assertEqual(read_question(question_file).question, question.question)
            self.assertEqual(read_answer(answer_file).answer, answer.answer)
            self.assertEqual(handoff_path(memory_root, "design-to-execution").name, "design-to-execution.json")
            self.assertEqual(report_path(memory_root, "execution").name, "execution.json")
            self.assertEqual(answer_path(memory_root, "q-001").name, "q-001.json")

    def test_dead_running_launcher_is_marked_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher_state_path = Path(temp_dir) / "state.json"
            launcher_state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "agent_id": "design",
                        "active_run_id": "cycle-001-00",
                        "pid": 43210,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("lib.scheduler_components.background_runtime._pid_is_alive", return_value=False):
                status = load_launcher_status(launcher_state_path)

            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["active_run_id"], "")
            self.assertEqual(status["last_exit_code"], -1)
            self.assertIn("stale_reason", status)

    def test_parent_launch_does_not_overwrite_child_completed_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            launcher_state_path = root / "launcher" / "state.json"
            launcher_run_path = root / "launcher" / "runs" / "cycle-001-00.json"
            request_path = root / "artifacts" / "cycle-001" / "00-design-request.json"
            result_path = root / "artifacts" / "cycle-001" / "00-design-result.json"
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text("{}\n", encoding="utf-8")
            started_at = "2026-03-27T00:00:00Z"

            class _FakeProcess:
                pid = 24680

            def _spawn_and_complete(*args: object, **kwargs: object) -> _FakeProcess:
                launcher_state_path.parent.mkdir(parents=True, exist_ok=True)
                launcher_state_path.write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "agent_id": "design",
                            "active_run_id": "",
                            "last_request_path": str(request_path),
                            "last_result_path": str(result_path),
                            "last_exit_code": 0,
                            "completed_at": "2026-03-27T00:00:01Z",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return _FakeProcess()

            with patch("lib.scheduler_components.background_runtime.subprocess.Popen", side_effect=_spawn_and_complete):
                launch_result = launch_background_agent(
                    agent_id="design",
                    workspace_root=workspace_root,
                    request_path=request_path,
                    result_path=result_path,
                    launcher_state_path=launcher_state_path,
                    launcher_run_path=launcher_run_path,
                    started_at=started_at,
                )

            self.assertTrue(launch_result["ok"])
            status = json.loads(launcher_state_path.read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["last_exit_code"], 0)
            self.assertEqual(status["last_request_path"], str(request_path))
            self.assertEqual(status["last_result_path"], str(result_path))

    def test_background_design_launch_writes_result_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            doc_root = root / "docs"
            doc_root.mkdir(parents=True, exist_ok=True)
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
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            request_path = root / "artifacts" / "cycle-001" / "00-design-request.json"
            result_path = root / "artifacts" / "cycle-001" / "00-design-result.json"
            launcher_state_path = root / "launchers" / "design" / "state.json"
            launcher_run_path = root / "launchers" / "design" / "runs" / "cycle-001-00.json"
            request_payload = {
                "doc_bundle": {
                    "doc_count": 1,
                    "doc_digest": "demo",
                    "doc_root": str(doc_root),
                    "docs": [
                        {
                            "relative_path": "plan.md",
                            "title": "Demo Plan",
                            "excerpt": "Phase 1",
                            "sha256": "demo",
                            "size_bytes": 1,
                        }
                    ],
                    "gate_signals": [],
                    "primary_docs": [
                        {
                            "relative_path": "plan.md",
                            "title": "Demo Plan",
                            "excerpt": "Phase 1",
                            "sha256": "demo",
                            "size_bytes": 1,
                        }
                    ],
                    "summary": "Demo Plan",
                },
                "doc_root": str(doc_root),
                "project_root": str(root),
                "selected_primary_doc": "plan.md",
                "completed_slices": [],
                "maintenance_findings": [],
                "pending_supervisor_decision": {},
                "planned_slice_queue": [],
                "assigned_worktree": str(workspace_root),
                "recorded_at": "2026-03-27T00:00:00Z",
            }
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(json.dumps(request_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            launch_result = launch_background_agent(
                agent_id="design",
                workspace_root=workspace_root,
                request_path=request_path,
                result_path=result_path,
                launcher_state_path=launcher_state_path,
                launcher_run_path=launcher_run_path,
                started_at="2026-03-27T00:00:00Z",
            )

            self.assertTrue(launch_result["ok"])
            deadline = time.time() + 5
            while time.time() < deadline and not result_path.exists():
                time.sleep(0.1)

            self.assertTrue(
                result_path.exists(),
                f"expected background design result from {(HARNESS_ROOT / 'runners' / 'codex_agent_launcher.py')}",
            )
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["design_status"], "ready")


if __name__ == "__main__":
    unittest.main()

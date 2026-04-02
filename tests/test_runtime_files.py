from __future__ import annotations

import json
import subprocess
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
    append_event_row,
    brief_record_path,
    ensure_runtime_layout,
    ensure_runtime_root,
    event_log_path,
    gate_record_path,
    inbox_message_path,
    load_mission,
    load_jsonl_rows,
    load_or_build_mission,
    load_or_init_state,
    load_state,
    read_brief_record,
    read_gate_record,
    read_inbox_message,
    read_session_metadata,
    save_mission,
    save_state,
    session_metadata_path,
    supervisor_inbox_event_log_path,
    utc_now,
    write_brief_record,
    write_gate_record,
    write_inbox_message,
    write_session_metadata,
)
from lib.scheduler_components.background_runtime import (
    HARNESS_ROOT,
    _pid_matches_launcher,
    launch_background_agent,
    load_launcher_status,
    save_launcher_state,
)
from lib.scheduler_components.execution import DEFAULT_EXECUTION_OUTPUT, _run_execution_subagent_from_saved_request


class RuntimeFileTests(unittest.TestCase):
    @unittest.skip("legacy runtime path expectations replaced by WS-01 Task 1 frozen substrate tests")
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

    def test_runtime_layout_round_trips_mission_and_state_with_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            paths = ensure_runtime_root(memory_root)
            self.assertTrue(paths.harness_root.exists())
            self.assertTrue(paths.events_dir.exists())
            self.assertTrue(paths.sessions_dir.exists())
            self.assertTrue(paths.inbox_dir.exists())
            self.assertTrue(paths.artifacts_dir.exists())
            self.assertTrue((paths.artifacts_dir / "launchers").exists())
            self.assertTrue(paths.gates_dir.exists())
            self.assertTrue(paths.briefs_dir.exists())
            self.assertTrue(paths.worktrees_dir.exists())
            for removed_name in ("handoffs", "reports", "questions", "answers", "locks", "launchers"):
                self.assertFalse((paths.harness_root / removed_name).exists())

            mission = Mission(
                doc_root="C:/docs/项目主目录",
                goal="一键运行 harness engineering",
                status="active",
                round=3,
                extra={"note": "中文内容"},
            )
            state = RuntimeState(
                active_agent="execution-agent",
                last_successful_agent="design-agent",
                retry_count=2,
                last_run_at="2026-03-25T12:34:56Z",
                current_round=3,
                extra={"comment": "稳定"},
            )
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
            self.assertEqual(loaded_mission.extra["note"], "中文内容")
            self.assertEqual(loaded_state.active_agent, state.active_agent)
            self.assertEqual(loaded_state.extra["comment"], "稳定")

    def test_utf8_runtime_substrate_helpers_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            paths = ensure_runtime_layout(memory_root)

            session_path = session_metadata_path(memory_root, "session-001")
            session_payload = {
                "session_id": "session-001",
                "agent": "execution-agent",
                "summary": "继续处理 UTF-8 会话",
            }
            write_session_metadata(session_path, session_payload)
            self.assertEqual(read_session_metadata(session_path), session_payload)
            self.assertIn("继续处理 UTF-8 会话", session_path.read_text(encoding="utf-8"))

            inbox_path = inbox_message_path(memory_root, "message-001")
            inbox_payload = {
                "message_id": "message-001",
                "sender": "supervisor",
                "body": "请继续处理下一步。",
            }
            write_inbox_message(inbox_path, inbox_payload)
            self.assertEqual(read_inbox_message(inbox_path), inbox_payload)
            self.assertIn("请继续处理下一步。", inbox_path.read_text(encoding="utf-8"))

            gate_path = gate_record_path(memory_root, "gate-001")
            gate_payload = {"gate_id": "gate-001", "status": "open", "title": "需要人工确认"}
            write_gate_record(gate_path, gate_payload)
            self.assertEqual(read_gate_record(gate_path), gate_payload)

            brief_path = brief_record_path(memory_root, "brief-001")
            brief_payload = {"brief_id": "brief-001", "agent": "design-agent", "summary": "交付新的简报内容"}
            write_brief_record(brief_path, brief_payload)
            self.assertEqual(read_brief_record(brief_path), brief_payload)

            event_path = event_log_path(memory_root, "session-001")
            event_rows = [
                {"event": "session.started", "summary": "会话已启动"},
                {"event": "session.updated", "summary": "继续处理第二步"},
            ]
            for row in event_rows:
                append_event_row(event_path, row)
            self.assertEqual(load_jsonl_rows(event_path), event_rows)
            supervisor_event_path = supervisor_inbox_event_log_path(memory_root)
            supervisor_event_rows = [
                {"event": "communication.message_recorded", "summary": "?? human ?????"},
                {"event": "communication.gate_replied", "summary": "??????? supervisor inbox"},
            ]
            for row in supervisor_event_rows:
                append_event_row(supervisor_event_path, row)
            self.assertEqual(load_jsonl_rows(supervisor_event_path), supervisor_event_rows)
            self.assertIn("?? human ?????", supervisor_event_path.read_text(encoding="utf-8"))

            self.assertIn("会话已启动", event_path.read_text(encoding="utf-8"))

            self.assertEqual(session_path.parent, paths.sessions_dir)
            self.assertEqual(inbox_path.parent, paths.inbox_dir)
            self.assertEqual(gate_path.parent, paths.gates_dir)
            self.assertEqual(brief_path.parent, paths.briefs_dir)
            self.assertEqual(event_path.parent, paths.events_dir)
            self.assertEqual(supervisor_event_path.parent, paths.events_dir)

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

    def test_running_launcher_without_pid_and_without_run_or_result_is_marked_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            launcher_dir = root / "launchers" / "execution"
            launcher_dir.mkdir(parents=True, exist_ok=True)
            launcher_state_path = launcher_dir / "state.json"
            request_path = root / "artifacts" / "cycle-001" / "00-execution-request.json"
            result_path = root / "artifacts" / "cycle-001" / "00-execution-result.json"
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text("{}\n", encoding="utf-8")
            launcher_state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "agent_id": "execution",
                        "active_run_id": "cycle-001-00",
                        "last_request_path": str(request_path),
                        "last_result_path": str(result_path),
                        "started_at": "2026-03-27T00:00:00Z",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            status = load_launcher_status(launcher_state_path)

            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["active_run_id"], "")
            self.assertEqual(status["last_exit_code"], -1)
            self.assertIn("stale_reason", status)
            self.assertIn("missing", status["stale_reason"])

    def test_running_launcher_with_reused_pid_is_marked_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher_state_path = Path(temp_dir) / "state.json"
            launcher_state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "agent_id": "execution",
                        "active_run_id": "cycle-001-00",
                        "pid": 26688,
                        "pid_executable": "python.exe",
                        "started_at": "2026-03-27T00:00:00Z",
                        "pid_mismatch_detected_at": "2026-03-27T00:00:00Z",
                        "last_request_path": str(Path(temp_dir) / "request.json"),
                        "last_result_path": str(Path(temp_dir) / "result.json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("lib.scheduler_components.background_runtime._pid_is_alive", return_value=True), patch(
                "lib.scheduler_components.background_runtime._pid_matches_launcher",
                return_value=False,
            ):
                status = load_launcher_status(launcher_state_path)

            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["last_exit_code"], -1)
            self.assertIn("different process", status["stale_reason"])

    def test_recent_pid_mismatch_stays_running_during_grace_period(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher_state_path = Path(temp_dir) / "state.json"
            launcher_state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "agent_id": "execution",
                        "active_run_id": "cycle-001-00",
                        "pid": 26688,
                        "pid_executable": "python.exe",
                        "started_at": "2026-03-27T00:00:00Z",
                        "heartbeat_at": utc_now(),
                        "last_request_path": str(Path(temp_dir) / "request.json"),
                        "last_result_path": str(Path(temp_dir) / "result.json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("lib.scheduler_components.background_runtime._pid_is_alive", return_value=True), patch(
                "lib.scheduler_components.background_runtime._pid_matches_launcher",
                return_value=False,
            ):
                status = load_launcher_status(launcher_state_path)

            self.assertEqual(status["status"], "running")

    def test_first_pid_mismatch_only_records_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher_state_path = Path(temp_dir) / "state.json"
            launcher_state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "agent_id": "execution",
                        "active_run_id": "cycle-001-00",
                        "pid": 26688,
                        "pid_executable": "python.exe",
                        "started_at": "2026-03-27T00:00:00Z",
                        "last_request_path": str(Path(temp_dir) / "request.json"),
                        "last_result_path": str(Path(temp_dir) / "result.json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("lib.scheduler_components.background_runtime._pid_is_alive", return_value=True), patch(
                "lib.scheduler_components.background_runtime._pid_matches_launcher",
                return_value=False,
            ):
                status = load_launcher_status(launcher_state_path)

            self.assertEqual(status["status"], "running")
            self.assertTrue(status.get("pid_mismatch_detected_at"))

    def test_running_update_preserves_recent_heartbeat_for_same_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            launcher_state_path = root / "state.json"
            request_path = root / "request.json"
            result_path = root / "result.json"
            heartbeat_at = utc_now()
            launcher_state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "agent_id": "execution",
                        "active_run_id": "cycle-001-00",
                        "last_request_path": str(request_path),
                        "last_result_path": str(result_path),
                        "heartbeat_at": heartbeat_at,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            updated = save_launcher_state(
                launcher_state_path=launcher_state_path,
                request_path=request_path,
                result_path=result_path,
                payload={
                    "status": "running",
                    "agent_id": "execution",
                    "last_request_path": str(request_path),
                    "last_result_path": str(result_path),
                },
            )

            self.assertEqual(updated.get("heartbeat_at"), heartbeat_at)

    def test_save_launcher_state_mirrors_public_substrate_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir)
            paths = ensure_runtime_layout(memory_root)
            request_path = paths.artifacts_dir / "cycle-001" / "00-execution-request.json"
            result_path = paths.artifacts_dir / "cycle-001" / "00-execution-result.json"
            launcher_state_path = memory_root / ".launcher-private" / "execution" / "state.json"
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(
                json.dumps(
                    {
                        "goal": "Mirror launcher state into the frozen substrate",
                        "assigned_worktree": "C:/worktrees/execution",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            result_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "summary": "执行结果已写入共享 substrate",
                        "gate_id": "gate-001",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            updated = save_launcher_state(
                launcher_state_path=launcher_state_path,
                request_path=request_path,
                result_path=result_path,
                payload={
                    "status": "completed",
                    "agent_id": "execution",
                    "active_run_id": "cycle-001-00",
                    "last_request_path": str(request_path),
                    "last_result_path": str(result_path),
                    "completed_at": "2026-04-02T00:00:01Z",
                    "heartbeat_at": "2026-04-02T00:00:00Z",
                },
            )

            record_id = "launcher-execution-cycle-001-00"
            session_record = read_session_metadata(session_metadata_path(memory_root, record_id))
            inbox_record = read_inbox_message(inbox_message_path(memory_root, f"{record_id}-request"))
            brief_record = read_brief_record(brief_record_path(memory_root, record_id))
            gate_record = read_gate_record(gate_record_path(memory_root, "gate-001"))
            event_rows = load_jsonl_rows(event_log_path(memory_root, record_id))
            artifact_record = json.loads(
                (paths.artifacts_dir / "launchers" / f"{record_id}.json").read_text(encoding="utf-8")
            )

            self.assertEqual(updated["status"], "completed")
            self.assertEqual(session_record["agent_id"], "execution")
            self.assertEqual(session_record["status"], "completed")
            self.assertEqual(session_record["request_path"], str(request_path))
            self.assertEqual(inbox_record["assigned_worktree"], "C:/worktrees/execution")
            self.assertEqual(brief_record["summary"], "执行结果已写入共享 substrate")
            self.assertEqual(gate_record["gate_id"], "gate-001")
            self.assertEqual(event_rows[-1]["status"], "completed")
            self.assertEqual(artifact_record["result"]["summary"], "执行结果已写入共享 substrate")

    def test_pid_identity_match_beats_executable_name_difference(self) -> None:
        payload = {
            "pid": 26688,
            "pid_identity": "created-token-123",
            "pid_executable": "python.exe",
        }

        with patch(
            "lib.scheduler_components.background_runtime._process_identity_token",
            return_value="created-token-123",
        ), patch(
            "lib.scheduler_components.background_runtime._process_executable_path",
            return_value="python3.10.exe",
        ):
            self.assertTrue(_pid_matches_launcher(26688, payload))

    def test_recent_heartbeat_prevents_pid_dead_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher_state_path = Path(temp_dir) / "state.json"
            launcher_state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "agent_id": "execution",
                        "active_run_id": "cycle-001-00",
                        "pid": 26688,
                        "pid_executable": "python.exe",
                        "started_at": "2026-03-27T00:00:00Z",
                        "heartbeat_at": utc_now(),
                        "last_request_path": str(Path(temp_dir) / "request.json"),
                        "last_result_path": str(Path(temp_dir) / "result.json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("lib.scheduler_components.background_runtime._pid_is_alive", return_value=False):
                status = load_launcher_status(launcher_state_path)

            self.assertEqual(status["status"], "running")

    def test_saved_execution_request_can_resume_prior_codex_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            request_path = root / "artifacts" / "cycle-001" / "00-execution-request.json"
            result_path = root / "artifacts" / "cycle-001" / "00-execution-result.json"
            launcher_state_path = root / "launchers" / "execution" / "state.json"
            launcher_run_path = root / "launchers" / "execution" / "runs" / "cycle-001-00.json"
            schema_path = request_path.with_name("00-execution-request-schema.json")
            output_path = root / "artifacts" / "cycle-001" / "00-execution-result.message.json"
            session_id = "11111111-2222-4333-8444-555555555555"
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(
                json.dumps(
                    {
                        "workspace_root": str(workspace_root),
                        "prompt": "Continue after the last human reply.",
                        "codex_executable": "codex",
                        "schema_path": str(schema_path),
                        "output_path": str(output_path),
                        "recorded_at": "2026-03-27T00:00:00Z",
                        "resume_session_id": session_id,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            output_path.write_text(
                json.dumps(
                    {
                        "status": "implemented",
                        "summary": "Resumed the prior session.",
                        "changed_paths": [],
                        "verification_notes": [],
                        "needs_human": False,
                        "human_question": "",
                        "why_not_auto_answered": "",
                        "required_reply_shape": "",
                        "decision_tags": [],
                        "options": [],
                        "notes": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            completed = subprocess.CompletedProcess(
                args=["codex", "exec", "resume", session_id],
                returncode=0,
                stdout="",
                stderr=f"session id: {session_id}\n",
            )
            with patch(
                "lib.scheduler_components.execution.subprocess.run",
                return_value=completed,
            ) as run_mock, patch(
                "lib.scheduler_components.execution._git_status_snapshot",
                return_value={"entries": [], "ok": True},
            ):
                payload = _run_execution_subagent_from_saved_request(
                    request_path=request_path,
                    result_path=result_path,
                    launcher_state_path=launcher_state_path,
                    launcher_run_path=launcher_run_path,
                )

            command = next(
                call.args[0]
                for call in run_mock.call_args_list
                if call.args and isinstance(call.args[0], list) and call.args[0][:3] == ["codex", "exec", "resume"]
            )
            self.assertEqual(command[:4], ["codex", "exec", "resume", session_id])
            self.assertEqual(command[4], "Continue after the last human reply.")
            self.assertEqual(payload["session_id"], session_id)
            self.assertTrue(payload["ok"])

    def test_saved_execution_request_detects_session_requested_task_again_without_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            request_path = root / "artifacts" / "cycle-001" / "00-execution-request.json"
            result_path = root / "artifacts" / "cycle-001" / "00-execution-result.json"
            launcher_state_path = root / "launchers" / "execution" / "state.json"
            launcher_run_path = root / "launchers" / "execution" / "runs" / "cycle-001-00.json"
            schema_path = request_path.with_name("00-execution-request-schema.json")
            output_path = root / "artifacts" / "cycle-001" / "00-execution-result.message.json"
            session_id = "11111111-2222-4333-8444-555555555555"
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(
                json.dumps(
                    {
                        "workspace_root": str(workspace_root),
                        "prompt": "Implement the approved slice.",
                        "codex_executable": "codex",
                        "schema_path": str(schema_path),
                        "output_path": str(output_path),
                        "recorded_at": "2026-03-27T00:00:00Z",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            output_path.write_text(
                json.dumps(
                    {
                        "status": "unknown",
                        "summary": "",
                        "changed_paths": [],
                        "verification_notes": [],
                        "needs_human": False,
                        "human_question": "",
                        "why_not_auto_answered": "",
                        "required_reply_shape": "",
                        "decision_tags": [],
                        "options": [],
                        "notes": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=0,
                stdout="Send the specific task when you're ready.\n",
                stderr=f"session id: {session_id}\n",
            )

            with patch(
                "lib.scheduler_components.execution.subprocess.run",
                return_value=completed,
            ), patch(
                "lib.scheduler_components.execution._git_status_snapshot",
                return_value={"entries": [], "ok": True},
            ):
                payload = _run_execution_subagent_from_saved_request(
                    request_path=request_path,
                    result_path=result_path,
                    launcher_state_path=launcher_state_path,
                    launcher_run_path=launcher_run_path,
                )

            self.assertEqual(payload["session_id"], session_id)
            self.assertEqual(payload["session_state"], "requested_task_again")

    def test_saved_execution_request_detects_provide_task_readiness_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            request_path = root / "artifacts" / "cycle-001" / "00-execution-request.json"
            result_path = root / "artifacts" / "cycle-001" / "00-execution-result.json"
            launcher_state_path = root / "launchers" / "execution" / "state.json"
            launcher_run_path = root / "launchers" / "execution" / "runs" / "cycle-001-00.json"
            schema_path = request_path.with_name("00-execution-request-schema.json")
            output_path = root / "artifacts" / "cycle-001" / "00-execution-result.message.json"
            session_id = "11111111-2222-4333-8444-555555555555"
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(
                json.dumps(
                    {
                        "workspace_root": str(workspace_root),
                        "prompt": "Implement the approved slice.",
                        "codex_executable": "codex",
                        "schema_path": str(schema_path),
                        "output_path": str(output_path),
                        "recorded_at": "2026-03-27T00:00:00Z",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            output_path.write_text(
                json.dumps(DEFAULT_EXECUTION_OUTPUT, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=0,
                stdout="Provide the task, and I'll handle it directly.\n",
                stderr=f"session id: {session_id}\n",
            )

            with patch(
                "lib.scheduler_components.execution.subprocess.run",
                return_value=completed,
            ), patch(
                "lib.scheduler_components.execution._git_status_snapshot",
                return_value={"entries": [], "ok": True},
            ):
                payload = _run_execution_subagent_from_saved_request(
                    request_path=request_path,
                    result_path=result_path,
                    launcher_state_path=launcher_state_path,
                    launcher_run_path=launcher_run_path,
                )

            self.assertEqual(payload["session_id"], session_id)
            self.assertEqual(payload["session_state"], "requested_task_again")

    def test_saved_execution_request_detects_using_superpowers_bootstrap_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            request_path = root / "artifacts" / "cycle-001" / "00-execution-request.json"
            result_path = root / "artifacts" / "cycle-001" / "00-execution-result.json"
            launcher_state_path = root / "launchers" / "execution" / "state.json"
            launcher_run_path = root / "launchers" / "execution" / "runs" / "cycle-001-00.json"
            schema_path = request_path.with_name("00-execution-request-schema.json")
            output_path = root / "artifacts" / "cycle-001" / "00-execution-result.message.json"
            session_id = "11111111-2222-4333-8444-555555555555"
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(
                json.dumps(
                    {
                        "workspace_root": str(workspace_root),
                        "prompt": "Implement the approved slice.",
                        "codex_executable": "codex",
                        "schema_path": str(schema_path),
                        "output_path": str(output_path),
                        "recorded_at": "2026-03-27T00:00:00Z",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            output_path.write_text(
                json.dumps(DEFAULT_EXECUTION_OUTPUT, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=0,
                stdout="Using `using-superpowers` to align with the required skill workflow for this session.\n",
                stderr=f"session id: {session_id}\n",
            )

            with patch(
                "lib.scheduler_components.execution.subprocess.run",
                return_value=completed,
            ), patch(
                "lib.scheduler_components.execution._git_status_snapshot",
                return_value={"entries": [], "ok": True},
            ):
                payload = _run_execution_subagent_from_saved_request(
                    request_path=request_path,
                    result_path=result_path,
                    launcher_state_path=launcher_state_path,
                    launcher_run_path=launcher_run_path,
                )

            self.assertEqual(payload["session_id"], session_id)
            self.assertEqual(payload["session_state"], "requested_task_again")

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
            paths = ensure_runtime_layout(root)
            request_path = paths.artifacts_dir / "cycle-001" / "00-design-request.json"
            result_path = paths.artifacts_dir / "cycle-001" / "00-design-result.json"
            launcher_state_path = root / ".launcher-private" / "design" / "state.json"
            launcher_run_path = root / ".launcher-private" / "design" / "runs" / "cycle-001-00.json"
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
            session_record = read_session_metadata(session_metadata_path(root, "launcher-design-cycle-001-00"))
            inbox_record = read_inbox_message(inbox_message_path(root, "launcher-design-cycle-001-00-request"))
            brief_record = read_brief_record(brief_record_path(root, "launcher-design-cycle-001-00"))
            event_rows = load_jsonl_rows(event_log_path(root, "launcher-design-cycle-001-00"))
            artifact_record_path = paths.artifacts_dir / "launchers" / "launcher-design-cycle-001-00.json"

            self.assertEqual(session_record["agent_id"], "design")
            self.assertEqual(session_record["request_path"], str(request_path))
            self.assertEqual(inbox_record["selected_primary_doc"], "plan.md")
            self.assertTrue(brief_record["summary"])
            self.assertTrue(event_rows)
            self.assertTrue(artifact_record_path.exists())


if __name__ == "__main__":
    unittest.main()

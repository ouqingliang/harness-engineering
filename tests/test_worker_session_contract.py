from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from lib.runner_bridge import run_agent
from lib.runtime_contract import (
    PUBLIC_CONTROL_ACTIONS,
    SESSION_CONTROL_FIELD,
    TASK_NOTIFICATION_FIELD,
    build_task_notification,
    coerce_session_control,
)
from lib.scheduler_components.execution import _prepare_execution_request, _run_execution_subagent_from_saved_request


class WorkerSessionContractTests(unittest.TestCase):
    def test_public_control_vocabulary_uses_spawn_continue_and_terminate(self) -> None:
        self.assertEqual(PUBLIC_CONTROL_ACTIONS, ("spawn", "continue", "terminate"))
        self.assertEqual(coerce_session_control({"action": "spawn"}), {"action": "spawn"})
        self.assertEqual(
            coerce_session_control({"action": "continue", "session": "session-001"}),
            {"action": "continue", "session": "session-001"},
        )
        self.assertEqual(
            coerce_session_control({"action": "terminate", "session": "session-001"}),
            {"action": "terminate", "session": "session-001"},
        )
        with self.assertRaises(ValueError):
            coerce_session_control({"action": "resume", "session": "session-001"})

    def test_task_notification_envelope_is_compact_and_utf8_clean(self) -> None:
        payload = build_task_notification(
            session="session-utf8",
            status="completed",
            summary="继续处理当前切片",
            result={"note": "输出保持 UTF-8 干净"},
            output_file="artifacts/执行结果.json",
        )

        self.assertEqual(
            payload,
            {
                "session": "session-utf8",
                "status": "completed",
                "summary": "继续处理当前切片",
                "result": {"note": "输出保持 UTF-8 干净"},
                "output-file": "artifacts/执行结果.json",
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "notification.json"
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.assertIn("继续处理当前切片", target.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), payload)

    def test_execution_request_uses_continue_contract_for_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request_payload = _prepare_execution_request(
                workspace_root=root / "workspace",
                canonical_project_root=root / "project",
                design_contract={"selected_phase": {"title": "Phase 2"}, "proposed_slice": "Implement Task 2"},
                baseline_docs=["memory/doc/plan.md"],
                planning_doc="memory/doc/phase.md",
                human_decisions=[],
                supervisor_brief={"resume_session_id": "11111111-2222-4333-8444-555555555555"},
                request_path=root / "artifacts" / "cycle-001" / "00-execution-request.json",
                result_path=root / "artifacts" / "cycle-001" / "00-execution-result.json",
            )

        self.assertEqual(
            request_payload[SESSION_CONTROL_FIELD],
            {"action": "continue", "session": "11111111-2222-4333-8444-555555555555"},
        )
        self.assertEqual(request_payload["resume_session_id"], "11111111-2222-4333-8444-555555555555")

    def test_runner_bridge_and_execution_emit_shared_contract_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / ".harness"
            runtime_paths = {
                "runtime_root": runtime_root,
                "handoff_dir": runtime_root / "handoffs",
                "report_dir": runtime_root / "reports",
                "launcher_dir": runtime_root / "launchers" / "codex_app_server",
                "state_file": runtime_root / "launchers" / "codex_app_server" / "state.json",
            }
            result = run_agent(
                {"id": "execution", "name": "Execution Agent"},
                {
                    "from": "supervisor",
                    "goal": "Continue the current session",
                    SESSION_CONTROL_FIELD: {"action": "continue", "session": "session-001"},
                },
                runtime_paths,
                {"goal": "Task 2"},
                {"runtime_root": str(runtime_root), "cycle_id": "cycle-001"},
                turn_executor=lambda turn: {
                    TASK_NOTIFICATION_FIELD: build_task_notification(
                        session="session-001",
                        status="completed",
                        summary="继续沿用同一会话",
                    )
                },
            )

            self.assertEqual(
                result["handoff"][SESSION_CONTROL_FIELD],
                {"action": "continue", "session": "session-001"},
            )
            self.assertEqual(
                result["report"][TASK_NOTIFICATION_FIELD],
                {
                    "session": "session-001",
                    "status": "completed",
                    "summary": "继续沿用同一会话",
                },
            )

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
                        "recorded_at": "2026-04-02T00:00:00Z",
                        "resume_session_id": session_id,
                        SESSION_CONTROL_FIELD: {"action": "continue", "session": session_id},
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
                        "summary": "继续处理当前切片",
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

            self.assertEqual(
                payload[TASK_NOTIFICATION_FIELD],
                {
                    "session": session_id,
                    "status": "terminal",
                    "summary": "继续处理当前切片",
                    "result": {
                        "status": "implemented",
                        "summary": "继续处理当前切片",
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
                    "output-file": str(output_path),
                },
            )


if __name__ == "__main__":
    unittest.main()

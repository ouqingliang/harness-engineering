from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.communication_api import CommunicationStore
from lib.runtime_state import HarnessConfig, RuntimeState, ensure_runtime_root, save_mission, save_state, utc_now
from lib.scheduler import HarnessScheduler
from lib.scheduler_components.audit import run_saved_audit_request
from lib.scheduler_components.design import run_saved_design_request
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


class HumanGateFlowTests(unittest.TestCase):
    def test_decision_gate_waits_and_resumes_without_a_communication_worker_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            doc_root = root / "docs"
            doc_root.mkdir()
            (doc_root / "README.md").write_text(
                "# Demo Project\n\n[decision-gate] Confirm the architecture change before continuing.\n",
                encoding="utf-8",
            )
            _init_git_repo(root)

            memory_root = root / "memory"
            config = HarnessConfig.from_mapping(
                {"memory_root": str(memory_root), "doc_root": str(doc_root), "goal": "wait and resume"}
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

            with patch("lib.scheduler._launch_execution_subagent", side_effect=_launch_execution_immediately), patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ), patch(
                "lib.scheduler._run_verification_command",
                side_effect=_fake_verification_run,
            ):
                first_pass = scheduler.run_until_stable(max_turns=12)

            self.assertEqual(first_pass.status, "waiting_human")
            self.assertTrue(first_pass.pending_gate_id)
            self.assertEqual(scheduler.state.active_agent, "")
            self.assertEqual(scheduler.state.extra["resume_agent"], "design")
            self.assertEqual(scheduler.state.extra["communication_brief"]["blocked_agent"], "design")

            store = CommunicationStore(paths.harness_root)
            raw_reply = "Continue the mainline implementation."
            resolved_gate = store.reply_to_gate(first_pass.pending_gate_id or "", sender="human", body=raw_reply)
            self.assertEqual(json.loads(Path(resolved_gate["answer_path"]).read_text(encoding="utf-8"))["answer"], raw_reply)

            with patch("lib.scheduler._launch_execution_subagent", side_effect=_launch_execution_immediately), patch(
                "lib.scheduler_components.turns.launch_background_agent",
                side_effect=_launch_background_immediately,
            ), patch(
                "lib.scheduler._run_verification_command",
                side_effect=_fake_verification_run,
            ):
                second_pass = scheduler.run_until_stable(max_turns=12)

            self.assertEqual(second_pass.status, "completed")
            self.assertEqual(second_pass.state.current_round, 1)
            self.assertTrue(scheduler.mission.extra.get("human_decisions"))
            self.assertNotIn("communication_brief", scheduler.state.extra)


if __name__ == "__main__":
    unittest.main()

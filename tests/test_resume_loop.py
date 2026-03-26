from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.communication_api import CommunicationStore
from lib.runtime_state import HarnessConfig, RuntimeState, ensure_runtime_root, save_mission, save_state, utc_now
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


class ResumeLoopTests(unittest.TestCase):
    def test_gate_waits_for_human_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            doc_root = root / "docs"
            doc_root.mkdir()
            (doc_root / "README.md").write_text(
                "# Demo Project\n\nOverall planning.\n\n[decision-gate] Confirm the architecture change before continuing.\n",
                encoding="utf-8",
            )

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

            first_pass = scheduler.run_until_stable(max_turns=12)
            self.assertEqual(first_pass.status, "waiting_human")
            self.assertTrue(first_pass.pending_gate_id)

            store = CommunicationStore(paths.harness_root)
            store.reply_to_gate(first_pass.pending_gate_id or "", sender="human", body="Continue the mainline implementation.")

            with patch("lib.scheduler._run_execution_subagent", return_value=_fake_execution_result()), patch(
                "lib.scheduler._run_verification_command",
                side_effect=_fake_verification_run,
            ):
                second_pass = scheduler.run_until_stable(max_turns=12)
            self.assertEqual(second_pass.status, "completed")
            self.assertEqual(second_pass.state.current_round, 1)
            reports = sorted(paths.reports_dir.glob("*.json"))
            self.assertEqual(len(reports), 8)
            report_names = [path.name for path in reports]
            self.assertEqual(len([name for name in report_names if name.endswith("-communication.json")]), 2)
            self.assertEqual(len([name for name in report_names if name.endswith("-design.json")]), 3)


if __name__ == "__main__":
    unittest.main()

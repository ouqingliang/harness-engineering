from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from lib.runtime_state import HarnessConfig, RuntimeState, ensure_runtime_root, save_mission, save_state, utc_now
from lib.scheduler import HarnessScheduler
from main import build_or_update_mission, load_all_specs, validate_specs


def _init_git_repo(project_root: Path) -> None:
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


class RoleMigrationTests(unittest.TestCase):
    def test_inspect_json_freezes_new_worker_topology_order(self) -> None:
        completed = subprocess.run(
            ["python", "main.py", "inspect", "--format", "json"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            [
                {
                    "id": "decision",
                    "name": "Decision Agent",
                    "order": 10,
                    "dependencies": [],
                    "title": "Triage blockers and normalize decisions",
                    "goal": "Classify ambiguous blockers, decide whether human judgment is required, and return a thin decision that the supervisor can route without turning the worker into a human communication lane.",
                },
                {
                    "id": "design",
                    "name": "Design Agent",
                    "order": 20,
                    "dependencies": ["decision"],
                    "title": "Design the next approved slice",
                    "goal": "Read the active mission and durable harness memory, define or update the architecture contract for the next implementation slice, and write artifacts that downstream execution can follow without guessing.",
                },
                {
                    "id": "execution",
                    "name": "Execution Agent",
                    "order": 30,
                    "dependencies": ["design"],
                    "title": "Implement the approved slice",
                    "goal": "Follow the current design contract, make the required code changes, run targeted verification, and leave execution artifacts that verification can validate.",
                },
                {
                    "id": "verification",
                    "name": "Verification Agent",
                    "order": 40,
                    "dependencies": ["execution"],
                    "title": "Verify the approved slice",
                    "goal": "Run read-only checks against the execution result, determine whether the approved slice passes, fails, or is incomplete, and report evidence back to the supervisor.",
                },
                {
                    "id": "cleanup",
                    "name": "Cleanup Agent",
                    "order": 50,
                    "dependencies": ["verification"],
                    "title": "Compress state and remove drift",
                    "goal": "After verification acceptance completes, or when a recovery or maintenance pass is requested, compress transient execution state into durable memory, remove stale artifacts and temporary debris, and leave the workspace easier for the next run to resume.",
                },
            ],
        )

    def test_scheduler_normalizes_legacy_role_ids_during_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            doc_root = root / "docs"
            doc_root.mkdir()
            (doc_root / "README.md").write_text("# Demo\n\nRole migration resume.\n", encoding="utf-8")
            _init_git_repo(root)

            memory_root = root / "memory"
            config = HarnessConfig.from_mapping(
                {"memory_root": str(memory_root), "doc_root": str(doc_root), "goal": "resume migrated roles"}
            )
            paths = ensure_runtime_root(memory_root)
            mission = build_or_update_mission(config, doc_root=doc_root)
            mission.extra["pending_supervisor_decision"] = {
                "decision_id": "decision-001",
                "choice": "replan",
                "constraints": ["Keep the blocker slice focused."],
            }
            state = RuntimeState(
                active_agent="audit",
                last_successful_agent="communication",
                retry_count=0,
                last_run_at=utc_now(),
                current_round=0,
                extra={
                    "status": "running",
                    "pending_supervisor_decision": {
                        "decision_id": "decision-001",
                        "choice": "replan",
                    },
                },
            )
            save_mission(paths.memory_root, mission)
            save_state(paths.memory_root, state)

            specs = load_all_specs()
            validate_specs(specs)
            scheduler = HarnessScheduler(specs=specs, paths=paths, mission=mission, state=state)

            self.assertEqual(scheduler.state.active_agent, "verification")
            self.assertEqual(scheduler.verification_agent_id, "verification")
            self.assertEqual(scheduler.decision_agent_id, "decision")
            self.assertNotIn("pending_supervisor_decision", scheduler.state.extra)
            self.assertIn("pending_supervisor_decision", scheduler.mission.extra)
            snapshot = scheduler.snapshot()
            self.assertEqual(
                [item["id"] for item in snapshot["agent_statuses"]],
                ["decision", "design", "execution", "verification", "cleanup"],
            )
            self.assertEqual([item["id"] for item in snapshot["running_agents"]], ["verification"])


if __name__ == "__main__":
    unittest.main()

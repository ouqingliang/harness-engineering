from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from lib.runtime_state import HarnessConfig, RuntimeState, ensure_runtime_root, save_mission, save_state, utc_now
from lib.scheduler import HarnessScheduler
from main import build_or_update_mission, load_all_specs, validate_specs


class QuestionRoutingConfigTests(unittest.TestCase):
    def test_configured_gate_tags_open_a_human_gate_through_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            doc_root = root / "docs"
            doc_root.mkdir()
            (doc_root / "README.md").write_text("# Demo\n\nMainline docs only.\n", encoding="utf-8")

            memory_root = root / "memory"
            config = HarnessConfig.from_mapping(
                {
                    "memory_root": str(memory_root),
                    "doc_root": str(doc_root),
                    "goal": "route configured gate tags",
                    "decision_gate_tags": ["release_gate"],
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

            route = scheduler._route_questions(
                "design",
                {
                    "questions": [
                        {
                            "question_id": "q-release-001",
                            "agent": "design",
                            "question": "Should we pause for the release gate?",
                            "blocking": False,
                            "importance": "low",
                            "tags": ["release_gate"],
                        }
                    ]
                },
            )

            self.assertEqual(route, "gate")
            self.assertEqual(scheduler.state.active_agent, "")
            self.assertEqual(scheduler.state.extra["blocked_agent"], "design")
            self.assertEqual(scheduler.state.extra["resume_agent"], "design")
            self.assertEqual(
                scheduler.state.extra["communication_brief"]["severity"],
                "release_gate",
            )


if __name__ == "__main__":
    unittest.main()

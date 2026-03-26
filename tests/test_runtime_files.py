from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()

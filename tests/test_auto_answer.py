from __future__ import annotations

import unittest

from lib.auto_answer import answer_question
from lib.question_router import Question, is_decision_gate, route_question


class AutoAnswerTests(unittest.TestCase):
    def test_explicit_gate_tag_uses_configured_tags(self) -> None:
        question = Question(
            question_id="gate-001",
            agent="execution-agent",
            question="Should we open the release gate now?",
            blocking=False,
            importance="low",
            tags=["release_gate"],
            context={"decision_gate_tags": ["release_gate"]},
        )
        self.assertTrue(is_decision_gate(question))
        self.assertEqual(route_question(question).route, "gate")
        self.assertIsNone(answer_question(question))

    def test_ordinary_blocker_stays_auto_answerable_without_explicit_gate(self) -> None:
        question = Question(
            question_id="auto-001",
            agent="execution-agent",
            question="Do we need an architecture change in this slice?",
            blocking=True,
            importance="high",
            tags=["path"],
            context={
                "candidate_paths": ["lib/runtime_state.py", "lib/handoff.py"],
                "decision_gate_tags": ["release_gate"],
            },
        )
        self.assertFalse(is_decision_gate(question))
        self.assertEqual(route_question(question).route, "auto_answer")
        answer = answer_question(question)
        self.assertIsNotNone(answer)
        self.assertEqual(answer.question_id, "auto-001")
        self.assertEqual(answer.answer, "lib/runtime_state.py")
        self.assertEqual(answer.source, "supervisor:auto")

    def test_explicit_marker_flag_opens_gate_without_tag_match(self) -> None:
        question = Question(
            question_id="gate-002",
            agent="verification-agent",
            question="Need human confirmation before continuing.",
            blocking=False,
            importance="low",
            tags=["path"],
            context={"marker": "decision-gate", "decision_gate_tags": ["release_gate"]},
        )
        self.assertTrue(is_decision_gate(question))
        self.assertEqual(route_question(question).route, "gate")
        self.assertIsNone(answer_question(question))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .runtime_state import (
    coerce_bool,
    coerce_str,
    ensure_runtime_layout,
    read_json_file,
    runtime_paths,
    split_known_fields,
    write_json_file,
)


DECISION_GATE_TAGS = frozenset(
    {
        "architecture_change",
        "destructive_action",
        "security_boundary",
        "external_side_effect",
        "goal_conflict",
    }
)


@dataclass(slots=True)
class Question:
    question_id: str
    agent: str
    question: str
    blocking: bool = False
    importance: str = "medium"
    tags: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Question":
        known, extra = split_known_fields(
            data,
            ("question_id", "agent", "question", "blocking", "importance", "tags", "context"),
        )
        raw_tags = known.get("tags", [])
        tags = [coerce_str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else [coerce_str(raw_tags)]
        raw_context = known.get("context", {})
        context = dict(raw_context) if isinstance(raw_context, Mapping) else {"value": raw_context}
        return cls(
            question_id=coerce_str(known.get("question_id")),
            agent=coerce_str(known.get("agent")),
            question=coerce_str(known.get("question")),
            blocking=coerce_bool(known.get("blocking"), False),
            importance=normalize_importance(known.get("importance")),
            tags=tags,
            context=context,
            extra=extra,
        )

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            "question_id": self.question_id,
            "agent": self.agent,
            "question": self.question,
            "blocking": self.blocking,
            "importance": self.importance,
            "tags": self.tags,
            "context": self.context,
        }
        payload.update(self.extra)
        return payload


@dataclass(slots=True)
class Answer:
    question_id: str
    answer: str
    source: str
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Answer":
        known, extra = split_known_fields(data, ("question_id", "answer", "source"))
        return cls(
            question_id=coerce_str(known.get("question_id")),
            answer=coerce_str(known.get("answer")),
            source=coerce_str(known.get("source")),
            extra=extra,
        )

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            "question_id": self.question_id,
            "answer": self.answer,
            "source": self.source,
        }
        payload.update(self.extra)
        return payload


@dataclass(slots=True)
class RouteDecision:
    route: str
    reason: str
    question: Question

    @property
    def is_gate(self) -> bool:
        return self.route == "gate"

    @property
    def is_auto_answer(self) -> bool:
        return self.route == "auto_answer"


def normalize_importance(value: Any) -> str:
    text = coerce_str(value, "medium").strip().lower()
    if text in {"", "normal"}:
        return "medium"
    return text


def normalize_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, (list, tuple, set, frozenset)):
        return [coerce_str(tag).strip() for tag in tags if coerce_str(tag).strip()]
    text = coerce_str(tags).strip()
    return [text] if text else []


def _configured_gate_tags(question: Question) -> set[str]:
    raw_tags = question.context.get("decision_gate_tags")
    if raw_tags is None:
        raw_tags = question.extra.get("decision_gate_tags")
    if raw_tags is None:
        return set(DECISION_GATE_TAGS)
    return set(normalize_tags(raw_tags))


def _explicit_gate_marker(question: Question) -> bool:
    marker = coerce_str(question.context.get("marker") or question.extra.get("marker")).strip().lower()
    if marker in {"decision-gate", "decision_gate"}:
        return True
    return coerce_bool(question.context.get("decision_gate"), False) or coerce_bool(question.extra.get("decision_gate"), False)


def decision_gate_tags(question: Question) -> set[str]:
    return set(normalize_tags(question.tags)) & _configured_gate_tags(question)


def is_decision_gate(question: Question) -> bool:
    explicit = decision_gate_tags(question)
    if explicit:
        return True
    if _explicit_gate_marker(question):
        return True
    if coerce_bool(question.context.get("requires_human"), False):
        return True
    if coerce_bool(question.extra.get("requires_human"), False):
        return True
    return False


def route_question(question: Question) -> RouteDecision:
    if is_decision_gate(question):
        return RouteDecision(
            route="gate",
            reason="question matches an explicit human decision-gate signal",
            question=question,
        )
    return RouteDecision(
        route="auto_answer",
        reason="question can be handled by the supervisor automatically",
        question=question,
    )


def questions_dir(memory_root: Path | str) -> Path:
    return runtime_paths(memory_root).questions_dir


def answers_dir(memory_root: Path | str) -> Path:
    return runtime_paths(memory_root).answers_dir


def question_path(memory_root: Path | str, name: str) -> Path:
    return questions_dir(memory_root) / f"{name}.json"


def answer_path(memory_root: Path | str, question_id: str) -> Path:
    return answers_dir(memory_root) / f"{question_id}.json"


def read_question(path: Path) -> Question:
    return Question.from_mapping(read_json_file(path))


def write_question(path: Path, question: Question) -> Path:
    write_json_file(path, question.to_mapping())
    return path


def read_answer(path: Path) -> Answer:
    return Answer.from_mapping(read_json_file(path))


def write_answer(path: Path, answer: Answer) -> Path:
    write_json_file(path, answer.to_mapping())
    return path


def save_question(memory_root: Path | str, name: str, question: Question) -> Path:
    paths = ensure_runtime_layout(memory_root)
    path = paths.questions_dir / f"{name}.json"
    write_json_file(path, question.to_mapping())
    return path


def save_answer(memory_root: Path | str, question_id: str, answer: Answer) -> Path:
    paths = ensure_runtime_layout(memory_root)
    path = paths.answers_dir / f"{question_id}.json"
    write_json_file(path, answer.to_mapping())
    return path

from __future__ import annotations

from typing import Any, Mapping

from .question_router import Answer, Question, is_decision_gate
from .runtime_state import coerce_str


def _candidate_path(question: Question) -> str:
    context = question.context
    extra = question.extra
    for key in ("preferred_path", "target_path", "path"):
        value = context.get(key) or extra.get(key)
        if value:
            return coerce_str(value)
    candidates = context.get("candidate_paths") or extra.get("candidate_paths")
    if isinstance(candidates, list) and candidates:
        return coerce_str(candidates[0])
    return ""


def _default_answer_text(question: Question) -> str:
    text = question.question.lower()
    candidate = _candidate_path(question)
    if candidate:
        return candidate
    if "verification" in text or "测试" in text or "validate" in text:
        return "先跑最小验证命令，确认主线可行，再决定是否扩展。"
    if "scope" in text or "范围" in text or "扩大" in text:
        return "先延续当前切片，不扩大范围。"
    if "path" in text or "file" in text or "文件" in text or "路径" in text:
        return "优先使用当前 handoff 中已指定的路径。"
    if "retry" in text or "重试" in text or "失败" in text:
        return "保持同一 handoff，按最小代价重试一次。"
    if "human" in text or "人类" in text:
        return "只有真正的 decision gate 才升级给人类。"
    return "按当前 handoff 继续主线，不需要升级人类。"


def answer_question(question: Question | Mapping[str, Any]) -> Answer | None:
    parsed = question if isinstance(question, Question) else Question.from_mapping(question)
    if is_decision_gate(parsed):
        return None
    return Answer(
        question_id=parsed.question_id,
        answer=_default_answer_text(parsed),
        source="supervisor:auto",
    )

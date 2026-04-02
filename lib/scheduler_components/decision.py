from __future__ import annotations

import json
from typing import Any, Mapping

from ..runtime_state import coerce_str, utc_now
from .support import _write_json


def _execute_decision_turn(
    scheduler: Any,
    turn: Any,
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    brief = inputs.get("communication_brief", {})
    latest_human_reply = inputs.get("latest_human_reply", {})
    if isinstance(latest_human_reply, Mapping) and latest_human_reply:
        artifact_path = scheduler._artifact_path(turn, "human-reply")
        _write_json(
            artifact_path,
            {
                "reply": dict(latest_human_reply),
                "decision_brief": dict(brief) if isinstance(brief, Mapping) else {},
                "resume_agent": inputs.get("resume_agent", ""),
                "recorded_at": utc_now(),
            },
        )
        return {
            "status": "completed",
            "summary": "Recorded the human reply and returned control to supervisor.",
            "decision_action": "reply_recorded",
            "communication_action": "reply_recorded",
            "artifacts": [str(artifact_path)],
        }
    if isinstance(brief, Mapping) and brief:
        prompt = scheduler._render_communication_prompt(brief)
        gate = turn.communication_store.open_gate(
            title=coerce_str(brief.get("title"), "Decision gate").strip() or "Decision gate",
            prompt=prompt,
            source="supervisor",
            severity=coerce_str(brief.get("severity"), "decision_gate").strip() or "decision_gate",
            context=json.dumps(dict(brief), ensure_ascii=False),
        )
        artifact_path = scheduler._artifact_path(turn, "gate")
        _write_json(
            artifact_path,
            {
                "gate": gate,
                "decision_brief": dict(brief),
                "rendered_prompt": prompt,
                "created_at": utc_now(),
            },
        )
        return {
            "status": "blocked",
            "summary": f"Opened decision gate {gate['id']}",
            "gate_id": gate["id"],
            "decision_action": "gate_opened",
            "communication_action": "gate_opened",
            "artifacts": [str(artifact_path), str(turn.communication_store.state_file)],
        }
    artifact_path = scheduler._artifact_path(turn, "idle")
    _write_json(
        artifact_path,
        {
            "summary": "Decision agent had no pending brief or reply to process.",
            "recorded_at": utc_now(),
        },
    )
    return {
        "status": "completed",
        "summary": "Decision agent had no pending work.",
        "decision_action": "idle",
        "communication_action": "idle",
        "artifacts": [str(artifact_path)],
    }

from __future__ import annotations

from typing import Any, Mapping


SESSION_CONTROL_FIELD = "session-control"
TASK_NOTIFICATION_FIELD = "task-notification"

CONTROL_ACTION_SPAWN = "spawn"
CONTROL_ACTION_CONTINUE = "continue"
CONTROL_ACTION_TERMINATE = "terminate"
INTERNAL_CONTROL_ACTION_RESUME = "resume"

PUBLIC_CONTROL_ACTIONS = (
    CONTROL_ACTION_SPAWN,
    CONTROL_ACTION_CONTINUE,
    CONTROL_ACTION_TERMINATE,
)
TASK_NOTIFICATION_REQUIRED_FIELDS = ("session", "status", "summary")
TASK_NOTIFICATION_OPTIONAL_FIELDS = ("result", "output-file")


def coerce_session_control(
    value: Mapping[str, Any] | str | None,
    *,
    default_action: str = CONTROL_ACTION_SPAWN,
    allow_internal: bool = False,
) -> dict[str, str]:
    payload = dict(value) if isinstance(value, Mapping) else {}
    action = (str(payload.get("action") or value or default_action)).strip().lower()
    session = str(payload.get("session") or "").strip()
    allowed_actions = PUBLIC_CONTROL_ACTIONS + ((INTERNAL_CONTROL_ACTION_RESUME,) if allow_internal else ())
    if action not in allowed_actions:
        raise ValueError(f"unsupported session-control action: {action!r}")
    if action == CONTROL_ACTION_CONTINUE and not session:
        raise ValueError("session-control 'continue' requires a session")
    normalized = {"action": action}
    if session:
        normalized["session"] = session
    return normalized


def build_task_notification(
    *,
    session: Any,
    status: Any,
    summary: Any,
    result: Any | None = None,
    output_file: Any = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session": str(session).strip(),
        "status": str(status).strip(),
        "summary": str(summary),
    }
    if result is not None:
        payload["result"] = result
    output_path = str(output_file).strip()
    if output_path:
        payload["output-file"] = output_path
    return payload


def coerce_task_notification(
    value: Mapping[str, Any] | None,
    *,
    default_session: Any = "",
    default_status: Any = "",
    default_summary: Any = "",
    result: Any | None = None,
    output_file: Any = "",
) -> dict[str, Any]:
    payload = dict(value or {})
    if "output-file" not in payload and "output_file" in payload:
        payload["output-file"] = payload.pop("output_file")
    if "result" not in payload and result is not None:
        payload["result"] = result
    if "output-file" not in payload and str(output_file).strip():
        payload["output-file"] = str(output_file).strip()
    return build_task_notification(
        session=payload.get("session", default_session),
        status=payload.get("status", default_status),
        summary=payload.get("summary", default_summary),
        result=payload["result"] if "result" in payload else None,
        output_file=payload.get("output-file", ""),
    )

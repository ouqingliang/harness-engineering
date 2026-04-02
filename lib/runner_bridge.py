from __future__ import annotations

import json
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from lib.communication_api import CommunicationStore, coerce_gate_payload, pending_gates
from lib.runtime_contract import (
    SESSION_CONTROL_FIELD,
    TASK_NOTIFICATION_FIELD,
    coerce_session_control,
    coerce_task_notification,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp-{uuid.uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        for attempt in range(10):
            try:
                temp_path.replace(path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _json_copy(payload: Any) -> Any:
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "to_dict"):
        candidate = value.to_dict()
        if isinstance(candidate, Mapping):
            return candidate
    candidate: dict[str, Any] = {}
    for key in ("id", "name", "order", "dependencies", "goal", "title"):
        if hasattr(value, key):
            candidate[key] = getattr(value, key)
    if hasattr(value, "agent_id"):
        candidate["id"] = getattr(value, "agent_id")
    return candidate


def _normalize_agent_spec(agent_spec: Any) -> dict[str, Any]:
    payload = dict(_as_mapping(agent_spec))
    task = payload.get("task")
    task_payload = dict(task) if isinstance(task, Mapping) else {}
    dependencies = payload.get("dependencies", ())
    if dependencies is None:
        dependencies = ()
    if not isinstance(dependencies, (list, tuple)):
        dependencies = (dependencies,)
    return {
        "id": str(payload.get("id", "")).strip(),
        "name": str(payload.get("name", "")).strip(),
        "order": int(payload.get("order", 100)),
        "dependencies": tuple(str(item) for item in dependencies),
        "goal": str(payload.get("goal") or task_payload.get("goal", "")).strip(),
        "title": str(payload.get("title") or task_payload.get("title", "")).strip(),
    }


def _normalize_runtime_paths(runtime_paths: Mapping[str, Any] | None, runtime_root: Path) -> dict[str, Path]:
    runtime_paths = dict(runtime_paths or {})
    runtime_root = Path(runtime_paths.get("runtime_root", runtime_root))
    handoff_dir = Path(runtime_paths.get("handoff_dir", runtime_root / "handoffs"))
    report_dir = Path(runtime_paths.get("report_dir", runtime_root / "reports"))
    launcher_dir = Path(runtime_paths.get("launcher_dir", runtime_root / "launchers" / "codex_app_server"))
    state_file = Path(runtime_paths.get("state_file", launcher_dir / "state.json"))
    communication_state_file = Path(runtime_paths.get("communication_state_file", runtime_root / "launchers" / "communication" / "state.json"))
    return {
        "runtime_root": runtime_root,
        "handoff_dir": handoff_dir,
        "report_dir": report_dir,
        "launcher_dir": launcher_dir,
        "state_file": state_file,
        "communication_state_file": communication_state_file,
    }


def _extract_session_control(payload: Mapping[str, Any]) -> dict[str, str] | None:
    candidate = payload.get(SESSION_CONTROL_FIELD, payload.get("session_control"))
    if candidate is None:
        return None
    return coerce_session_control(candidate)


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "run_count": 0,
        "last_cycle_id": None,
        "last_run_at": None,
        "last_agent": None,
        "paused_gate_id": None,
        "last_handoff_path": None,
        "last_report_path": None,
    }


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@dataclass(frozen=True)
class RunnerTurn:
    cycle_id: str
    sequence: int
    agent_spec: dict[str, Any]
    handoff: dict[str, Any]
    runtime_paths: dict[str, Path]
    mission: dict[str, Any]
    state: dict[str, Any]
    handoff_path: Path
    report_path: Path
    communication_store: CommunicationStore


def _default_turn_executor(turn: RunnerTurn) -> dict[str, Any]:
    agent_id = turn.agent_spec["id"]
    report: dict[str, Any] = {
        "status": "completed",
        "summary": f"{turn.agent_spec['name'] or agent_id} processed cycle {turn.cycle_id}.",
        "artifacts": [str(turn.handoff_path)],
        "next_hint": "supervisor decides next step",
    }
    if agent_id == "communication":
        open_gate_spec = turn.mission.get("decision_gate")
        if open_gate_spec:
            gate_payload = coerce_gate_payload(open_gate_spec) if isinstance(open_gate_spec, Mapping) else {
                "title": "Decision gate",
                "prompt": str(open_gate_spec),
                "source": "communication-agent",
                "severity": "decision_gate",
                "context": None,
            }
            gate = turn.communication_store.open_gate(**gate_payload)
            report.update(
                {
                    "status": "blocked",
                    "summary": f"Opened decision gate {gate['id']}",
                    "gate_id": gate["id"],
                    "artifacts": [str(turn.communication_store.state_file)],
                    "next_hint": "wait for human reply",
                }
            )
        else:
            open_gate_ids = [gate["id"] for gate in pending_gates(turn.communication_store)]
            report["summary"] = f"Communication surface is ready; {len(open_gate_ids)} gate(s) pending."
            if open_gate_ids:
                report["artifacts"] = [str(turn.communication_store.state_file)]
    return report


def _normalize_report(raw_report: Mapping[str, Any] | None, turn: RunnerTurn, *, default_report: Mapping[str, Any]) -> dict[str, Any]:
    report = dict(default_report)
    if raw_report:
        report.update(dict(raw_report))
    report.setdefault("agent", turn.agent_spec["id"])
    report.setdefault("status", "completed")
    report.setdefault("summary", turn.handoff.get("goal") or turn.agent_spec.get("goal", ""))
    report.setdefault("artifacts", [])
    report.setdefault("next_hint", "cycle complete")
    report["artifacts"] = [str(item) for item in report.get("artifacts", [])]
    task_notification = report.get(TASK_NOTIFICATION_FIELD, report.get("task_notification"))
    if task_notification is not None:
        report[TASK_NOTIFICATION_FIELD] = coerce_task_notification(
            task_notification if isinstance(task_notification, Mapping) else None,
            default_summary=report.get("summary", ""),
        )
        report.pop("task_notification", None)
    report["completed_at"] = _utc_now()
    return report


def _build_handoff(agent_spec: Mapping[str, Any], handoff: Mapping[str, Any] | None, mission: Mapping[str, Any], state: Mapping[str, Any], *, cycle_id: str, sequence: int, runtime_paths: Mapping[str, Path]) -> dict[str, Any]:
    handoff_payload = dict(handoff or {})
    goal = handoff_payload.get("goal") or mission.get("goal") or agent_spec.get("goal", "")
    done_when = handoff_payload.get("done_when") or mission.get("done_when") or []
    if isinstance(done_when, str):
        done_when = [done_when]
    inputs = dict(handoff_payload.get("inputs") or {})
    inputs.setdefault("mission", _json_copy(mission))
    inputs.setdefault("state", _json_copy(state))
    inputs.setdefault("previous_agent", state.get("last_agent"))
    payload = {
        "id": handoff_payload.get("id") or _new_id("handoff"),
        "cycle_id": cycle_id,
        "sequence": sequence,
        "from": handoff_payload.get("from") or state.get("last_agent") or "supervisor",
        "to": agent_spec["id"],
        "goal": goal,
        "inputs": inputs,
        "done_when": list(done_when),
        "agent_spec": _json_copy(agent_spec),
        "mission": _json_copy(mission),
        "state": _json_copy(state),
        "runtime_paths": {key: str(value) for key, value in runtime_paths.items()},
    }
    session_control = _extract_session_control(handoff_payload)
    if session_control is not None:
        payload[SESSION_CONTROL_FIELD] = session_control
    return payload


def _write_turn_state(state_file: Path, payload: Mapping[str, Any]) -> None:
    _write_json_atomic(state_file, dict(payload))


def run_agent(
    agent_spec: Any,
    handoff: Mapping[str, Any],
    runtime_paths: Mapping[str, Any],
    mission: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    turn_executor: Callable[[RunnerTurn], Mapping[str, Any] | None] | None = None,
    communication_store: CommunicationStore | None = None,
) -> dict[str, Any]:
    normalized_agent_spec = _normalize_agent_spec(agent_spec)
    if not normalized_agent_spec["id"]:
        raise ValueError("agent_spec.id is required")
    runtime_paths_payload = _normalize_runtime_paths(
        runtime_paths,
        Path(runtime_paths.get("runtime_root", state.get("runtime_root", "."))) if isinstance(runtime_paths, Mapping) else Path("."),
    )
    runtime_root = runtime_paths_payload["runtime_root"]
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_paths_payload["handoff_dir"].mkdir(parents=True, exist_ok=True)
    runtime_paths_payload["report_dir"].mkdir(parents=True, exist_ok=True)
    runtime_paths_payload["launcher_dir"].mkdir(parents=True, exist_ok=True)
    communication_store = communication_store or CommunicationStore(runtime_root)

    cycle_id = str(state.get("cycle_id") or mission.get("cycle_id") or _new_id("cycle"))
    sequence = int(handoff.get("sequence", state.get("sequence", 0)))
    handoff_path = Path(handoff.get("handoff_path", runtime_paths_payload["handoff_dir"] / f"{cycle_id}-{sequence:02d}-{normalized_agent_spec['id']}.json"))
    report_path = Path(handoff.get("report_path", runtime_paths_payload["report_dir"] / f"{cycle_id}-{sequence:02d}-{normalized_agent_spec['id']}.json"))

    turn_state = dict(state)
    turn_state.setdefault("runtime_root", str(runtime_root))
    turn_state.setdefault("cycle_id", cycle_id)
    turn = RunnerTurn(
        cycle_id=cycle_id,
        sequence=sequence,
        agent_spec=normalized_agent_spec,
        handoff=_build_handoff(normalized_agent_spec, handoff, dict(mission), turn_state, cycle_id=cycle_id, sequence=sequence, runtime_paths=runtime_paths_payload),
        runtime_paths=runtime_paths_payload,
        mission=dict(mission),
        state=turn_state,
        handoff_path=handoff_path,
        report_path=report_path,
        communication_store=communication_store,
    )
    _write_json_atomic(handoff_path, turn.handoff)

    executor = turn_executor or _default_turn_executor
    raw_report = executor(turn)
    report = _normalize_report(raw_report, turn, default_report=_default_turn_executor(turn))
    _write_json_atomic(report_path, report)

    state_after = dict(turn_state)
    state_after.update(
        {
            "cycle_id": cycle_id,
            "sequence": sequence + 1,
            "last_agent": normalized_agent_spec["id"],
            "last_run_at": _utc_now(),
            "last_handoff_path": str(handoff_path),
            "last_report_path": str(report_path),
            "paused_gate_id": report.get("gate_id") if report.get("status") == "blocked" else None,
        }
    )
    _write_turn_state(runtime_paths_payload["state_file"], state_after)
    return {
        "agent": normalized_agent_spec,
        "handoff": turn.handoff,
        "report": report,
        "handoff_path": str(handoff_path),
        "report_path": str(report_path),
        "state_after": state_after,
        "cycle_id": cycle_id,
    }


class RunnerBridge:
    def __init__(self, runtime_root: Path, *, communication_store: CommunicationStore | None = None, turn_executor: Callable[[RunnerTurn], Mapping[str, Any] | None] | None = None) -> None:
        self.runtime_root = Path(runtime_root)
        self._lock = threading.RLock()
        self.communication_store = communication_store or CommunicationStore(self.runtime_root)
        self.turn_executor = turn_executor
        self.runtime_paths = _normalize_runtime_paths(None, self.runtime_root)
        self.runtime_paths["runtime_root"].mkdir(parents=True, exist_ok=True)
        self.runtime_paths["handoff_dir"].mkdir(parents=True, exist_ok=True)
        self.runtime_paths["report_dir"].mkdir(parents=True, exist_ok=True)
        self.runtime_paths["launcher_dir"].mkdir(parents=True, exist_ok=True)
        if not self.runtime_paths["state_file"].exists():
            _write_turn_state(self.runtime_paths["state_file"], _default_state())

    def _load_state(self) -> dict[str, Any]:
        return _read_json(self.runtime_paths["state_file"], _default_state())

    def _save_state(self, payload: Mapping[str, Any]) -> None:
        _write_turn_state(self.runtime_paths["state_file"], payload)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "runtime_root": str(self.runtime_root),
                "runtime_paths": {key: str(value) for key, value in self.runtime_paths.items()},
                "state": deepcopy(self._load_state()),
                "pending_gates": pending_gates(self.communication_store),
            }

    def run_agent(
        self,
        agent_spec: Any,
        handoff: Mapping[str, Any],
        *,
        mission: Mapping[str, Any] | None = None,
        state: Mapping[str, Any] | None = None,
        runtime_paths: Mapping[str, Any] | None = None,
        turn_executor: Callable[[RunnerTurn], Mapping[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        payload = run_agent(
            agent_spec,
            handoff,
            runtime_paths or self.runtime_paths,
            mission or {},
            state or self._load_state(),
            turn_executor=turn_executor or self.turn_executor,
            communication_store=self.communication_store,
        )
        self._save_state(payload["state_after"])
        return payload

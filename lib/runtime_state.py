from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Iterable, Mapping
import uuid


HARNESS_DIR_NAME = ".harness"
MISSION_FILE_NAME = "mission.json"
STATE_FILE_NAME = "state.json"
HANDOFFS_DIR_NAME = "handoffs"
REPORTS_DIR_NAME = "reports"
QUESTIONS_DIR_NAME = "questions"
ANSWERS_DIR_NAME = "answers"
ARTIFACTS_DIR_NAME = "artifacts"
LOCKS_DIR_NAME = "locks"
LAUNCHERS_DIR_NAME = "launchers"
DEFAULT_DECISION_GATE_TAGS = (
    "architecture_change",
    "destructive_action",
    "security_boundary",
    "external_side_effect",
    "goal_conflict",
)
DEFAULT_CLEANUP_MAINTENANCE_INTERVAL_SECONDS = 4 * 60 * 60


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    memory_root: Path
    harness_root: Path
    mission_file: Path
    state_file: Path
    handoffs_dir: Path
    reports_dir: Path
    questions_dir: Path
    answers_dir: Path
    artifacts_dir: Path
    locks_dir: Path
    launchers_dir: Path


@dataclass(slots=True)
class HarnessConfig:
    memory_root: str = "runtime-memory"
    doc_root: str = ""
    goal: str = ""
    sleep_seconds: float = 5.0
    decision_gate_tags: tuple[str, ...] = DEFAULT_DECISION_GATE_TAGS
    cleanup_maintenance_interval_seconds: int = DEFAULT_CLEANUP_MAINTENANCE_INTERVAL_SECONDS
    default_launcher: str = "codex_app_server"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HarnessConfig":
        known, extra = split_known_fields(
            data,
            (
                "memory_root",
                "doc_root",
                "goal",
                "sleep_seconds",
                "decision_gate_tags",
                "cleanup_maintenance_interval_seconds",
                "default_launcher",
            ),
        )
        raw_tags = known.get("decision_gate_tags", ())
        if isinstance(raw_tags, (list, tuple)):
            tags = tuple(coerce_str(item).strip() for item in raw_tags if coerce_str(item).strip())
        else:
            tag = coerce_str(raw_tags).strip()
            tags = (tag,) if tag else ()
        return cls(
            memory_root=coerce_str(known.get("memory_root"), "runtime-memory"),
            doc_root=coerce_str(known.get("doc_root")),
            goal=coerce_str(known.get("goal")),
            sleep_seconds=float(known.get("sleep_seconds", 5.0)),
            decision_gate_tags=tags or DEFAULT_DECISION_GATE_TAGS,
            cleanup_maintenance_interval_seconds=max(
                1,
                coerce_int(
                    known.get("cleanup_maintenance_interval_seconds"),
                    DEFAULT_CLEANUP_MAINTENANCE_INTERVAL_SECONDS,
                ),
            ),
            default_launcher=coerce_str(known.get("default_launcher"), "codex_app_server"),
            extra=extra,
        )

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            "memory_root": self.memory_root,
            "doc_root": self.doc_root,
            "goal": self.goal,
            "sleep_seconds": self.sleep_seconds,
            "decision_gate_tags": list(self.decision_gate_tags),
            "cleanup_maintenance_interval_seconds": self.cleanup_maintenance_interval_seconds,
            "default_launcher": self.default_launcher,
        }
        payload.update(self.extra)
        return payload


@dataclass(slots=True)
class Mission:
    doc_root: str
    goal: str
    status: str = "pending"
    round: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Mission":
        known, extra = split_known_fields(data, ("doc_root", "goal", "status", "round"))
        return cls(
            doc_root=coerce_str(known.get("doc_root")),
            goal=coerce_str(known.get("goal")),
            status=coerce_str(known.get("status"), "pending"),
            round=coerce_int(known.get("round"), 0),
            extra=extra,
        )

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            "doc_root": self.doc_root,
            "goal": self.goal,
            "status": self.status,
            "round": self.round,
        }
        payload.update(self.extra)
        return payload


@dataclass(slots=True)
class RuntimeState:
    active_agent: str = ""
    last_successful_agent: str = ""
    retry_count: int = 0
    last_run_at: str = ""
    current_round: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RuntimeState":
        known, extra = split_known_fields(
            data,
            ("active_agent", "last_successful_agent", "retry_count", "last_run_at", "current_round"),
        )
        return cls(
            active_agent=coerce_str(known.get("active_agent")),
            last_successful_agent=coerce_str(known.get("last_successful_agent")),
            retry_count=coerce_int(known.get("retry_count"), 0),
            last_run_at=coerce_str(known.get("last_run_at")),
            current_round=coerce_int(known.get("current_round"), 0),
            extra=extra,
        )

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            "active_agent": self.active_agent,
            "last_successful_agent": self.last_successful_agent,
            "retry_count": self.retry_count,
            "last_run_at": self.last_run_at,
            "current_round": self.current_round,
        }
        payload.update(self.extra)
        return payload


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def coerce_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def split_known_fields(
    data: Mapping[str, Any], known_fields: Iterable[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    known_field_set = set(known_fields)
    known: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in data.items():
        if key in known_field_set:
            known[key] = value
        else:
            extra[key] = value
    return known, extra


def read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_file(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp-{uuid.uuid4().hex}")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
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


def runtime_paths(memory_root: Path | str) -> RuntimePaths:
    memory_root_path = Path(memory_root)
    harness_root = memory_root_path / HARNESS_DIR_NAME
    return RuntimePaths(
        memory_root=memory_root_path,
        harness_root=harness_root,
        mission_file=harness_root / MISSION_FILE_NAME,
        state_file=harness_root / STATE_FILE_NAME,
        handoffs_dir=harness_root / HANDOFFS_DIR_NAME,
        reports_dir=harness_root / REPORTS_DIR_NAME,
        questions_dir=harness_root / QUESTIONS_DIR_NAME,
        answers_dir=harness_root / ANSWERS_DIR_NAME,
        artifacts_dir=harness_root / ARTIFACTS_DIR_NAME,
        locks_dir=harness_root / LOCKS_DIR_NAME,
        launchers_dir=harness_root / LAUNCHERS_DIR_NAME,
    )


def ensure_runtime_layout(memory_root: Path | str) -> RuntimePaths:
    paths = runtime_paths(memory_root)
    paths.harness_root.mkdir(parents=True, exist_ok=True)
    paths.handoffs_dir.mkdir(parents=True, exist_ok=True)
    paths.reports_dir.mkdir(parents=True, exist_ok=True)
    paths.questions_dir.mkdir(parents=True, exist_ok=True)
    paths.answers_dir.mkdir(parents=True, exist_ok=True)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.locks_dir.mkdir(parents=True, exist_ok=True)
    paths.launchers_dir.mkdir(parents=True, exist_ok=True)
    return paths


def ensure_runtime_root(memory_root: Path | str) -> RuntimePaths:
    return ensure_runtime_layout(memory_root)


def default_mission(
    doc_root: Path | str,
    goal: str = "",
    status: str = "pending",
    round_number: int = 0,
    extra: Mapping[str, Any] | None = None,
) -> Mission:
    return Mission(
        doc_root=coerce_str(doc_root),
        goal=goal,
        status=status,
        round=round_number,
        extra=dict(extra or {}),
    )


def load_mission(memory_root: Path | str) -> Mission:
    return Mission.from_mapping(read_json_file(runtime_paths(memory_root).mission_file))


def save_mission(memory_root: Path | str, mission: Mission) -> Path:
    paths = ensure_runtime_layout(memory_root)
    write_json_file(paths.mission_file, mission.to_mapping())
    return paths.mission_file


def load_state(memory_root: Path | str) -> RuntimeState:
    return RuntimeState.from_mapping(read_json_file(runtime_paths(memory_root).state_file))


def save_state(memory_root: Path | str, state: RuntimeState) -> Path:
    paths = ensure_runtime_layout(memory_root)
    write_json_file(paths.state_file, state.to_mapping())
    return paths.state_file


def ensure_mission(
    memory_root: Path | str,
    doc_root: Path | str,
    goal: str = "",
    status: str = "pending",
    round_number: int = 0,
    extra: Mapping[str, Any] | None = None,
) -> Mission:
    paths = ensure_runtime_layout(memory_root)
    if paths.mission_file.exists():
        return load_mission(memory_root)
    mission = default_mission(doc_root=doc_root, goal=goal, status=status, round_number=round_number, extra=extra)
    write_json_file(paths.mission_file, mission.to_mapping())
    return mission


def load_or_build_mission(
    memory_root: Path | str,
    doc_root: Path | str,
    goal: str = "",
    status: str = "pending",
    round_number: int = 0,
    extra: Mapping[str, Any] | None = None,
) -> Mission:
    return ensure_mission(
        memory_root,
        doc_root,
        goal=goal,
        status=status,
        round_number=round_number,
        extra=extra,
    )


def ensure_state(
    memory_root: Path | str,
    *,
    active_agent: str = "",
    last_successful_agent: str = "",
    retry_count: int = 0,
    last_run_at: str = "",
    current_round: int = 0,
    extra: Mapping[str, Any] | None = None,
) -> RuntimeState:
    paths = ensure_runtime_layout(memory_root)
    if paths.state_file.exists():
        return load_state(memory_root)
    state = RuntimeState(
        active_agent=active_agent,
        last_successful_agent=last_successful_agent,
        retry_count=retry_count,
        last_run_at=last_run_at,
        current_round=current_round,
        extra=dict(extra or {}),
    )
    write_json_file(paths.state_file, state.to_mapping())
    return state


def load_or_init_state(
    memory_root: Path | str,
    *,
    active_agent: str = "",
    last_successful_agent: str = "",
    retry_count: int = 0,
    last_run_at: str | None = None,
    current_round: int = 0,
    extra: Mapping[str, Any] | None = None,
) -> RuntimeState:
    return ensure_state(
        memory_root,
        active_agent=active_agent,
        last_successful_agent=last_successful_agent,
        retry_count=retry_count,
        last_run_at=last_run_at or utc_now(),
        current_round=current_round,
        extra=extra,
    )

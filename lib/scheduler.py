from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
import uuid

from .auto_answer import answer_question
from .communication_api import CommunicationStore
from .documents import build_doc_bundle
from .project_context import path_within, project_root_from_doc_root, same_path
from .question_router import Question, route_question, save_answer, save_question
from .runtime_state import (
    Mission,
    RuntimePaths,
    RuntimeState,
    coerce_bool,
    coerce_int,
    coerce_str,
    save_mission,
    save_state,
    utc_now,
)
from .runner_bridge import RunnerBridge, RunnerTurn


DEFAULT_CLEANUP_MAINTENANCE_INTERVAL_SECONDS = 4 * 60 * 60
HARNESS_ROOT = Path(__file__).resolve().parents[1]
CODEX_EXECUTABLE_NAMES = ("codex.cmd", "codex.exe", "codex")
ARCHITECTURE_BASELINE_DOCS = (
    "designs/2026-03-25-task-centered-autonomous-ops-platform.md",
    "designs/2026-03-25-harness-engineering-integration.md",
    "designs/2026-03-25-center-subsystem-architecture-outline.md",
    "plans/2026-03-25-task-mainline-and-engineernode-removal.md",
)
DEFAULT_EXECUTION_OUTPUT = {
    "status": "unknown",
    "summary": "",
    "changed_paths": [],
    "verification_notes": [],
    "needs_human": False,
    "human_question": "",
    "why_not_auto_answered": "",
    "required_reply_shape": "",
    "decision_tags": [],
    "options": [],
    "notes": [],
}
SUPERVISOR_DECISION_NEGATION_HINTS = (
    "ignore",
    "exclude",
    "separate",
    "not block",
    "not a blocker",
    "unless",
    "only if related",
    "split out",
    "defer",
    "temporary non-blocker",
    "fen li",
    "bu zu sai",
    "ji you shi bai",
    "xiang guan",
    "wu guan",
    "分离",
    "不阻塞",
    "不是 blocker",
    "不是blocker",
    "除非",
    "仅在",
    "只有在",
    "无关",
    "既有失败",
)


@dataclass(frozen=True, slots=True)
class SchedulerResult:
    status: str
    steps: list[dict[str, Any]]
    pending_gate_id: str | None
    mission: Mission
    state: RuntimeState


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _spec_value(spec: Any, key: str, default: Any = "") -> Any:
    if isinstance(spec, Mapping):
        return spec.get(key, default)
    if key == "id" and hasattr(spec, "agent_id"):
        return getattr(spec, "agent_id")
    return getattr(spec, key, default)


def _spec_mapping(spec: Any) -> dict[str, Any]:
    return {
        "id": str(_spec_value(spec, "id")),
        "name": str(_spec_value(spec, "name")),
        "order": int(_spec_value(spec, "order", 100)),
        "dependencies": tuple(_spec_value(spec, "dependencies", ()) or ()),
        "title": str(_spec_value(spec, "title")),
        "goal": str(_spec_value(spec, "goal")),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _command_display(command: Sequence[str]) -> str:
    return " ".join(command)


def _harness_default_verification_command() -> list[str]:
    return [sys.executable, "-m", "unittest", "tests.test_runtime_files", "-v"]


def _normalize_verification_command(candidate: Any) -> list[str]:
    if isinstance(candidate, str):
        return shlex.split(candidate, posix=sys.platform != "win32")
    if isinstance(candidate, Sequence) and not isinstance(candidate, (bytes, bytearray, str)):
        return [str(item) for item in candidate if str(item)]
    if isinstance(candidate, Mapping):
        raw_command = candidate.get("command", candidate.get("argv", []))
        return _normalize_verification_command(raw_command)
    return []


def _normalize_env_mapping(candidate: Any) -> dict[str, str]:
    if not isinstance(candidate, Mapping):
        return {}
    return {
        coerce_str(key).strip(): coerce_str(value)
        for key, value in candidate.items()
        if coerce_str(key).strip()
    }


def _resolve_cwd(candidate: Any, *, project_root: Path, default_cwd: Path) -> Path:
    raw = coerce_str(candidate).strip()
    if not raw:
        return default_cwd
    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _generic_external_verification_spec(*, project_root: Path, doc_root: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; import sys; "
            "project = Path(sys.argv[1]); doc = Path(sys.argv[2]); "
            "assert project.exists(), project; assert doc.exists(), doc; "
            "print(project); print(doc)"
        ),
        str(project_root),
        str(doc_root),
    ]
    return {
        "command": command,
        "command_display": _command_display(command),
        "cwd": str(project_root),
        "env": {},
        "source": "generic_external_default",
    }


def _default_verification_specs(*, project_root: Path, doc_root: Path) -> list[dict[str, Any]]:
    if same_path(project_root, HARNESS_ROOT):
        command = _harness_default_verification_command()
        return [
            {
                "command": command,
                "command_display": _command_display(command),
                "cwd": str(HARNESS_ROOT),
                "env": {},
                "source": "harness_default",
            }
        ]
    return [_generic_external_verification_spec(project_root=project_root, doc_root=doc_root)]


def _parse_shell_verification_spec(raw_command: str, *, project_root: Path, default_cwd: Path) -> dict[str, Any] | None:
    command_text = raw_command.strip()
    if not command_text:
        return None
    if command_text.startswith("(") and command_text.endswith(")"):
        command_text = command_text[1:-1].strip()
    cwd = default_cwd
    env: dict[str, str] = {}
    command: list[str] = []
    for segment in [item.strip() for item in command_text.split("&&") if item.strip()]:
        tokens = shlex.split(segment, posix=True)
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) >= 2:
            cwd = _resolve_cwd(tokens[1], project_root=project_root, default_cwd=default_cwd)
            continue
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", tokens[0]):
            key, value = tokens.pop(0).split("=", 1)
            env[key] = value
        if tokens:
            command = [str(item) for item in tokens]
    if not command:
        return None
    return {
        "command": command,
        "command_display": command_text,
        "cwd": str(cwd),
        "env": env,
        "source": "shell_text",
    }


def _normalize_verification_spec(
    candidate: Any,
    *,
    project_root: Path,
    default_cwd: Path,
) -> dict[str, Any] | None:
    if isinstance(candidate, Mapping):
        command = _normalize_verification_command(candidate)
        if not command and candidate.get("raw"):
            return _parse_shell_verification_spec(
                coerce_str(candidate.get("raw")),
                project_root=project_root,
                default_cwd=default_cwd,
            )
        if not command:
            return None
        cwd = _resolve_cwd(candidate.get("cwd"), project_root=project_root, default_cwd=default_cwd)
        env = _normalize_env_mapping(candidate.get("env"))
        return {
            "command": command,
            "command_display": coerce_str(candidate.get("command_display")).strip() or _command_display(command),
            "cwd": str(cwd),
            "env": env,
            "source": coerce_str(candidate.get("source")).strip() or "mapping",
        }
    if isinstance(candidate, str):
        parsed = _parse_shell_verification_spec(candidate, project_root=project_root, default_cwd=default_cwd)
        if parsed is not None:
            return parsed
    command = _normalize_verification_command(candidate)
    if not command:
        return None
    return {
        "command": command,
        "command_display": _command_display(command),
        "cwd": str(default_cwd),
        "env": {},
        "source": "argv",
    }


def _verification_specs(
    design_contract: Mapping[str, Any],
    *,
    project_root: Path,
    doc_root: Path,
) -> list[dict[str, Any]]:
    if os.environ.get("HARNESS_VERIFICATION_SUBPROCESS"):
        return _default_verification_specs(project_root=HARNESS_ROOT, doc_root=HARNESS_ROOT)

    raw_expectation = design_contract.get("verification_expectation", [])
    if isinstance(raw_expectation, (str, bytes, bytearray)):
        candidates: Sequence[Any] = [raw_expectation]
    elif isinstance(raw_expectation, Sequence):
        candidates = raw_expectation
    else:
        candidates = []

    default_cwd = _resolve_cwd(design_contract.get("project_root"), project_root=project_root, default_cwd=project_root)
    specs = [
        _normalize_verification_spec(candidate, project_root=project_root, default_cwd=default_cwd)
        for candidate in candidates
    ]
    normalized_specs = [spec for spec in specs if spec]
    if normalized_specs:
        return normalized_specs
    return _default_verification_specs(project_root=project_root, doc_root=doc_root)


def _run_verification_command(spec: Mapping[str, Any]) -> dict[str, Any]:
    command = [str(item) for item in spec.get("command", [])]
    cwd = Path(coerce_str(spec.get("cwd"))).resolve()
    env_overrides = _normalize_env_mapping(spec.get("env"))
    started_at = utc_now()
    try:
        child_env = dict(os.environ)
        child_env["HARNESS_VERIFICATION_SUBPROCESS"] = "1"
        child_env.update(env_overrides)
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            env=child_env,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except Exception as exc:  # pragma: no cover - defensive, recorded in artifact
        returncode = -1
        stdout = ""
        stderr = f"{exc.__class__.__name__}: {exc}"
    return {
        "command": command,
        "command_display": coerce_str(spec.get("command_display")).strip() or _command_display(command),
        "cwd": str(cwd),
        "env": env_overrides,
        "source": coerce_str(spec.get("source")).strip(),
        "started_at": started_at,
        "completed_at": utc_now(),
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _count_sequence_items(value: Any) -> int:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value)
    return 0


def _verification_acceptance_from_runs(
    runs: Sequence[Mapping[str, Any]],
    *,
    expected_count: int = 0,
) -> tuple[bool, list[str]]:
    findings: list[str] = []
    if not runs:
        return False, ["Execution did not record any verification runs."]

    if expected_count and len(runs) < expected_count:
        findings.append(
            f"Execution recorded {len(runs)} verification run(s) but expected {expected_count}."
        )

    for run in runs:
        command = run.get("command_display") or run.get("command") or []
        returncode = run.get("returncode")
        if returncode != 0:
            findings.append(f"Verification command {command!r} returned {returncode}.")

    return not findings, findings


def _parse_utc(text: Any) -> datetime | None:
    raw = coerce_str(text).strip()
    if not raw:
        return None
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _normalize_text_list(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [coerce_str(item).strip() for item in value if coerce_str(item).strip()]
    text = coerce_str(value).strip()
    return [text] if text else []


def _normalize_option_items(value: Any) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return options
    for item in value:
        if isinstance(item, Mapping):
            label = coerce_str(item.get("label") or item.get("value")).strip()
            option_value = coerce_str(item.get("value") or label).strip()
            description = coerce_str(item.get("description")).strip()
        else:
            label = coerce_str(item).strip()
            option_value = label
            description = ""
        if label and option_value:
            options.append({"label": label, "value": option_value, "description": description})
    return options


def _unique_texts(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for item in values:
        text = coerce_str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _parse_supervisor_choice(answer_text: str, options: Sequence[Mapping[str, Any]]) -> str:
    lines = [line.strip() for line in coerce_str(answer_text).splitlines() if line.strip()]
    if not lines:
        return ""
    first_line = re.sub(r"^[>\-\*\d\.\)\s]+", "", lines[0]).strip().lower()
    for option in options:
        value = coerce_str(option.get("value")).strip().lower()
        label = coerce_str(option.get("label")).strip().lower()
        candidates = [item for item in (value, label) if item]
        for candidate in candidates:
            if (
                first_line == candidate
                or first_line.startswith(candidate + " ")
                or first_line.startswith(candidate + ":")
                or first_line.startswith(candidate + "-")
            ):
                return value or label
    for option in options:
        value = coerce_str(option.get("value")).strip().lower()
        if value and re.search(rf"\b{re.escape(value)}\b", first_line):
            return value
    return ""


def _answer_constraints(answer_text: str, *, choice: str) -> list[str]:
    constraints: list[str] = []
    for index, raw_line in enumerate(coerce_str(answer_text).splitlines()):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if index == 0 and choice:
            normalized = re.sub(r"^[>\-\*\d\.\)\s]+", "", stripped).strip()
            lowered = normalized.lower()
            if (
                lowered == choice
                or lowered.startswith(choice + " ")
                or lowered.startswith(choice + ":")
                or lowered.startswith(choice + "-")
            ):
                remainder = normalized[len(choice) :].lstrip(" :-\t")
                if remainder:
                    constraints.append(remainder)
                continue
        constraints.append(stripped)
    return _unique_texts(constraints)


def _supervisor_decision_from_answer(
    answer_payload: Mapping[str, Any],
    brief: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(brief, Mapping) or not brief:
        return None
    options = _normalize_option_items(brief.get("options"))
    answer_text = coerce_str(answer_payload.get("answer") or answer_payload.get("body")).strip()
    if not answer_text:
        return None
    current_context = brief.get("current_context", {})
    if not isinstance(current_context, Mapping):
        current_context = {}
    choice = _parse_supervisor_choice(answer_text, options)
    return {
        "decision_id": _new_id("supervisor-decision"),
        "source_decision_id": coerce_str(brief.get("decision_id")).strip(),
        "gate_id": coerce_str(answer_payload.get("gate_id") or answer_payload.get("question_id")).strip(),
        "answer_id": coerce_str(answer_payload.get("id")).strip(),
        "title": coerce_str(brief.get("title")).strip(),
        "question": coerce_str(brief.get("question")).strip(),
        "choice": choice,
        "raw_answer": answer_text,
        "constraints": _answer_constraints(answer_text, choice=choice),
        "blocked_agent": coerce_str(brief.get("blocked_agent")).strip(),
        "source_ref": coerce_str(brief.get("source_ref")).strip(),
        "options": options,
        "current_context": dict(current_context),
        "created_at": utc_now(),
    }


def _verification_spec_identifier(spec: Any) -> str:
    if isinstance(spec, Mapping):
        command_display = coerce_str(spec.get("command_display")).strip()
        cwd = coerce_str(spec.get("cwd")).strip()
        command = _normalize_verification_command(spec)
        return " | ".join(item for item in (command_display, cwd, _command_display(command)) if item).lower()
    if isinstance(spec, str):
        return spec.lower()
    return json.dumps(spec, ensure_ascii=False, sort_keys=True).lower()


def _apply_verification_constraints(
    verification_expectation: Sequence[Any],
    constraints: Sequence[str],
) -> tuple[list[Any], list[Any]]:
    blocking = list(verification_expectation)
    advisory: list[Any] = []
    constraint_text = "\n".join(_normalize_text_list(constraints)).lower()
    soft_block_markers = (
        "ignore",
        "not block",
        "non-blocking",
        "do not block",
        "暂时忽略",
        "不阻塞",
        "既有失败",
        "直接相关",
    )
    if "engineer/access" in constraint_text and any(marker in constraint_text for marker in soft_block_markers):
        for spec in list(blocking):
            identifier = _verification_spec_identifier(spec)
            if "engineer/access" in identifier or "src/engineer/access" in identifier:
                advisory.append(spec)
                blocking.remove(spec)
    return (blocking or list(verification_expectation)), advisory


def _target_paths_from_findings(findings: Sequence[Any]) -> list[str]:
    targets: list[str] = []
    for item in findings:
        text = coerce_str(item).strip().lower()
        if not text:
            continue
        if "tests/test_center_alembic_from_repo_root.py" in text:
            targets.append("tests/test_center_alembic_from_repo_root.py")
        if "src/center" in text:
            targets.append("src/center/**")
        if "engineer/access" in text:
            targets.append("src/engineer/access/**")
    return _unique_texts(targets)


def _contract_for_supervisor_decision(
    base_contract: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    contract = dict(base_contract)
    context = decision.get("current_context", {})
    if not isinstance(context, Mapping):
        context = {}
    referenced_contract = context.get("design_contract", {})
    if isinstance(referenced_contract, Mapping) and referenced_contract:
        contract.update(dict(referenced_contract))

    selected_phase = contract.get("selected_phase", {})
    if not isinstance(selected_phase, Mapping):
        selected_phase = {}
    origin_phase_title = coerce_str(contract.get("origin_phase_title")).strip()
    phase_title = origin_phase_title or coerce_str(selected_phase.get("title")).strip() or "the current slice"
    choice = coerce_str(decision.get("choice")).strip().lower()
    constraints = _normalize_text_list(decision.get("constraints", []))
    findings = _normalize_text_list(context.get("findings", []))
    verification_expectation = list(contract.get("verification_expectation", []))
    blocking_expectation, advisory_expectation = _apply_verification_constraints(
        verification_expectation,
        constraints,
    )
    target_paths = _unique_texts(
        _normalize_text_list(contract.get("target_paths", [])) + _target_paths_from_findings(findings)
    )
    supervisor_note = {
        "choice": choice or "unspecified",
        "raw_answer": coerce_str(decision.get("raw_answer")).strip(),
        "constraints": constraints,
        "source_ref": coerce_str(decision.get("source_ref")).strip(),
    }

    if choice == "replan":
        work_items = constraints or [
            f"Unblock repeated verification issue: {finding}" for finding in findings[:5]
        ]
        if not work_items:
            work_items = [f"Unblock {phase_title} before resuming the original slice."]
        acceptance_criteria = _unique_texts(
            [
                "Produce concrete unblocker evidence instead of repeating the same verification failures.",
                f"Leave the harness ready to resume {phase_title}.",
            ]
            + constraints
        )
        contract.update(
            {
                "selected_phase": {
                    "title": f"Blocker slice: unblock {phase_title}",
                    "goals": work_items,
                    "file_targets": target_paths,
                    "done_criteria": acceptance_criteria,
                },
                "slice_key": f"{coerce_str(contract.get('slice_key')).strip() or 'supervisor'}::blocker::{coerce_str(decision.get('decision_id')).strip()}",
                "proposed_slice": f"Unblock {phase_title} under {coerce_str(contract.get('project_root')).strip() or 'the target project'} before resuming the main slice.",
                "work_items": work_items,
                "target_paths": target_paths,
                "acceptance_criteria": acceptance_criteria,
                "verification_expectation": blocking_expectation,
                "advisory_verification_expectation": advisory_expectation,
                "human_constraints": constraints,
                "supervisor_decision": supervisor_note,
                "origin_phase_title": phase_title,
                "is_blocker_slice": True,
                "work_status": "ready",
            }
        )
        return contract

    if choice == "continue":
        work_items = _unique_texts(_normalize_text_list(contract.get("work_items", [])) + constraints)
        acceptance_criteria = _unique_texts(_normalize_text_list(contract.get("acceptance_criteria", [])) + constraints)
        contract.update(
            {
                "proposed_slice": f"Continue {phase_title} under explicit human constraints in {coerce_str(contract.get('project_root')).strip() or 'the target project'}.",
                "work_items": work_items,
                "acceptance_criteria": acceptance_criteria,
                "verification_expectation": blocking_expectation,
                "advisory_verification_expectation": advisory_expectation,
                "human_constraints": constraints,
                "supervisor_decision": supervisor_note,
                "origin_phase_title": phase_title,
                "is_blocker_slice": coerce_bool(contract.get("is_blocker_slice"), False),
                "work_status": "ready",
            }
        )
    return contract


def _is_blocker_slice(design_contract: Mapping[str, Any]) -> bool:
    if coerce_bool(design_contract.get("is_blocker_slice"), False):
        return True
    selected_phase = design_contract.get("selected_phase", {})
    if not isinstance(selected_phase, Mapping):
        selected_phase = {}
    title = coerce_str(selected_phase.get("title")).strip().lower()
    return title.startswith("blocker slice:")


def _available_doc_paths(doc_bundle: Mapping[str, Any]) -> list[str]:
    docs = doc_bundle.get("docs", [])
    if not isinstance(docs, list):
        return []
    paths: list[str] = []
    for record in docs:
        if not isinstance(record, Mapping):
            continue
        relative_path = coerce_str(record.get("relative_path")).strip()
        if relative_path:
            paths.append(relative_path)
    return paths


def _preferred_baseline_docs(doc_bundle: Mapping[str, Any]) -> list[str]:
    available = set(_available_doc_paths(doc_bundle))
    preferred = [path for path in ARCHITECTURE_BASELINE_DOCS if path in available]
    if preferred:
        return preferred

    ranked: list[str] = []
    for path in available:
        lowered = path.lower()
        score = 0
        if lowered.startswith("designs/") or "/designs/" in lowered:
            score += 100
        if "task-centered-autonomous-ops-platform" in lowered:
            score += 80
        if "harness-engineering-integration" in lowered:
            score += 60
        if "architecture" in lowered or "design" in lowered:
            score += 25
        if score:
            ranked.append(f"{score:03d}:{path}")
    if ranked:
        ranked.sort(reverse=True)
        return [item.split(":", 1)[1] for item in ranked[:3]]
    return []


def _preferred_planning_doc(doc_bundle: Mapping[str, Any]) -> str:
    docs = doc_bundle.get("docs", [])
    if not isinstance(docs, list):
        docs = []
    for preferred in ARCHITECTURE_BASELINE_DOCS:
        if preferred.startswith("plans/"):
            for record in docs:
                if not isinstance(record, Mapping):
                    continue
                if coerce_str(record.get("relative_path")).strip() == preferred:
                    return preferred
    ranked_paths: list[str] = []
    for record in docs:
        if not isinstance(record, Mapping):
            continue
        relative_path = coerce_str(record.get("relative_path")).strip()
        lowered = relative_path.lower()
        if not relative_path:
            continue
        score = 0
        if lowered.startswith("plans/") or "/plans/" in lowered:
            score += 100
        if "task-mainline" in lowered or "mainline" in lowered:
            score += 50
        if "plan" in lowered:
            score += 25
        if score:
            ranked_paths.append(f"{score:03d}:{relative_path}")
    if ranked_paths:
        ranked_paths.sort(reverse=True)
        return ranked_paths[0].split(":", 1)[1]
    baseline_docs = _preferred_baseline_docs(doc_bundle)
    if baseline_docs:
        return baseline_docs[-1]
    return ""


def _resolve_doc_path(doc_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return candidate
    return (doc_root / candidate).resolve()


def _read_doc_text(doc_root: Path, relative_path: str) -> str:
    if not relative_path:
        return ""
    path = _resolve_doc_path(doc_root, relative_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _extract_phase_plans(text: str) -> list[dict[str, Any]]:
    phases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_section = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("### "):
            if current:
                phases.append(current)
            current = {
                "title": stripped[4:].strip(),
                "goals": [],
                "file_targets": [],
                "done_criteria": [],
            }
            current_section = ""
            continue
        if current is None:
            continue
        lowered = stripped.rstrip(":").lower()
        if lowered == "goals":
            current_section = "goals"
            continue
        if lowered == "file targets":
            current_section = "file_targets"
            continue
        if lowered == "done criteria":
            current_section = "done_criteria"
            continue
        if current_section and stripped.startswith("- "):
            current[current_section].append(stripped[2:].strip())
            continue
        if current_section and stripped and not stripped.startswith("#") and current[current_section]:
            current[current_section][-1] = f"{current[current_section][-1]} {stripped}"
    if current:
        phases.append(current)
    return phases


def _phase_priority(phase: Mapping[str, Any]) -> tuple[int, int]:
    title = coerce_str(phase.get("title")).lower()
    targets = [coerce_str(item).lower() for item in phase.get("file_targets", [])]
    score = 0
    if any(target.startswith("src/") or target.startswith("tests/") for target in targets):
        score += 100
    if any("src/center" in target for target in targets):
        score += 50
    if "phase 2" in title:
        score += 25
    if "replace" in title or "wire" in title:
        score += 10
    return score, -len(targets)


def _slice_key(doc_path: str, phase_title: str) -> str:
    doc_text = coerce_str(doc_path).strip()
    title_text = coerce_str(phase_title).strip()
    if not doc_text or not title_text:
        return ""
    return f"{doc_text}::{title_text.lower()}"


def _completed_slice_keys(completed_slices: Sequence[Any]) -> set[str]:
    keys: set[str] = set()
    for item in completed_slices:
        if not isinstance(item, Mapping):
            continue
        explicit = coerce_str(item.get("slice_key")).strip()
        if explicit:
            keys.add(explicit)
            continue
        derived = _slice_key(
            coerce_str(item.get("selected_planning_doc") or item.get("doc_path")).strip(),
            coerce_str(item.get("phase_title") or item.get("title")).strip(),
        )
        if derived:
            keys.add(derived)
    return keys


def _select_active_phase(
    phases: Sequence[Mapping[str, Any]],
    *,
    planning_doc: str,
    completed_slices: Sequence[Any],
) -> dict[str, Any]:
    if not phases:
        return {"title": "", "goals": [], "file_targets": [], "done_criteria": []}
    completed_keys = _completed_slice_keys(completed_slices)
    remaining = [
        dict(phase)
        for phase in phases
        if _slice_key(planning_doc, coerce_str(phase.get("title")).strip()) not in completed_keys
    ]
    if not remaining:
        return {"title": "", "goals": [], "file_targets": [], "done_criteria": []}
    ranked = sorted(remaining, key=_phase_priority, reverse=True)
    return ranked[0]


def _verification_section(text: str) -> str:
    match = re.search(r"^##\s+Verification\b", text, flags=re.MULTILINE)
    if not match:
        return ""
    tail = text[match.end() :]
    next_section = re.search(r"^##\s+", tail, flags=re.MULTILINE)
    if next_section:
        tail = tail[: next_section.start()]
    return tail


def _verification_expectation_from_text(text: str, *, project_root: Path, doc_root: Path) -> list[dict[str, Any]]:
    section = _verification_section(text)
    if not section:
        return _default_verification_specs(project_root=project_root, doc_root=doc_root)
    blocks = re.findall(r"```(?:bash|sh|shell)?\s*(.*?)```", section, flags=re.DOTALL | re.IGNORECASE)
    specs: list[dict[str, Any]] = []
    for block in blocks:
        for raw_line in block.splitlines():
            command_text = raw_line.strip()
            if not command_text:
                continue
            parsed = _parse_shell_verification_spec(
                command_text,
                project_root=project_root,
                default_cwd=project_root,
            )
            if parsed is not None:
                specs.append(parsed)
    return specs or _default_verification_specs(project_root=project_root, doc_root=doc_root)


def _find_codex_executable() -> str:
    for name in CODEX_EXECUTABLE_NAMES:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return ""


def _git_status_snapshot(project_root: Path) -> dict[str, Any]:
    command = ["git", "status", "--short"]
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "ok": False,
            "command": command,
            "cwd": str(project_root),
            "returncode": -1,
            "stdout": "",
            "stderr": f"{exc.__class__.__name__}: {exc}",
            "entries": [],
        }
    entries = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "cwd": str(project_root),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "entries": entries,
    }


def _execution_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "summary": {"type": "string"},
            "changed_paths": {"type": "array", "items": {"type": "string"}},
            "verification_notes": {"type": "array", "items": {"type": "string"}},
            "needs_human": {"type": "boolean"},
            "human_question": {"type": "string"},
            "why_not_auto_answered": {"type": "string"},
            "required_reply_shape": {"type": "string"},
            "decision_tags": {"type": "array", "items": {"type": "string"}},
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "value": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["label", "value"],
                    "additionalProperties": False,
                },
            },
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": list(DEFAULT_EXECUTION_OUTPUT.keys()),
        "additionalProperties": False,
    }


def _execution_prompt(
    *,
    project_root: Path,
    design_contract: Mapping[str, Any],
    baseline_docs: Sequence[str],
    planning_doc: str,
    human_decisions: Sequence[Any],
) -> str:
    selected_phase = design_contract.get("selected_phase", {})
    if not isinstance(selected_phase, Mapping):
        selected_phase = {}
    lines = [
        "You are the Harness execution-agent for AIMA-refactor.",
        f"Project root: {project_root}",
        "Read the required baseline docs first and then implement the current slice.",
        "",
        "Required baseline docs:",
    ]
    seen_docs: set[str] = set()
    for path in list(baseline_docs) + ([planning_doc] if planning_doc else []):
        normalized = coerce_str(path).strip()
        if not normalized or normalized in seen_docs:
            continue
        seen_docs.add(normalized)
        lines.append(f"- {path}")
    lines.extend(
        [
            "",
            "Current slice:",
            f"- phase: {coerce_str(selected_phase.get('title')).strip() or 'unspecified active slice'}",
            f"- goal: {coerce_str(design_contract.get('proposed_slice')).strip()}",
        ]
    )
    work_items = _normalize_text_list(design_contract.get("work_items", []))
    if work_items:
        lines.append("- work items:")
        for item in work_items:
            lines.append(f"  - {item}")
    target_paths = _normalize_text_list(design_contract.get("target_paths", []))
    if target_paths:
        lines.append("- target paths:")
        for item in target_paths:
            lines.append(f"  - {item}")
    acceptance = _normalize_text_list(design_contract.get("acceptance_criteria", []))
    if acceptance:
        lines.append("- acceptance criteria:")
        for item in acceptance:
            lines.append(f"  - {item}")
    human_constraints = _normalize_text_list(design_contract.get("human_constraints", []))
    if human_constraints:
        lines.append("- human constraints:")
        for item in human_constraints:
            lines.append(f"  - {item}")
    supervisor_decision = design_contract.get("supervisor_decision", {})
    if isinstance(supervisor_decision, Mapping):
        supervisor_choice = coerce_str(supervisor_decision.get("choice")).strip()
        if supervisor_choice:
            lines.append(f"- supervisor choice: {supervisor_choice}")
    if human_decisions:
        lines.append("- prior human decisions:")
        for item in human_decisions[-5:]:
            if isinstance(item, Mapping):
                body = coerce_str(item.get("body") or item.get("answer")).strip()
                if body:
                    lines.append(f"  - {body}")
    lines.extend(
        [
            "",
            "Execution rules:",
            "- Use subagents for code modification work whenever the implementation can be decomposed safely.",
            "- Modify the repository directly under the project root before claiming progress.",
            "- Do not drift back into harness self-tests unless the current slice explicitly targets harness-engineering paths.",
            "- Follow the repository AGENTS/architecture guidance and implement the mainline directly. Do not add fallback code, compatibility shims, or duplicate paths unless the docs explicitly require it.",
            "- Run any targeted local checks you need for confidence, but the harness will run the required verification commands after you return.",
            "- Only request human input for a real decision gate. Ordinary blockers must be handled autonomously.",
            "- If you truly need a human decision, finish as much analysis as you can first and set needs_human=true in the final JSON.",
            "",
            "Return only JSON that matches the provided schema.",
        ]
    )
    return "\n".join(lines)


def _run_execution_subagent(
    *,
    project_root: Path,
    design_contract: Mapping[str, Any],
    baseline_docs: Sequence[str],
    planning_doc: str,
    human_decisions: Sequence[Any],
    request_path: Path,
    result_path: Path,
    launcher_state_path: Path,
    launcher_run_path: Path,
) -> dict[str, Any]:
    codex_executable = _find_codex_executable()
    started_at = utc_now()
    prompt = _execution_prompt(
        project_root=project_root,
        design_contract=design_contract,
        baseline_docs=baseline_docs,
        planning_doc=planning_doc,
        human_decisions=human_decisions,
    )
    schema_path = request_path.with_name(request_path.stem + "-schema.json")
    output_path = result_path.with_suffix(".message.json")
    request_payload = {
        "project_root": str(project_root),
        "baseline_docs": list(baseline_docs),
        "planning_doc": planning_doc,
        "design_contract": dict(design_contract),
        "prompt": prompt,
        "codex_executable": codex_executable,
        "schema_path": str(schema_path),
        "output_path": str(output_path),
        "recorded_at": started_at,
    }
    _write_json(request_path, request_payload)
    launcher_state_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_run_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        launcher_state_path,
        {
            "status": "running",
            "active_run_id": launcher_run_path.stem,
            "last_request_path": str(request_path),
            "last_result_path": str(result_path),
            "last_cycle_id": request_path.parent.name,
            "started_at": started_at,
        },
    )
    if not codex_executable:
        payload = {
            "ok": False,
            "exit_code": -1,
            "started_at": started_at,
            "completed_at": utc_now(),
            "stdout": "",
            "stderr": "codex executable was not found on PATH",
            "parsed_output": dict(DEFAULT_EXECUTION_OUTPUT),
            "pre_git_status": _git_status_snapshot(project_root),
            "post_git_status": _git_status_snapshot(project_root),
            "command": [],
        }
        _write_json(result_path, payload)
        _write_json(
            launcher_state_path,
            {
                "status": "failed",
                "active_run_id": "",
                "last_request_path": str(request_path),
                "last_result_path": str(result_path),
                "last_exit_code": -1,
                "completed_at": payload["completed_at"],
            },
        )
        _write_json(launcher_run_path, payload)
        return payload

    schema_path.write_text(
        json.dumps(_execution_output_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    pre_git_status = _git_status_snapshot(project_root)
    command = [
        codex_executable,
        "exec",
        prompt,
        "-C",
        str(project_root),
        "--dangerously-bypass-approvals-and-sandbox",
        "--output-schema",
        str(schema_path),
        "-o",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    parsed_output = dict(DEFAULT_EXECUTION_OUTPUT)
    if output_path.exists():
        try:
            loaded = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(loaded, Mapping):
                parsed_output.update(dict(loaded))
        except json.JSONDecodeError:
            parsed_output["notes"] = list(parsed_output.get("notes", [])) + [
                f"Failed to parse {output_path.name} as JSON.",
            ]
    post_git_status = _git_status_snapshot(project_root)
    payload = {
        "ok": completed.returncode == 0,
        "command": command,
        "cwd": str(project_root),
        "exit_code": completed.returncode,
        "started_at": started_at,
        "completed_at": utc_now(),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "parsed_output": parsed_output,
        "pre_git_status": pre_git_status,
        "post_git_status": post_git_status,
    }
    _write_json(result_path, payload)
    _write_json(launcher_run_path, payload)
    _write_json(
        launcher_state_path,
        {
            "status": "completed" if payload["ok"] else "failed",
            "active_run_id": "",
            "last_request_path": str(request_path),
            "last_result_path": str(result_path),
            "last_exit_code": completed.returncode,
            "completed_at": payload["completed_at"],
        },
    )
    return payload


def _design_contract_from_docs(
    *,
    doc_root: Path,
    project_root: Path,
    doc_bundle: Mapping[str, Any],
    selected_primary_doc: str,
    maintenance_findings: Sequence[Any],
    completed_slices: Sequence[Any],
) -> dict[str, Any]:
    planning_doc = _preferred_planning_doc(doc_bundle)
    baseline_docs = _preferred_baseline_docs(doc_bundle)
    doc_path = selected_primary_doc or (baseline_docs[0] if baseline_docs else planning_doc)
    planning_text = _read_doc_text(doc_root, planning_doc)
    doc_text = planning_text or _read_doc_text(doc_root, doc_path)
    phases = _extract_phase_plans(doc_text)
    selected_phase = _select_active_phase(
        phases,
        planning_doc=planning_doc,
        completed_slices=completed_slices,
    )
    verification_expectation = _verification_expectation_from_text(
        planning_text or doc_text,
        project_root=project_root,
        doc_root=doc_root,
    )
    execution_scope = "harness_internal" if same_path(project_root, HARNESS_ROOT) else "external_project"
    file_targets = [
        coerce_str(item).strip()
        for item in selected_phase.get("file_targets", [])
        if coerce_str(item).strip()
    ]
    goals = [
        coerce_str(item).strip()
        for item in selected_phase.get("goals", [])
        if coerce_str(item).strip()
    ]
    done_criteria = [
        coerce_str(item).strip()
        for item in selected_phase.get("done_criteria", [])
        if coerce_str(item).strip()
    ]
    if not goals:
        goals = [
            "Read the selected planning docs and advance the current repository slice without drifting back into harness self-tests.",
        ]
    if not done_criteria:
        done_criteria = [
            "Verification runs against the target project root rather than the harness repository root.",
        ]
    completed_slice_keys = _completed_slice_keys(completed_slices)
    selected_phase_title = coerce_str(selected_phase.get("title")).strip()
    slice_key = _slice_key(planning_doc, selected_phase_title)
    if not slice_key:
        generic_doc = planning_doc or doc_path or "docs"
        slice_key = f"{generic_doc}::generic"
    work_status = "ready"
    if phases and not selected_phase_title:
        work_status = "completed"
    elif not phases and slice_key in completed_slice_keys:
        work_status = "completed"
    contract = {
        "goal": doc_bundle.get("summary", ""),
        "doc_summary": doc_bundle.get("summary", ""),
        "doc_count": doc_bundle.get("doc_count", 0),
        "doc_root": str(doc_root),
        "project_root": str(project_root),
        "execution_scope": execution_scope,
        "selected_primary_doc": doc_path,
        "selected_planning_doc": planning_doc,
        "baseline_docs": baseline_docs,
        "selected_phase": selected_phase,
        "slice_key": slice_key,
        "work_status": work_status,
        "remaining_phase_count": sum(
            1
            for phase in phases
            if _slice_key(planning_doc, coerce_str(phase.get("title")).strip())
            not in _completed_slice_keys(completed_slices)
        ),
        "proposed_slice": (
            f"Advance {selected_phase.get('title') or 'the active slice'} under {project_root}."
            if work_status == "ready"
            else "No remaining planned phases are left in the current planning document."
        ),
        "work_items": goals,
        "target_paths": file_targets,
        "acceptance_criteria": done_criteria,
        "verification_expectation": verification_expectation,
        "maintenance_findings": list(maintenance_findings),
    }
    return contract


def _verification_scope_findings(
    design_contract: Mapping[str, Any],
    verification_runs: Sequence[Mapping[str, Any]],
) -> list[str]:
    findings: list[str] = []
    execution_scope = coerce_str(design_contract.get("execution_scope")).strip()
    project_root_text = coerce_str(design_contract.get("project_root")).strip()
    if not execution_scope or not project_root_text:
        return findings
    project_root = Path(project_root_text)
    for run in verification_runs:
        command_display = coerce_str(run.get("command_display")).strip()
        cwd_text = coerce_str(run.get("cwd")).strip()
        if not cwd_text:
            findings.append("Execution recorded a verification run without a cwd.")
            continue
        cwd = Path(cwd_text)
        if execution_scope == "external_project":
            if same_path(cwd, HARNESS_ROOT):
                findings.append(
                    "Execution verified the harness repository instead of the target project root."
                )
            elif not path_within(cwd, project_root):
                findings.append(
                    f"Verification cwd {cwd} is outside the target project root {project_root}."
                )
        if "tests.test_runtime_files" in command_display:
            findings.append("Execution fell back to harness-only runtime file tests.")
    return findings


def _audit_failure_signature(report: Mapping[str, Any]) -> str:
    design_contract = report.get("design_contract", {})
    if not isinstance(design_contract, Mapping):
        design_contract = {}
    selected_phase = design_contract.get("selected_phase", {})
    if not isinstance(selected_phase, Mapping):
        selected_phase = {}
    payload = {
        "selected_primary_doc": coerce_str(design_contract.get("selected_primary_doc")).strip(),
        "selected_phase": coerce_str(selected_phase.get("title")).strip(),
        "verification_commands": report.get("verification_commands", []),
        "findings": _normalize_text_list(report.get("findings", [])),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _cleanup_runtime_temp_files(runtime_root: Path) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for path in sorted(runtime_root.rglob("*.tmp-*")):
        try:
            path.unlink(missing_ok=True)
            actions.append({"path": str(path), "action": "removed"})
        except OSError as exc:  # pragma: no cover - defensive
            actions.append({"path": str(path), "action": f"failed: {exc.__class__.__name__}: {exc}"})
    return actions


def _project_hygiene_findings(project_root: Path, limit: int = 40) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    skip_dirs = {".git", ".venv", "node_modules"}
    for root, dirs, files in os.walk(project_root):
        current_root = Path(root)
        dirs[:] = [item for item in dirs if item not in skip_dirs]
        for name in list(dirs):
            if name in {"__pycache__", ".pytest_cache"}:
                findings.append(
                    {
                        "kind": "generated_directory",
                        "path": str((current_root / name).resolve()),
                    }
                )
                if len(findings) >= limit:
                    return findings
        for name in files:
            if name.endswith((".pyc", ".pyo", ".tmp")):
                findings.append(
                    {
                        "kind": "generated_file",
                        "path": str((current_root / name).resolve()),
                    }
                )
                if len(findings) >= limit:
                    return findings
    return findings


class HarnessScheduler:
    def __init__(
        self,
        *,
        specs: Sequence[Any],
        paths: RuntimePaths,
        mission: Mission,
        state: RuntimeState,
    ) -> None:
        ordered_specs = sorted((_spec_mapping(spec) for spec in specs), key=lambda item: (item["order"], item["id"]))
        self.specs = ordered_specs
        self.specs_by_id = {spec["id"]: spec for spec in ordered_specs}
        self.paths = paths
        self.mission = mission
        self.state = state
        self.communication_store = CommunicationStore(paths.harness_root)
        self.runner = RunnerBridge(
            paths.harness_root,
            communication_store=self.communication_store,
            turn_executor=self._execute_turn,
        )
        self.communication_agent_id = "communication" if "communication" in self.specs_by_id else None
        self.design_agent_id = "design" if "design" in self.specs_by_id else None
        self.execution_agent_id = "execution" if "execution" in self.specs_by_id else None
        self.audit_agent_id = "audit" if "audit" in self.specs_by_id else None
        self.cleanup_agent_id = "cleanup" if "cleanup" in self.specs_by_id else None
        self._refresh_doc_bundle()
        self._ensure_runtime_defaults()
        self._save_runtime()

    def _refresh_doc_bundle(self) -> None:
        bundle = build_doc_bundle(self.mission.doc_root)
        self.mission.extra["doc_bundle"] = bundle
        self.mission.extra["doc_count"] = bundle["doc_count"]
        self.mission.extra["doc_digest"] = bundle["doc_digest"]
        self.mission.extra["primary_docs"] = bundle["primary_docs"]
        if not self.mission.goal:
            self.mission.goal = bundle["summary"] or f"Process docs under {self.mission.doc_root}"

    def _resume_for_doc_change(self, previous_digest: str) -> None:
        current_digest = coerce_str(self.mission.extra.get("doc_digest")).strip()
        if not previous_digest or not current_digest or previous_digest == current_digest:
            return
        if self._runtime_status() not in {"completed", "failed"}:
            return
        if self._communication_side_channel_pending():
            return
        self.state.active_agent = self.design_agent_id or self._default_work_entry_agent()
        self.mission.status = "active"
        self._set_runtime_status("running")
        self.state.extra.pop("failure_reason", None)
        self.state.extra.pop("last_failure_findings", None)
        self._save_runtime()

    def _ensure_runtime_defaults(self) -> None:
        self.mission.extra.setdefault("human_decisions", [])
        self.mission.extra.setdefault("auto_answers", {})
        self.mission.extra.setdefault("maintenance_findings", [])
        self.mission.extra.setdefault("completed_slices", [])
        self.mission.extra.setdefault("supervisor_decisions", [])
        legacy_pending_supervisor_decision = self.state.extra.pop("pending_supervisor_decision", None)
        if legacy_pending_supervisor_decision and "pending_supervisor_decision" not in self.mission.extra:
            self.mission.extra["pending_supervisor_decision"] = legacy_pending_supervisor_decision
        self.mission.extra.setdefault("project_root", str(project_root_from_doc_root(self.mission.doc_root)))
        raw_tags = self.mission.extra.get("decision_gate_tags", [])
        if not isinstance(raw_tags, list):
            raw_tags = list(raw_tags) if isinstance(raw_tags, (tuple, set, frozenset)) else _normalize_text_list(raw_tags)
        self.mission.extra["decision_gate_tags"] = [coerce_str(tag).strip() for tag in raw_tags if coerce_str(tag).strip()]
        interval = coerce_int(
            self.mission.extra.get("cleanup_maintenance_interval_seconds"),
            DEFAULT_CLEANUP_MAINTENANCE_INTERVAL_SECONDS,
        )
        self.mission.extra["cleanup_maintenance_interval_seconds"] = max(1, interval)
        self.state.extra.setdefault("status", "running")
        if "last_cleanup_maintenance_at" not in self.state.extra:
            self.state.extra["last_cleanup_maintenance_at"] = self.state.last_run_at or utc_now()
        if self._communication_side_channel_pending() and self.communication_agent_id:
            self.state.active_agent = self.communication_agent_id
        elif self.state.active_agent == self.communication_agent_id and not self._communication_side_channel_pending():
            self.state.active_agent = self._default_work_entry_agent()
        elif self.state.active_agent == self.cleanup_agent_id and not self._cleanup_mode():
            self.state.active_agent = self._default_work_entry_agent()
        elif self.state.active_agent and self.state.active_agent not in self.specs_by_id:
            self.state.extra["recovery_requested"] = True
            self.state.active_agent = self._default_work_entry_agent()
        elif not self.state.active_agent and self._runtime_status() not in {"waiting_human", "completed", "failed"}:
            self.state.active_agent = self._default_work_entry_agent()

    def _save_runtime(self) -> None:
        save_mission(self.paths.memory_root, self.mission)
        save_state(self.paths.memory_root, self.state)

    def _clear_audit_reopen_tracking(self) -> None:
        self.state.extra.pop("last_audit_failure_signature", None)
        self.state.extra.pop("audit_reopen_streak", None)

    def _record_completed_slice(self, design_contract: Mapping[str, Any]) -> None:
        slice_key = coerce_str(design_contract.get("slice_key")).strip()
        if not slice_key:
            return
        selected_phase = design_contract.get("selected_phase", {})
        if not isinstance(selected_phase, Mapping):
            selected_phase = {}
        completed = list(self.mission.extra.get("completed_slices", []))
        if any(
            isinstance(item, Mapping) and coerce_str(item.get("slice_key")).strip() == slice_key
            for item in completed
        ):
            return
        completed.append(
            {
                "slice_key": slice_key,
                "selected_planning_doc": coerce_str(design_contract.get("selected_planning_doc")).strip(),
                "phase_title": coerce_str(selected_phase.get("title")).strip(),
                "completed_at": utc_now(),
            }
        )
        self.mission.extra["completed_slices"] = completed

    def _record_audit_reopen(self, findings: Sequence[Any], *, design_contract: Mapping[str, Any]) -> int:
        signature = json.dumps(
            {
                "execution_scope": coerce_str(design_contract.get("execution_scope")).strip(),
                "project_root": coerce_str(design_contract.get("project_root")).strip(),
                "findings": [coerce_str(item).strip() for item in findings if coerce_str(item).strip()],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        previous_signature = coerce_str(self.state.extra.get("last_audit_failure_signature")).strip()
        previous_streak = coerce_int(self.state.extra.get("audit_reopen_streak"), 0)
        streak = previous_streak + 1 if signature and signature == previous_signature else 1
        self.state.extra["last_audit_failure_signature"] = signature
        self.state.extra["audit_reopen_streak"] = streak
        return streak

    def snapshot(self) -> dict[str, Any]:
        return {
            "runtime_root": str(self.paths.harness_root),
            "mission": self.mission.to_mapping(),
            "state": self.state.to_mapping(),
            "pending_gate_id": coerce_str(self.state.extra.get("pending_gate_id")) or None,
            "runtime_status": self._runtime_status(),
        }

    def run_agent(
        self,
        agent_spec: Any,
        handoff: Mapping[str, Any],
        *,
        mission: Mapping[str, Any] | None = None,
        state: Mapping[str, Any] | None = None,
        runtime_paths: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.runner.run_agent(
            agent_spec,
            handoff,
            mission=mission or self.mission.to_mapping(),
            state=state or self.state.to_mapping(),
            runtime_paths=runtime_paths or {"runtime_root": self.paths.harness_root},
        )

    def _runtime_status(self) -> str:
        return str(self.state.extra.get("status", "running"))

    def _set_runtime_status(self, value: str) -> None:
        self.state.extra["status"] = value

    def _runner_cycle_id(self) -> str:
        return str(self.state.extra.get("cycle_id", "")).strip()

    def _runner_sequence(self) -> int:
        try:
            return int(self.state.extra.get("sequence", 0))
        except (TypeError, ValueError):
            return 0

    def _set_runner_turn_state(self, *, cycle_id: str | None = None, sequence: int | None = None) -> None:
        if cycle_id:
            self.state.extra["cycle_id"] = cycle_id
        else:
            self.state.extra.pop("cycle_id", None)
        if sequence is None:
            self.state.extra.pop("sequence", None)
        else:
            self.state.extra["sequence"] = sequence

    def _default_work_entry_agent(self) -> str:
        for candidate in (self.design_agent_id, self.execution_agent_id, self.audit_agent_id):
            if candidate:
                return candidate
        for spec in self.specs:
            if spec["id"] not in {self.communication_agent_id, self.cleanup_agent_id}:
                return spec["id"]
        return self.specs[0]["id"]

    def _communication_brief(self) -> dict[str, Any] | None:
        payload = self.state.extra.get("communication_brief")
        return dict(payload) if isinstance(payload, Mapping) else None

    def _set_communication_brief(self, payload: Mapping[str, Any] | None) -> None:
        if payload:
            self.state.extra["communication_brief"] = dict(payload)
        else:
            self.state.extra.pop("communication_brief", None)

    def _latest_human_reply(self) -> dict[str, Any] | None:
        payload = self.state.extra.get("latest_human_reply")
        return dict(payload) if isinstance(payload, Mapping) else None

    def _set_latest_human_reply(self, payload: Mapping[str, Any] | None) -> None:
        if payload:
            self.state.extra["latest_human_reply"] = dict(payload)
        else:
            self.state.extra.pop("latest_human_reply", None)

    def _clear_audit_failure_streak(self) -> None:
        self.state.extra.pop("last_audit_failure_signature", None)
        self.state.extra.pop("last_audit_failure_count", None)

    def _record_audit_failure_streak(self, report: Mapping[str, Any]) -> int:
        signature = _audit_failure_signature(report)
        previous_signature = coerce_str(self.state.extra.get("last_audit_failure_signature")).strip()
        previous_count = coerce_int(self.state.extra.get("last_audit_failure_count"), 0)
        next_count = previous_count + 1 if signature and signature == previous_signature else 1
        self.state.extra["last_audit_failure_signature"] = signature
        self.state.extra["last_audit_failure_count"] = next_count
        return next_count

    def _communication_side_channel_pending(self) -> bool:
        return self._communication_brief() is not None or self._latest_human_reply() is not None

    def _cleanup_mode(self) -> str:
        return coerce_str(self.state.extra.get("cleanup_mode")).strip()

    def _resume_after_cleanup(self) -> str:
        return coerce_str(self.state.extra.get("resume_after_cleanup")).strip()

    def _clear_cleanup_request(self) -> None:
        self.state.extra.pop("cleanup_mode", None)
        self.state.extra.pop("resume_after_cleanup", None)
        self.state.extra.pop("cleanup_reason", None)
        self.state.extra.pop("cleanup_resume_status", None)

    def _cleanup_resume_status(self) -> str:
        return coerce_str(self.state.extra.get("cleanup_resume_status")).strip()

    def _maintenance_interval_seconds(self) -> int:
        return max(
            1,
            coerce_int(
                self.mission.extra.get("cleanup_maintenance_interval_seconds"),
                DEFAULT_CLEANUP_MAINTENANCE_INTERVAL_SECONDS,
            ),
        )

    def _maintenance_due(self) -> bool:
        if self._cleanup_mode():
            return False
        last_run = _parse_utc(self.state.extra.get("last_cleanup_maintenance_at")) or _parse_utc(self.state.last_run_at)
        if last_run is None:
            return False
        elapsed_seconds = (datetime.now(timezone.utc) - last_run).total_seconds()
        return elapsed_seconds >= self._maintenance_interval_seconds()

    def _recovery_needed(self) -> bool:
        if coerce_bool(self.state.extra.get("recovery_requested"), False):
            return True
        pending_gate_id = coerce_str(self.state.extra.get("pending_gate_id")).strip()
        if pending_gate_id:
            try:
                self.communication_store.get_gate(pending_gate_id)
            except KeyError:
                return True
        if self._runtime_status() == "running" and not self.state.active_agent and (
            self._runner_cycle_id() or self._runner_sequence() > 0
        ):
            return True
        return False

    def _schedule_cleanup(self, mode: str, *, resume_after: str, reason: str, resume_status: str = "running") -> bool:
        if not self.cleanup_agent_id:
            return False
        self.state.extra["cleanup_mode"] = mode
        self.state.extra["resume_after_cleanup"] = resume_after
        self.state.extra["cleanup_reason"] = reason
        self.state.extra["cleanup_resume_status"] = resume_status
        self.state.active_agent = self.cleanup_agent_id
        self.mission.status = "active"
        self._set_runtime_status("running")
        self._save_runtime()
        return True

    def _prepare_next_agent(self) -> None:
        runtime_status = self._runtime_status()
        if runtime_status == "failed":
            return
        if self.state.active_agent == self.cleanup_agent_id and self._cleanup_mode():
            return
        if self._recovery_needed() and self.cleanup_agent_id:
            resume_after = (
                self.state.active_agent
                or coerce_str(self.state.extra.get("blocked_agent")).strip()
                or self._default_work_entry_agent()
            )
            self._schedule_cleanup(
                "recovery",
                resume_after=resume_after,
                reason="runtime inconsistency detected by supervisor",
                resume_status="running",
            )
            return
        if runtime_status == "waiting_human":
            return
        if self._maintenance_due() and self.cleanup_agent_id:
            resume_after = "" if runtime_status == "completed" else (self.state.active_agent or self._default_work_entry_agent())
            resume_status = "completed" if runtime_status == "completed" else "running"
            self._schedule_cleanup(
                "maintenance",
                resume_after=resume_after,
                reason="maintenance interval elapsed",
                resume_status=resume_status,
            )
            return
        if runtime_status == "completed":
            return
        if self._communication_side_channel_pending():
            if self.communication_agent_id:
                self.state.active_agent = self.communication_agent_id
                self._save_runtime()
            return
        if not self.state.active_agent:
            self.state.active_agent = self._default_work_entry_agent()
            self._save_runtime()

    def _restore_after_cleanup(self, default_resume_after: str) -> None:
        resume_status = self._cleanup_resume_status() or "running"
        resume_after = self._resume_after_cleanup() or default_resume_after
        self._clear_cleanup_request()
        if resume_status == "completed":
            self._complete_mission()
            return
        if resume_status == "waiting_human":
            self.state.active_agent = ""
            self.mission.status = "waiting_human"
            self._set_runtime_status("waiting_human")
            self._save_runtime()
            return
        self.state.active_agent = resume_after
        self.mission.status = "active"
        self._set_runtime_status("running")
        self._save_runtime()

    def _pending_gate_answer(self, gate_id: str) -> dict[str, Any] | None:
        if not gate_id:
            return None
        for path in sorted(self.paths.answers_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("question_id") == gate_id or payload.get("gate_id") == gate_id:
                payload.setdefault("id", path.stem)
                payload.setdefault("answer_path", str(path))
                return payload
        return None

    def _set_pending_supervisor_decision(self, payload: Mapping[str, Any] | None) -> None:
        if payload:
            self.mission.extra["pending_supervisor_decision"] = dict(payload)
        else:
            self.mission.extra.pop("pending_supervisor_decision", None)
        self.state.extra.pop("pending_supervisor_decision", None)

    def _pending_supervisor_decision(self) -> dict[str, Any] | None:
        payload = self.mission.extra.get("pending_supervisor_decision")
        return dict(payload) if isinstance(payload, Mapping) else None

    def _consume_pending_supervisor_decision(self, design_contract: Mapping[str, Any]) -> None:
        pending = self._pending_supervisor_decision()
        if not pending:
            return
        history = list(self.mission.extra.get("supervisor_decisions", []))
        entry = dict(pending)
        selected_phase = design_contract.get("selected_phase", {})
        if not isinstance(selected_phase, Mapping):
            selected_phase = {}
        entry["applied_at"] = utc_now()
        entry["applied_slice_key"] = coerce_str(design_contract.get("slice_key")).strip()
        entry["applied_phase_title"] = coerce_str(selected_phase.get("title")).strip()
        history.append(entry)
        self.mission.extra["supervisor_decisions"] = history
        self._set_pending_supervisor_decision(None)
        self._clear_audit_reopen_tracking()
        self._clear_audit_failure_streak()

    def _resume_if_human_replied(self) -> bool:
        gate_id = coerce_str(self.state.extra.get("pending_gate_id")).strip()
        if not gate_id:
            return False
        answer_payload = self._pending_gate_answer(gate_id)
        if answer_payload is None:
            return False
        applied_answer_id = coerce_str(self.state.extra.get("applied_answer_id")).strip()
        if applied_answer_id and applied_answer_id == answer_payload.get("id"):
            return False
        human_decisions = list(self.mission.extra.get("human_decisions", []))
        human_decisions.append(answer_payload)
        self.mission.extra["human_decisions"] = human_decisions
        pending_decision = _supervisor_decision_from_answer(answer_payload, self._communication_brief())
        if pending_decision is not None:
            self._set_pending_supervisor_decision(pending_decision)
            self._clear_audit_reopen_tracking()
            self._clear_audit_failure_streak()
        self.mission.status = "active"
        self._set_latest_human_reply(answer_payload)
        self.state.extra["resume_agent"] = coerce_str(self.state.extra.get("blocked_agent") or self.design_agent_id)
        self.state.extra["applied_answer_id"] = answer_payload.get("id", "")
        self.state.extra["pending_gate_id"] = ""
        self.state.active_agent = self.communication_agent_id or self.state.extra["resume_agent"]
        self._set_runtime_status("running")
        self._save_runtime()
        return True

    def _latest_reports(self) -> dict[str, str]:
        payload = self.state.extra.get("latest_reports", {})
        return dict(payload) if isinstance(payload, Mapping) else {}

    def _latest_artifacts(self) -> dict[str, list[str]]:
        payload = self.state.extra.get("latest_artifacts", {})
        return {
            key: list(value) if isinstance(value, list) else [str(value)]
            for key, value in dict(payload).items()
        } if isinstance(payload, Mapping) else {}

    def _selected_primary_doc(self) -> str:
        selected = str(self.mission.extra.get("selected_primary_doc", ""))
        if selected:
            return selected
        preferred = _preferred_planning_doc(self.mission.extra.get("doc_bundle", {}))
        if preferred:
            return preferred
        primary_docs = self.mission.extra.get("primary_docs", [])
        if isinstance(primary_docs, list) and primary_docs:
            first = primary_docs[0]
            if isinstance(first, Mapping):
                return str(first.get("relative_path", ""))
        return ""

    def _build_handoff(self, agent_id: str) -> dict[str, Any]:
        inputs: dict[str, Any] = {
            "doc_bundle": self.mission.extra.get("doc_bundle", {}),
            "project_root": self.mission.extra.get("project_root", ""),
            "human_decisions": self.mission.extra.get("human_decisions", []),
            "pending_supervisor_decision": self._pending_supervisor_decision(),
            "selected_primary_doc": self._selected_primary_doc(),
            "completed_slices": self.mission.extra.get("completed_slices", []),
            "latest_reports": self._latest_reports(),
            "latest_artifacts": self._latest_artifacts(),
            "pending_gate_id": self.state.extra.get("pending_gate_id", ""),
            "resume_agent": self.state.extra.get("resume_agent", ""),
            "auto_answers": self.mission.extra.get("auto_answers", {}),
            "maintenance_findings": self.mission.extra.get("maintenance_findings", []),
        }
        if agent_id == self.communication_agent_id:
            inputs["communication_brief"] = self._communication_brief()
            inputs["latest_human_reply"] = self._latest_human_reply()
        if agent_id == self.cleanup_agent_id:
            inputs["cleanup_mode"] = self._cleanup_mode()
            inputs["resume_after_cleanup"] = self._resume_after_cleanup()
            inputs["cleanup_reason"] = self.state.extra.get("cleanup_reason", "")
        return {
            "from": self.state.last_successful_agent or "supervisor",
            "to": agent_id,
            "goal": self.specs_by_id[agent_id]["goal"],
            "done_when": self.specs_by_id[agent_id]["title"] or self.specs_by_id[agent_id]["goal"],
            "inputs": inputs,
        }

    def _artifact_path(self, turn: RunnerTurn, suffix: str) -> Path:
        return self.paths.artifacts_dir / turn.cycle_id / f"{turn.sequence:02d}-{turn.agent_spec['id']}-{suffix}.json"

    def _load_json(self, path_text: str) -> dict[str, Any]:
        return json.loads(Path(path_text).read_text(encoding="utf-8"))

    def _build_communication_brief(self, agent_id: str, question: Question, *, why_not_auto_answered: str) -> dict[str, Any]:
        context = dict(question.context)
        source_ref = ""
        relative_path = coerce_str(context.get("relative_path")).strip()
        line_number = coerce_str(context.get("line_number")).strip()
        if relative_path:
            source_ref = f"{relative_path}:{line_number}" if line_number else relative_path
        options = _normalize_option_items(context.get("options"))
        if not options and isinstance(context.get("candidate_paths"), list):
            options = [
                {
                    "label": coerce_str(item),
                    "value": coerce_str(item),
                    "description": "Use this path as the mainline input.",
                }
                for item in context.get("candidate_paths", [])
                if coerce_str(item).strip()
            ]
        if not options:
            options = [
                {
                    "label": "Continue current mainline",
                    "value": "continue",
                    "description": "Approve the current direction and continue.",
                },
                {
                    "label": "Replan before continue",
                    "value": "replan",
                    "description": "Ask design to revise the current contract first.",
                },
            ]
        tradeoffs = _normalize_text_list(context.get("tradeoffs"))
        agent_positions = context.get("agent_positions")
        if not isinstance(agent_positions, list):
            agent_positions = [{"agent": agent_id, "position": question.question}]
        return {
            "decision_id": question.question_id,
            "title": coerce_str(context.get("title")).strip() or f"{agent_id} needs a decision",
            "question": question.question,
            "severity": question.tags[0] if question.tags else "decision_gate",
            "why_not_auto_answered": why_not_auto_answered,
            "source_ref": source_ref,
            "current_context": context,
            "options": options,
            "tradeoffs": tradeoffs,
            "supervisor_recommendation": coerce_str(
                context.get("supervisor_recommendation"),
                "Reply with a direct decision or a concrete constraint for the blocked agent.",
            ),
            "agent_positions": agent_positions,
            "required_reply_shape": coerce_str(
                context.get("required_reply_shape"),
                "Provide a clear decision, a chosen option, or an explicit constraint.",
            ),
            "blocked_agent": agent_id,
        }

    def _render_communication_prompt(self, brief: Mapping[str, Any]) -> str:
        lines = [coerce_str(brief.get("question")).strip()]
        source_ref = coerce_str(brief.get("source_ref")).strip()
        if source_ref:
            lines.append(f"来源: {source_ref}")
        reason = coerce_str(brief.get("why_not_auto_answered")).strip()
        if reason:
            lines.append(f"需要人工决策的原因: {reason}")
        options = brief.get("options", [])
        if isinstance(options, list) and options:
            lines.append("可选项:")
            for option in options:
                if isinstance(option, Mapping):
                    label = coerce_str(option.get("label") or option.get("value")).strip()
                    description = coerce_str(option.get("description")).strip()
                    if label and description:
                        lines.append(f"- {label}: {description}")
                    elif label:
                        lines.append(f"- {label}")
        tradeoffs = brief.get("tradeoffs", [])
        if isinstance(tradeoffs, list) and tradeoffs:
            lines.append("权衡:")
            for item in tradeoffs:
                text = coerce_str(item).strip()
                if text:
                    lines.append(f"- {text}")
        recommendation = coerce_str(brief.get("supervisor_recommendation")).strip()
        if recommendation:
            lines.append(f"Supervisor 建议: {recommendation}")
        reply_shape = coerce_str(brief.get("required_reply_shape")).strip()
        if reply_shape:
            lines.append(f"请回复: {reply_shape}")
        return "\n".join(item for item in lines if item)

    def _execute_turn(self, turn: RunnerTurn) -> dict[str, Any]:
        agent_id = turn.agent_spec["id"]
        inputs = turn.handoff.get("inputs", {})
        doc_bundle = turn.mission.get("doc_bundle", {})
        latest_artifacts = inputs.get("latest_artifacts", {})
        doc_root = Path(turn.mission.get("doc_root", self.paths.memory_root)).resolve()
        project_root = Path(
            coerce_str(turn.mission.get("project_root") or inputs.get("project_root"))
        ).resolve() if coerce_str(turn.mission.get("project_root") or inputs.get("project_root")).strip() else project_root_from_doc_root(doc_root)

        if agent_id == self.communication_agent_id:
            brief = inputs.get("communication_brief", {})
            latest_human_reply = inputs.get("latest_human_reply", {})
            if isinstance(latest_human_reply, Mapping) and latest_human_reply:
                artifact_path = self._artifact_path(turn, "human-reply")
                _write_json(
                    artifact_path,
                    {
                        "reply": dict(latest_human_reply),
                        "communication_brief": dict(brief) if isinstance(brief, Mapping) else {},
                        "resume_agent": inputs.get("resume_agent", ""),
                        "recorded_at": utc_now(),
                    },
                )
                return {
                    "status": "completed",
                    "summary": "Recorded the human reply and returned control to supervisor.",
                    "communication_action": "reply_recorded",
                    "artifacts": [str(artifact_path)],
                }
            if isinstance(brief, Mapping) and brief:
                prompt = self._render_communication_prompt(brief)
                gate = turn.communication_store.open_gate(
                    title=coerce_str(brief.get("title"), "Decision gate").strip() or "Decision gate",
                    prompt=prompt,
                    source="supervisor",
                    severity=coerce_str(brief.get("severity"), "decision_gate").strip() or "decision_gate",
                    context=json.dumps(dict(brief), ensure_ascii=False),
                )
                artifact_path = self._artifact_path(turn, "gate")
                _write_json(
                    artifact_path,
                    {
                        "gate": gate,
                        "communication_brief": dict(brief),
                        "rendered_prompt": prompt,
                        "created_at": utc_now(),
                    },
                )
                return {
                    "status": "blocked",
                    "summary": f"Opened decision gate {gate['id']}",
                    "gate_id": gate["id"],
                    "communication_action": "gate_opened",
                    "artifacts": [str(artifact_path), str(turn.communication_store.state_file)],
                }
            artifact_path = self._artifact_path(turn, "idle")
            _write_json(
                artifact_path,
                {
                    "summary": "Communication agent had no pending brief or reply to process.",
                    "recorded_at": utc_now(),
                },
            )
            return {
                "status": "completed",
                "summary": "Communication agent had no pending work.",
                "communication_action": "idle",
                "artifacts": [str(artifact_path)],
            }

        if agent_id == self.design_agent_id:
            if int(doc_bundle.get("doc_count", 0)) == 0:
                return {
                    "status": "blocked",
                    "summary": "No UTF-8 docs were discovered under the provided doc root.",
                    "questions": [
                        {
                            "question_id": _new_id("question"),
                            "agent": "design",
                            "question": "当前 doc 主目录下没有可读的总体规划/设计文档，是否需要重新指定文档目录？",
                            "blocking": True,
                            "importance": "high",
                            "tags": ["goal_conflict"],
                            "context": {"doc_root": turn.mission.get("doc_root", "")},
                        }
                    ],
                }
            selected_primary_doc = coerce_str(inputs.get("selected_primary_doc")).strip()
            auto_answers = inputs.get("auto_answers", {})
            if not selected_primary_doc and doc_bundle.get("primary_docs"):
                first_question = next(iter(auto_answers.values()), None)
                if isinstance(first_question, Mapping):
                    selected_primary_doc = coerce_str(first_question.get("answer")).strip()
            if not selected_primary_doc:
                selected_primary_doc = _preferred_planning_doc(doc_bundle)
            gate_signals = doc_bundle.get("gate_signals", [])
            human_decisions = inputs.get("human_decisions", [])
            if gate_signals and not human_decisions:
                gate_signal = gate_signals[0]
                return {
                    "status": "blocked",
                    "summary": "Design detected a decision gate in the planning docs.",
                    "questions": [
                        {
                            "question_id": _new_id("question"),
                            "agent": "design",
                            "question": f"{gate_signal['relative_path']}:{gate_signal['line_number']} 提到了需要决策的事项：{gate_signal['prompt']}",
                            "blocking": True,
                            "importance": "high",
                            "tags": [gate_signal["tag"]],
                            "context": gate_signal,
                        }
                    ],
                }
            if not selected_primary_doc and len(doc_bundle.get("docs", [])) > 1 and not auto_answers:
                candidate_paths = [item["relative_path"] for item in doc_bundle.get("primary_docs", [])[:3]]
                return {
                    "status": "blocked",
                    "summary": "Design needs a primary planning document, but this is an ordinary blocker.",
                    "questions": [
                        {
                            "question_id": _new_id("question"),
                            "agent": "design",
                            "question": "应该优先以哪个规划文档作为当前主线入口？",
                            "blocking": False,
                            "importance": "low",
                            "tags": ["path"],
                            "context": {"candidate_paths": candidate_paths},
                        }
                    ],
                }
            design_contract = _design_contract_from_docs(
                doc_root=doc_root,
                project_root=project_root,
                doc_bundle=doc_bundle if isinstance(doc_bundle, Mapping) else {},
                selected_primary_doc=selected_primary_doc,
                maintenance_findings=_normalize_text_list(inputs.get("maintenance_findings", [])),
                completed_slices=inputs.get("completed_slices", []),
            )
            pending_supervisor_decision = inputs.get("pending_supervisor_decision")
            if isinstance(pending_supervisor_decision, Mapping) and pending_supervisor_decision:
                design_contract = _contract_for_supervisor_decision(design_contract, pending_supervisor_decision)
                self._consume_pending_supervisor_decision(design_contract)
            self.mission.extra["selected_primary_doc"] = design_contract["selected_primary_doc"]
            self.mission.extra["project_root"] = str(project_root)
            artifact_path = self._artifact_path(turn, "contract")
            _write_json(artifact_path, design_contract)
            if coerce_str(design_contract.get("work_status")).strip() == "completed":
                return {
                    "status": "completed",
                    "summary": "Design found no remaining planned slices to execute.",
                    "design_status": "completed",
                    "artifacts": [str(artifact_path)],
                }
            return {
                "status": "completed",
                "summary": f"Prepared the next slice from {doc_bundle.get('doc_count', 0)} document(s).",
                "design_status": "ready",
                "artifacts": [str(artifact_path)],
            }

        if agent_id == self.execution_agent_id:
            design_artifacts = latest_artifacts.get("design", [])
            design_contract = self._load_json(design_artifacts[-1]) if design_artifacts else {}
            execution_project_root = Path(
                coerce_str(design_contract.get("project_root") or project_root)
            ).resolve()
            baseline_docs = _normalize_text_list(design_contract.get("baseline_docs", []))
            planning_doc = coerce_str(design_contract.get("selected_planning_doc")).strip()
            request_artifact_path = self._artifact_path(turn, "codex-request")
            result_artifact_path = self._artifact_path(turn, "codex-result")
            launcher_dir = self.paths.launchers_dir / "codex_exec"
            launcher_state_path = launcher_dir / "state.json"
            launcher_run_path = launcher_dir / "runs" / f"{turn.cycle_id}-{turn.sequence:02d}.json"
            execution_result = _run_execution_subagent(
                project_root=execution_project_root,
                design_contract=design_contract,
                baseline_docs=baseline_docs,
                planning_doc=planning_doc,
                human_decisions=inputs.get("human_decisions", []),
                request_path=request_artifact_path,
                result_path=result_artifact_path,
                launcher_state_path=launcher_state_path,
                launcher_run_path=launcher_run_path,
            )
            execution_output = execution_result.get("parsed_output", {})
            if not isinstance(execution_output, Mapping):
                execution_output = dict(DEFAULT_EXECUTION_OUTPUT)
            needs_human = bool(execution_output.get("needs_human"))
            if needs_human:
                return {
                    "status": "blocked",
                    "summary": coerce_str(execution_output.get("summary")).strip() or "Execution needs a decision before it can continue.",
                    "questions": [
                        {
                            "question_id": _new_id("question"),
                            "agent": "execution",
                            "question": coerce_str(execution_output.get("human_question")).strip() or "Execution requires a human decision.",
                            "blocking": True,
                            "importance": "high",
                            "tags": _normalize_text_list(execution_output.get("decision_tags", [])) or ["goal_conflict"],
                            "context": {
                                "title": "Execution needs a decision",
                                "options": execution_output.get("options", []),
                                "tradeoffs": execution_output.get("notes", []),
                                "required_reply_shape": coerce_str(execution_output.get("required_reply_shape")).strip(),
                                "supervisor_recommendation": coerce_str(execution_output.get("why_not_auto_answered")).strip(),
                                "selected_primary_doc": design_contract.get("selected_primary_doc", ""),
                                "selected_phase": design_contract.get("selected_phase", {}),
                            },
                        }
                    ],
                    "artifacts": [str(request_artifact_path), str(result_artifact_path)],
                }
            verification_specs = _verification_specs(
                design_contract,
                project_root=execution_project_root,
                doc_root=doc_root,
            )
            verification_runs = [_run_verification_command(spec) for spec in verification_specs]
            verification_ok, verification_findings = _verification_acceptance_from_runs(
                verification_runs,
                expected_count=len(verification_specs),
            )
            scope_findings = _verification_scope_findings(design_contract, verification_runs)
            if scope_findings:
                verification_ok = False
                verification_findings = list(verification_findings) + scope_findings
            artifact_path = self._artifact_path(turn, "execution")
            _write_json(
                artifact_path,
                {
                    "goal": turn.mission.get("goal", ""),
                    "project_root": str(execution_project_root),
                    "selected_primary_doc": design_contract.get("selected_primary_doc") or inputs.get("selected_primary_doc", ""),
                    "design_contract": design_contract,
                    "execution_subagent": execution_result,
                    "execution_output": execution_output,
                    "verification_expectation": design_contract.get("verification_expectation", []),
                    "verification_specs": verification_specs,
                    "verification_commands": [spec.get("command", []) for spec in verification_specs],
                    "work_items": design_contract.get("work_items", []),
                    "target_paths": design_contract.get("target_paths", []),
                    "verification_runs": verification_runs,
                    "verification_status": "passed" if verification_ok else "failed",
                    "verification_findings": verification_findings,
                    "recorded_at": utc_now(),
                },
            )
            return {
                "status": "completed",
                "summary": f"Ran {len(verification_runs)} verification command(s); {sum(1 for run in verification_runs if run.get('returncode') == 0)} passed.",
                "artifacts": [str(request_artifact_path), str(result_artifact_path), str(artifact_path)],
            }

        if agent_id == self.audit_agent_id:
            execution_artifacts = latest_artifacts.get("execution", [])
            execution_plan = self._load_json(execution_artifacts[-1]) if execution_artifacts else {}
            verification_runs = execution_plan.get("verification_runs", [])
            verification_commands = execution_plan.get("verification_commands", [])
            design_contract = execution_plan.get("design_contract", {})
            execution_subagent = execution_plan.get("execution_subagent", {})
            execution_output = execution_plan.get("execution_output", {})
            accepted, findings = _verification_acceptance_from_runs(
                verification_runs if isinstance(verification_runs, Sequence) and not isinstance(verification_runs, (str, bytes, bytearray)) else [],
                expected_count=_count_sequence_items(verification_commands),
            )
            scope_findings = _verification_scope_findings(
                design_contract if isinstance(design_contract, Mapping) else {},
                verification_runs if isinstance(verification_runs, Sequence) and not isinstance(verification_runs, (str, bytes, bytearray)) else [],
            )
            if scope_findings:
                findings = list(findings) + scope_findings
                accepted = False
            if not isinstance(execution_subagent, Mapping) or not execution_subagent:
                findings = list(findings) + ["Execution did not record any subagent implementation evidence."]
                accepted = False
            else:
                exit_code = coerce_int(execution_subagent.get("exit_code"), 0)
                if exit_code != 0:
                    findings = list(findings) + [f"Execution subagent exited with code {exit_code}."]
                    accepted = False
            if isinstance(execution_output, Mapping):
                if coerce_bool(execution_output.get("needs_human"), False):
                    findings = list(findings) + ["Execution requested a human decision instead of finishing the slice."]
                    accepted = False
            else:
                findings = list(findings) + ["Execution output payload was missing or malformed."]
                accepted = False
            if not design_contract:
                audit_status = "replan_design"
                findings = ["Execution ran without a usable design contract."]
            elif accepted:
                audit_status = "accepted"
            else:
                audit_status = "reopen_execution"
            artifact_path = self._artifact_path(turn, "verdict")
            _write_json(
                artifact_path,
                {
                    "audit_status": audit_status,
                    "accepted": audit_status == "accepted",
                    "findings": findings,
                    "verification_commands": verification_commands,
                    "verification_runs": verification_runs,
                    "design_contract": design_contract,
                    "recorded_at": utc_now(),
                },
            )
            return {
                "status": audit_status,
                "summary": {
                    "accepted": "Audit accepted the current round.",
                    "reopen_execution": "Audit reopened the round and returned it to execution.",
                    "replan_design": "Audit requested a new design contract before execution can continue.",
                }[audit_status],
                "artifacts": [str(artifact_path)],
                "audit_status": audit_status,
                "findings": findings,
                "design_contract": design_contract,
                "verification_commands": verification_commands,
            }

        if agent_id == self.cleanup_agent_id:
            cleanup_mode = coerce_str(inputs.get("cleanup_mode")).strip() or "round-close"
            runtime_actions = _cleanup_runtime_temp_files(self.paths.harness_root)
            repo_hygiene_findings = _project_hygiene_findings(project_root) if cleanup_mode == "maintenance" else []
            stale_turn_identity = bool(turn.state.get("cycle_id") or turn.state.get("sequence"))
            stale_pending_gate = False
            pending_gate_id = coerce_str(turn.state.get("pending_gate_id")).strip()
            if cleanup_mode == "recovery" and pending_gate_id:
                try:
                    turn.communication_store.get_gate(pending_gate_id)
                except KeyError:
                    stale_pending_gate = True
            artifact_path = self._artifact_path(turn, cleanup_mode)
            _write_json(
                artifact_path,
                {
                    "cleanup_mode": cleanup_mode,
                    "cleanup_reason": inputs.get("cleanup_reason", ""),
                    "resume_after_cleanup": inputs.get("resume_after_cleanup", ""),
                    "runtime_cleanup_actions": runtime_actions,
                    "repo_hygiene_findings": repo_hygiene_findings,
                    "stale_turn_identity": stale_turn_identity,
                    "stale_pending_gate": stale_pending_gate,
                    "follow_up_required": bool(repo_hygiene_findings),
                    "recorded_at": utc_now(),
                },
            )
            return {
                "status": "completed",
                "summary": f"Cleanup completed in {cleanup_mode} mode.",
                "cleanup_mode": cleanup_mode,
                "follow_up_required": bool(repo_hygiene_findings),
                "artifacts": [str(artifact_path)],
            }

        return {
            "status": "completed",
            "summary": f"Processed {agent_id}.",
            "artifacts": [],
        }

    def _record_result(self, agent_id: str, result: Mapping[str, Any]) -> None:
        latest_reports = self._latest_reports()
        latest_reports[agent_id] = str(result.get("report_path", ""))
        self.state.extra["latest_reports"] = latest_reports
        latest_artifacts = self._latest_artifacts()
        latest_artifacts[agent_id] = [str(item) for item in result.get("report", {}).get("artifacts", [])]
        self.state.extra["latest_artifacts"] = latest_artifacts
        self.state.extra["last_report_path"] = str(result.get("report_path", ""))
        self.state.extra["last_handoff_path"] = str(result.get("handoff_path", ""))
        self.state.last_run_at = utc_now()
        self.state.last_successful_agent = agent_id
        state_after = result.get("state_after", {})
        next_sequence = self._runner_sequence()
        if isinstance(state_after, Mapping):
            try:
                next_sequence = int(state_after.get("sequence", next_sequence))
            except (TypeError, ValueError):
                next_sequence = self._runner_sequence()
        self._set_runner_turn_state(cycle_id=str(result.get("cycle_id", "")), sequence=next_sequence)
        self._save_runtime()

    def _sync_turn_identity(self, result: Mapping[str, Any]) -> None:
        self.state.extra["last_report_path"] = str(result.get("report_path", ""))
        self.state.extra["last_handoff_path"] = str(result.get("handoff_path", ""))
        state_after = result.get("state_after", {})
        next_sequence = self._runner_sequence()
        next_cycle_id = str(result.get("cycle_id", "")).strip()
        if isinstance(state_after, Mapping):
            try:
                next_sequence = int(state_after.get("sequence", next_sequence))
            except (TypeError, ValueError):
                next_sequence = self._runner_sequence()
            state_cycle_id = str(state_after.get("cycle_id", "")).strip()
            if state_cycle_id:
                next_cycle_id = state_cycle_id
        self._set_runner_turn_state(cycle_id=next_cycle_id, sequence=next_sequence)
        self._save_runtime()

    def _open_communication_lane(self, agent_id: str, question: Question, *, why_not_auto_answered: str) -> None:
        brief = self._build_communication_brief(
            agent_id,
            question,
            why_not_auto_answered=why_not_auto_answered,
        )
        self.state.extra["blocked_agent"] = agent_id
        self.state.extra["resume_agent"] = agent_id
        self._set_communication_brief(brief)
        self._set_latest_human_reply(None)
        if self.communication_agent_id:
            self.state.active_agent = self.communication_agent_id
            self._set_runtime_status("running")
            self.mission.status = "active"
        else:
            gate = self.communication_store.open_gate(
                title=brief["title"],
                prompt=self._render_communication_prompt(brief),
                source="supervisor",
                severity=brief["severity"],
                context=json.dumps(brief, ensure_ascii=False),
            )
            self.state.extra["pending_gate_id"] = gate["id"]
            self.state.active_agent = ""
            self._set_runtime_status("waiting_human")
            self.mission.status = "waiting_human"
        self._save_runtime()

    def _open_supervisor_brief(self, brief: Mapping[str, Any], *, blocked_agent: str) -> None:
        self.state.extra["blocked_agent"] = blocked_agent
        self.state.extra["resume_agent"] = blocked_agent
        self._set_communication_brief(dict(brief))
        self._set_latest_human_reply(None)
        if self.communication_agent_id:
            self.state.active_agent = self.communication_agent_id
            self._set_runtime_status("running")
            self.mission.status = "active"
        else:
            gate = self.communication_store.open_gate(
                title=coerce_str(brief.get("title"), "Decision gate").strip() or "Decision gate",
                prompt=self._render_communication_prompt(brief),
                source="supervisor",
                severity=coerce_str(brief.get("severity"), "decision_gate").strip() or "decision_gate",
                context=json.dumps(dict(brief), ensure_ascii=False),
            )
            self.state.extra["pending_gate_id"] = gate["id"]
            self.state.active_agent = ""
            self._set_runtime_status("waiting_human")
            self.mission.status = "waiting_human"
        self._save_runtime()

    def _open_execution_stall_gate(self, report: Mapping[str, Any], *, repeat_count: int) -> None:
        design_contract = report.get("design_contract", {})
        if not isinstance(design_contract, Mapping):
            design_contract = {}
        selected_phase = design_contract.get("selected_phase", {})
        if not isinstance(selected_phase, Mapping):
            selected_phase = {}
        findings = _normalize_text_list(report.get("findings", []))
        brief = {
            "decision_id": _new_id("decision"),
            "title": "Execution is stuck on the current project slice",
            "question": (
                "Supervisor saw the same target-project verification failures "
                f"{repeat_count} time(s) while executing {coerce_str(selected_phase.get('title')).strip() or 'the current slice'}. "
                "How should the harness proceed?"
            ),
            "severity": "goal_conflict",
            "why_not_auto_answered": (
                "Execution keeps producing the same repository-level failures and no new implementation evidence."
            ),
            "source_ref": coerce_str(design_contract.get("selected_primary_doc")).strip(),
            "current_context": {
                "design_contract": dict(design_contract),
                "findings": findings,
                "repeat_count": repeat_count,
            },
            "options": [
                {
                    "label": "Replan before another execution attempt",
                    "value": "replan",
                    "description": "Send the slice back to design with new constraints or a different target.",
                },
                {
                    "label": "Continue current slice after I fix the blocker",
                    "value": "continue",
                    "description": "Keep the current slice, but wait for a concrete environment or implementation fix first.",
                },
            ],
            "tradeoffs": findings[:5],
            "supervisor_recommendation": (
                "Replan the slice or provide a concrete environment/implementation constraint before another run."
            ),
            "agent_positions": [
                {
                    "agent": "audit",
                    "position": "Audit keeps reopening because the same verification findings remain unresolved.",
                },
                {
                    "agent": "execution",
                    "position": "Execution can rerun the target-project checks, but it does not have new implementation evidence to change the result.",
                },
            ],
            "required_reply_shape": "Choose replan/continue and include any concrete constraint or environment fix.",
            "blocked_agent": self.design_agent_id or "design",
        }
        self._open_supervisor_brief(brief, blocked_agent=self.design_agent_id or self._default_work_entry_agent())

    def _auto_replan_stalled_slice(self, report: Mapping[str, Any], *, repeat_count: int) -> None:
        design_contract = report.get("design_contract", {})
        if not isinstance(design_contract, Mapping):
            design_contract = {}
        selected_phase = design_contract.get("selected_phase", {})
        if not isinstance(selected_phase, Mapping):
            selected_phase = {}
        findings = _normalize_text_list(report.get("findings", []))
        phase_title = (
            coerce_str(design_contract.get("origin_phase_title")).strip()
            or coerce_str(selected_phase.get("title")).strip()
            or "the current slice"
        )
        auto_constraints = _unique_texts(
            [
                f"Supervisor auto-replan after {repeat_count} repeated blocker verification failures.",
                "Do not escalate this blocker to the human unless a true architecture, security, or destructive-action decision appears.",
            ]
            + findings[:5]
        )
        self._set_pending_supervisor_decision(
            {
                "decision_id": _new_id("supervisor-auto"),
                "choice": "replan",
                "raw_answer": "\n".join(["replan"] + auto_constraints),
                "constraints": auto_constraints,
                "blocked_agent": self.design_agent_id or self._default_work_entry_agent(),
                "source_ref": coerce_str(design_contract.get("selected_primary_doc")).strip(),
                "current_context": {
                    "design_contract": dict(design_contract),
                    "findings": findings,
                    "repeat_count": repeat_count,
                },
                "created_at": utc_now(),
                "auto_generated": True,
                "title": f"Auto replan stalled blocker for {phase_title}",
            }
        )
        self._clear_audit_reopen_tracking()
        self.state.extra.pop("pending_gate_id", None)
        self.state.extra.pop("blocked_agent", None)
        self.state.extra.pop("resume_agent", None)
        self._set_communication_brief(None)
        self._set_latest_human_reply(None)
        self.state.active_agent = self.design_agent_id or self._default_work_entry_agent()
        self.mission.status = "active"
        self._set_runtime_status("running")
        self._save_runtime()

    def _route_questions(self, agent_id: str, report: Mapping[str, Any]) -> str:
        raw_questions = report.get("questions", [])
        if not isinstance(raw_questions, list) or not raw_questions:
            return "none"
        auto_answers = dict(self.mission.extra.get("auto_answers", {}))
        for raw_question in raw_questions:
            if not isinstance(raw_question, Mapping):
                continue
            payload = dict(raw_question)
            payload.setdefault("question_id", _new_id("question"))
            payload.setdefault("agent", agent_id)
            raw_context = payload.get("context", {})
            context = dict(raw_context) if isinstance(raw_context, Mapping) else {"value": raw_context}
            context["decision_gate_tags"] = list(self.mission.extra.get("decision_gate_tags", []))
            payload["context"] = context
            question = Question.from_mapping(payload)
            save_question(self.paths.memory_root, question.question_id, question)
            route = route_question(question)
            if route.is_gate:
                self._open_communication_lane(
                    agent_id,
                    question,
                    why_not_auto_answered=route.reason,
                )
                return "gate"
            answer = answer_question(question)
            if answer is not None:
                save_answer(self.paths.memory_root, question.question_id, answer)
                auto_answers[question.question_id] = answer.to_mapping()
                if question.context.get("candidate_paths"):
                    self.mission.extra["selected_primary_doc"] = answer.answer
                continue
            self._open_communication_lane(
                agent_id,
                question,
                why_not_auto_answered="supervisor could not safely auto-answer this blocker",
            )
            return "gate"
        if auto_answers:
            self.mission.extra["auto_answers"] = auto_answers
            self._save_runtime()
            return "rerun"
        return "none"

    def _complete_mission(self) -> None:
        self.state.active_agent = ""
        self.mission.status = "completed"
        self._set_runtime_status("completed")
        self._set_runner_turn_state()
        self._save_runtime()

    def _advance_after_report(self, agent_id: str, result: Mapping[str, Any]) -> None:
        report = dict(result["report"])
        self._record_result(agent_id, result)
        status = coerce_str(report.get("status"), "completed").strip()
        self.mission.status = "active"
        self._set_runtime_status("running")

        if agent_id == self.communication_agent_id:
            action = coerce_str(report.get("communication_action")).strip()
            if action == "gate_opened" and report.get("gate_id"):
                self.mission.status = "waiting_human"
                self.state.active_agent = ""
                self.state.extra["pending_gate_id"] = str(report.get("gate_id", ""))
                self._set_runtime_status("waiting_human")
                self._save_runtime()
                return
            if action == "reply_recorded":
                self._set_latest_human_reply(None)
                self._set_communication_brief(None)
                self.state.extra.pop("blocked_agent", None)
                next_agent = coerce_str(
                    self.state.extra.get("resume_agent") or self.state.extra.get("blocked_agent")
                ).strip()
                self.state.active_agent = next_agent or self._default_work_entry_agent()
                self.state.extra.pop("resume_agent", None)
                self._save_runtime()
                return
            self.state.active_agent = self._default_work_entry_agent()
            self._save_runtime()
            return

        if agent_id == self.design_agent_id:
            if coerce_str(report.get("design_status")).strip() == "completed":
                self._complete_mission()
                return
            self.state.active_agent = self.execution_agent_id or ""
            if not self.state.active_agent:
                self._complete_mission()
            else:
                self._save_runtime()
            return

        if agent_id == self.execution_agent_id:
            self.state.active_agent = self.audit_agent_id or ""
            if not self.state.active_agent:
                self._complete_mission()
            else:
                self._save_runtime()
            return

        if agent_id == self.audit_agent_id:
            audit_status = coerce_str(report.get("audit_status") or status).strip() or status
            artifacts = report.get("artifacts", [])
            audit_payload = self._load_json(str(artifacts[0])) if isinstance(artifacts, list) and artifacts else {}
            audit_design_contract = (
                dict(audit_payload.get("design_contract", {}))
                if isinstance(audit_payload, Mapping) and isinstance(audit_payload.get("design_contract", {}), Mapping)
                else {}
            )
            audit_findings = (
                list(audit_payload.get("findings", []))
                if isinstance(audit_payload, Mapping) and isinstance(audit_payload.get("findings", []), list)
                else []
            )
            if audit_status == "accepted":
                self._clear_audit_reopen_tracking()
                self._record_completed_slice(audit_design_contract)
                if self.cleanup_agent_id:
                    self._schedule_cleanup(
                        "round-close",
                        resume_after=self.design_agent_id or self._default_work_entry_agent(),
                        reason="audit accepted the current round",
                    )
                else:
                    self.state.current_round += 1
                    self.mission.round = self.state.current_round
                    self._set_runner_turn_state()
                    self.state.active_agent = self.design_agent_id or self._default_work_entry_agent()
                    self.mission.status = "active"
                    self._set_runtime_status("running")
                    self._save_runtime()
                return
            if audit_status == "replan_design":
                self._clear_audit_reopen_tracking()
                self.state.active_agent = self.design_agent_id or self._default_work_entry_agent()
                self._save_runtime()
                return
            reopen_streak = self._record_audit_reopen(
                audit_findings,
                design_contract=audit_design_contract,
            )
            if (
                coerce_str(audit_design_contract.get("execution_scope")).strip() == "external_project"
                and reopen_streak >= 2
            ):
                self._auto_replan_stalled_slice(
                    audit_payload if isinstance(audit_payload, Mapping) else report,
                    repeat_count=reopen_streak,
                )
                return
            self.state.active_agent = self.execution_agent_id or self._default_work_entry_agent()
            self._save_runtime()
            return

        if agent_id == self.cleanup_agent_id:
            cleanup_mode = coerce_str(report.get("cleanup_mode") or self._cleanup_mode()).strip() or "round-close"
            artifacts = report.get("artifacts", [])
            cleanup_payload = self._load_json(str(artifacts[0])) if isinstance(artifacts, list) and artifacts else {}
            if cleanup_mode == "round-close":
                self.state.current_round += 1
                self.mission.round = self.state.current_round
                self.state.extra["last_cleanup_round_close_at"] = utc_now()
                self._clear_cleanup_request()
                self._set_runner_turn_state()
                self.state.active_agent = self.design_agent_id or self._default_work_entry_agent()
                self.mission.status = "active"
                self._set_runtime_status("running")
                self._save_runtime()
                return
            if cleanup_mode == "maintenance":
                self.state.extra["last_cleanup_maintenance_at"] = utc_now()
                self.mission.extra["maintenance_findings"] = cleanup_payload.get("repo_hygiene_findings", [])
                if cleanup_payload.get("repo_hygiene_findings"):
                    self.state.extra["last_maintenance_report_path"] = str(artifacts[0])
                self._restore_after_cleanup(self._default_work_entry_agent())
                return
            if cleanup_mode == "recovery":
                self.state.extra["last_cleanup_recovery_at"] = utc_now()
                self.state.extra.pop("recovery_requested", None)
                if cleanup_payload.get("stale_turn_identity"):
                    self._set_runner_turn_state()
                if cleanup_payload.get("stale_pending_gate"):
                    self.state.extra["pending_gate_id"] = ""
                self._restore_after_cleanup(self._default_work_entry_agent())
                return
            self._clear_cleanup_request()
            self.state.active_agent = self._resume_after_cleanup() or self._default_work_entry_agent()
            self._save_runtime()
            return

        self.state.active_agent = self._default_work_entry_agent()
        self._save_runtime()

    def _run_agent_until_stable(self, agent_id: str, max_attempts: int = 3) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for _ in range(max_attempts):
            result = self.runner.run_agent(
                self.specs_by_id[agent_id],
                self._build_handoff(agent_id),
                mission=self.mission.to_mapping(),
                state=self.state.to_mapping(),
                runtime_paths={"runtime_root": self.paths.harness_root},
            )
            steps.append(result)
            route_outcome = self._route_questions(agent_id, result["report"])
            if route_outcome == "rerun":
                self._sync_turn_identity(result)
                continue
            if route_outcome == "gate":
                self._sync_turn_identity(result)
                return steps
            self._advance_after_report(agent_id, result)
            return steps
        self.state.retry_count += 1
        self._set_runtime_status("failed")
        self._save_runtime()
        return steps

    def run_until_stable(self, *, max_turns: int = 20) -> SchedulerResult:
        steps: list[dict[str, Any]] = []
        previous_digest = coerce_str(self.mission.extra.get("doc_digest")).strip()
        self._refresh_doc_bundle()
        self._resume_for_doc_change(previous_digest)
        if self._runtime_status() == "waiting_human" and not self._resume_if_human_replied():
            self._prepare_next_agent()
            if self._runtime_status() == "waiting_human":
                return SchedulerResult(
                    status="waiting_human",
                    steps=steps,
                    pending_gate_id=str(self.state.extra.get("pending_gate_id", "")) or None,
                    mission=self.mission,
                    state=self.state,
                )
        self._prepare_next_agent()
        for _ in range(max_turns):
            if self._runtime_status() in {"waiting_human", "completed", "failed"}:
                break
            self._prepare_next_agent()
            agent_id = self.state.active_agent or self._default_work_entry_agent()
            steps.extend(self._run_agent_until_stable(agent_id))
        return SchedulerResult(
            status=self._runtime_status(),
            steps=steps,
            pending_gate_id=coerce_str(self.state.extra.get("pending_gate_id")) or None,
            mission=self.mission,
            state=self.state,
        )

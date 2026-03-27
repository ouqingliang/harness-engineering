from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence
import uuid

from .auto_answer import answer_question
from .communication_api import CommunicationStore
from .documents import build_doc_bundle
from .project_context import project_root_from_doc_root, same_path
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
from .scheduler_components import (
    _launch_execution_subagent,
    DEFAULT_EXECUTION_OUTPUT,
    HARNESS_ROOT,
    _command_display,
    _normalize_text_list,
    _run_verification_command,
    _verification_acceptance_from_runs,
    _verification_expectation_from_text,
    _verification_scope_findings,
    _verification_specs,
    _write_json,
    execute_turn as _execute_scheduler_turn,
)
from .worktree import (
    WorktreeError,
    ensure_supervised_worktree,
    promote_worktree_to_project_root,
    remove_supervised_worktree,
    worktree_common_dir,
)


DEFAULT_CLEANUP_MAINTENANCE_INTERVAL_SECONDS = 4 * 60 * 60
ARCHITECTURE_BASELINE_DOCS = (
    "designs/2026-03-25-task-centered-autonomous-ops-platform.md",
    "designs/2026-03-25-harness-engineering-integration.md",
    "designs/2026-03-25-center-subsystem-architecture-outline.md",
    "plans/2026-03-25-task-mainline-and-engineernode-removal.md",
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


def _count_sequence_items(value: Any) -> int:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value)
    return 0
def _parse_utc(text: Any) -> datetime | None:
    raw = coerce_str(text).strip()
    if not raw:
        return None
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


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


def _design_contract_from_docs(
    *,
    doc_root: Path,
    project_root: Path,
    doc_bundle: Mapping[str, Any],
    selected_primary_doc: str,
    maintenance_findings: Sequence[Any],
    completed_slices: Sequence[Any],
    reserved_slice_keys: Sequence[str] = (),
) -> dict[str, Any]:
    planning_doc = _preferred_planning_doc(doc_bundle)
    baseline_docs = _preferred_baseline_docs(doc_bundle)
    doc_path = selected_primary_doc or (baseline_docs[0] if baseline_docs else planning_doc)
    planning_text = _read_doc_text(doc_root, planning_doc)
    doc_text = planning_text or _read_doc_text(doc_root, doc_path)
    phases = _extract_phase_plans(doc_text)
    completed_slice_keys = _completed_slice_keys(completed_slices)
    reserved_keys = {coerce_str(item).strip() for item in reserved_slice_keys if coerce_str(item).strip()}
    effective_completed = list(completed_slices)
    for slice_key in reserved_keys - completed_slice_keys:
        plan_doc, _, phase_title = slice_key.partition("::")
        effective_completed.append({"slice_key": slice_key, "selected_planning_doc": plan_doc, "phase_title": phase_title})
    selected_phase = _select_active_phase(
        phases,
        planning_doc=planning_doc,
        completed_slices=effective_completed,
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
    completed_slice_keys = _completed_slice_keys(effective_completed)
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
            not in _completed_slice_keys(effective_completed)
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
        self.mission.extra.setdefault("planned_slice_queue", [])
        self.mission.extra.setdefault("running_agent_runs", [])
        self.mission.extra.setdefault("completed_agent_queue", [])
        self.mission.extra.setdefault("managed_worktrees", [])
        self.mission.extra.setdefault("prefetch_completed", False)
        self.mission.extra.setdefault("supervisor_decisions", [])
        self.mission.extra.setdefault("pending_agent_briefs", {})
        legacy_execution_runs = self.mission.extra.pop("running_execution_runs", [])
        if isinstance(legacy_execution_runs, list) and legacy_execution_runs:
            migrated_runs = self._running_agent_runs()
            migrated_runs.extend(
                {
                    **dict(item),
                    "agent_id": coerce_str(item.get("agent_id")).strip() or (self.execution_agent_id or "execution"),
                }
                for item in legacy_execution_runs
                if isinstance(item, Mapping)
            )
            self._set_running_agent_runs(migrated_runs)
        legacy_execution_queue = self.mission.extra.pop("completed_execution_queue", [])
        if isinstance(legacy_execution_queue, list) and legacy_execution_queue:
            migrated_queue = self._completed_agent_queue()
            migrated_queue.extend(
                {
                    **dict(item),
                    "agent_id": coerce_str(item.get("agent_id")).strip() or (self.execution_agent_id or "execution"),
                    "status": coerce_str(item.get("status")).strip() or "waiting_audit",
                }
                for item in legacy_execution_queue
                if isinstance(item, Mapping)
            )
            self._set_completed_agent_queue(migrated_queue)
        legacy_execution_brief = self.mission.extra.pop("pending_execution_brief", None)
        if legacy_execution_brief and isinstance(legacy_execution_brief, Mapping):
            pending_agent_briefs = self._pending_agent_briefs()
            pending_agent_briefs[self.execution_agent_id or "execution"] = dict(legacy_execution_brief)
            self.mission.extra["pending_agent_briefs"] = pending_agent_briefs
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

    def _save_runtime(self) -> None:
        save_mission(self.paths.memory_root, self.mission)
        save_state(self.paths.memory_root, self.state)

    def _clear_audit_reopen_tracking(self) -> None:
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
            "running_agents": self._running_agents_snapshot(),
            "queued_slices": self._queued_slices_snapshot(),
            "recent_events": self._recent_events(),
            "agent_statuses": self._agent_statuses_snapshot(),
            "managed_worktrees": self._managed_worktrees(),
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

    def _running_agent_runs(self) -> list[dict[str, Any]]:
        payload = self.mission.extra.get("running_agent_runs", [])
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    def _set_running_agent_runs(self, runs: Sequence[Mapping[str, Any]]) -> None:
        self.mission.extra["running_agent_runs"] = [dict(item) for item in runs if isinstance(item, Mapping)]

    def _running_agent(self, agent_id: str) -> list[dict[str, Any]]:
        normalized = coerce_str(agent_id).strip()
        return [
            item
            for item in self._running_agent_runs()
            if coerce_str(item.get("agent_id")).strip() == normalized
        ]

    def _completed_agent_queue(self) -> list[dict[str, Any]]:
        payload = self.mission.extra.get("completed_agent_queue", [])
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    def _set_completed_agent_queue(self, queue: Sequence[Mapping[str, Any]]) -> None:
        self.mission.extra["completed_agent_queue"] = [dict(item) for item in queue if isinstance(item, Mapping)]

    def _completed_agent_entries(self, agent_id: str) -> list[dict[str, Any]]:
        normalized = coerce_str(agent_id).strip()
        return [
            item
            for item in self._completed_agent_queue()
            if coerce_str(item.get("agent_id")).strip() == normalized
        ]

    def _running_execution_runs(self) -> list[dict[str, Any]]:
        return self._running_agent(self.execution_agent_id or "execution")

    def _completed_execution_queue(self) -> list[dict[str, Any]]:
        return self._completed_agent_entries(self.execution_agent_id or "execution")

    def _managed_worktrees(self) -> list[dict[str, Any]]:
        payload = self.mission.extra.get("managed_worktrees", [])
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    def _set_managed_worktrees(self, entries: Sequence[Mapping[str, Any]]) -> None:
        self.mission.extra["managed_worktrees"] = [dict(item) for item in entries if isinstance(item, Mapping)]

    def _find_managed_worktree(self, *, slice_key: str, agent_id: str) -> dict[str, Any] | None:
        normalized_slice_key = coerce_str(slice_key).strip()
        normalized_agent_id = coerce_str(agent_id).strip()
        for item in self._managed_worktrees():
            if (
                coerce_str(item.get("slice_key")).strip() == normalized_slice_key
                and coerce_str(item.get("agent_id")).strip() == normalized_agent_id
            ):
                return item
        return None

    def _remember_managed_worktree(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        slice_key = coerce_str(payload.get("slice_key")).strip()
        agent_id = coerce_str(payload.get("agent_id")).strip()
        updated = False
        entries: list[dict[str, Any]] = []
        for item in self._managed_worktrees():
            if (
                coerce_str(item.get("slice_key")).strip() == slice_key
                and coerce_str(item.get("agent_id")).strip() == agent_id
            ):
                merged = dict(item)
                merged.update(dict(payload))
                entries.append(merged)
                updated = True
            else:
                entries.append(item)
        if not updated:
            entries.append(dict(payload))
        self._set_managed_worktrees(entries)
        return dict(payload)

    def _drop_managed_worktree(self, *, slice_key: str, agent_id: str) -> dict[str, Any] | None:
        normalized_slice_key = coerce_str(slice_key).strip()
        normalized_agent_id = coerce_str(agent_id).strip()
        removed: dict[str, Any] | None = None
        entries: list[dict[str, Any]] = []
        for item in self._managed_worktrees():
            if (
                removed is None
                and coerce_str(item.get("slice_key")).strip() == normalized_slice_key
                and coerce_str(item.get("agent_id")).strip() == normalized_agent_id
            ):
                removed = dict(item)
                continue
            entries.append(item)
        self._set_managed_worktrees(entries)
        return removed

    def _ensure_agent_worktree(
        self,
        *,
        agent_id: str,
        slice_key: str,
        canonical_project_root: Path,
        phase_title: str,
    ) -> dict[str, Any]:
        normalized_agent_id = coerce_str(agent_id).strip()
        existing = self._find_managed_worktree(slice_key=slice_key, agent_id=normalized_agent_id)
        if existing and Path(coerce_str(existing.get("path")).strip()).exists():
            existing_path = Path(coerce_str(existing.get("path")).strip()).resolve()
            if worktree_common_dir(existing_path) == worktree_common_dir(canonical_project_root):
                existing["status"] = "assigned"
                existing["last_assigned_at"] = utc_now()
                self._remember_managed_worktree(existing)
                return existing
            self._drop_managed_worktree(slice_key=slice_key, agent_id=normalized_agent_id)
            try:
                remove_supervised_worktree(
                    project_root=Path(
                        coerce_str(existing.get("project_root")).strip() or str(canonical_project_root)
                    ),
                    worktree_root=existing_path,
                )
            except Exception:
                shutil.rmtree(existing_path, ignore_errors=True)
        info = ensure_supervised_worktree(
            worktrees_dir=self.paths.worktrees_dir,
            project_root=canonical_project_root,
            key=slice_key,
            label=f"{normalized_agent_id}-{phase_title or slice_key}",
        )
        entry = {
            "agent_id": normalized_agent_id,
            "slice_key": slice_key,
            "phase_title": phase_title,
            "path": info["path"],
            "name": info["name"],
            "project_root": info["project_root"],
            "status": "assigned",
            "created_at": utc_now(),
            "last_assigned_at": utc_now(),
        }
        self._remember_managed_worktree(entry)
        self._append_recent_event(
            kind="worktree_assigned",
            summary=f"Supervisor assigned worktree {entry['name']} for {phase_title or slice_key}.",
            details={"slice_key": slice_key, "path": entry["path"]},
        )
        self._save_runtime()
        return entry

    def _ensure_execution_worktree(
        self,
        *,
        slice_key: str,
        canonical_project_root: Path,
        phase_title: str,
    ) -> dict[str, Any]:
        return self._ensure_agent_worktree(
            agent_id=self.execution_agent_id or "execution",
            slice_key=slice_key,
            canonical_project_root=canonical_project_root,
            phase_title=phase_title,
        )

    def _promote_execution_worktree(self, design_contract: Mapping[str, Any]) -> list[dict[str, Any]]:
        slice_key = coerce_str(design_contract.get("slice_key")).strip()
        if not slice_key:
            return []
        entry = self._find_managed_worktree(slice_key=slice_key, agent_id=self.execution_agent_id or "execution")
        if entry is None:
            return []
        actions = promote_worktree_to_project_root(
            worktree_root=Path(coerce_str(entry.get("path")).strip()),
            project_root=Path(coerce_str(entry.get("project_root")).strip() or coerce_str(design_contract.get("canonical_project_root")).strip()),
        )
        entry["status"] = "promoted"
        entry["last_promoted_at"] = utc_now()
        self._remember_managed_worktree(entry)
        self._append_recent_event(
            kind="worktree_promoted",
            summary=f"Supervisor promoted worktree {coerce_str(entry.get('name')).strip()} into the canonical repository.",
            details={"slice_key": slice_key, "action_count": len(actions)},
        )
        return actions

    def _release_execution_worktree(self, design_contract: Mapping[str, Any]) -> None:
        slice_key = coerce_str(design_contract.get("slice_key")).strip()
        if not slice_key:
            return
        self._release_agent_worktree(
            agent_id=self.execution_agent_id or "execution",
            slice_key=slice_key,
            canonical_project_root=coerce_str(design_contract.get("canonical_project_root")).strip()
            or coerce_str(design_contract.get("project_root")).strip(),
        )

    def _release_agent_worktree(
        self,
        *,
        agent_id: str,
        slice_key: str,
        canonical_project_root: str,
    ) -> None:
        entry = self._drop_managed_worktree(slice_key=slice_key, agent_id=agent_id)
        if entry is None:
            return
        remove_supervised_worktree(
            project_root=Path(coerce_str(entry.get("project_root")).strip() or canonical_project_root),
            worktree_root=Path(coerce_str(entry.get("path")).strip()),
        )
        self._append_recent_event(
            kind="worktree_released",
            summary=f"Supervisor released worktree {coerce_str(entry.get('name')).strip() or slice_key}.",
            details={"slice_key": slice_key},
        )

    def _pending_agent_briefs(self) -> dict[str, Any]:
        payload = self.mission.extra.get("pending_agent_briefs", {})
        return dict(payload) if isinstance(payload, Mapping) else {}

    def _pending_agent_brief(self, agent_id: str) -> dict[str, Any] | None:
        payload = self._pending_agent_briefs().get(agent_id)
        return dict(payload) if isinstance(payload, Mapping) else None

    def _set_pending_agent_brief(self, agent_id: str, payload: Mapping[str, Any] | None) -> None:
        pending = self._pending_agent_briefs()
        if payload:
            pending[agent_id] = dict(payload)
        else:
            pending.pop(agent_id, None)
        self.mission.extra["pending_agent_briefs"] = pending

    def _pending_execution_brief(self) -> dict[str, Any] | None:
        return self._pending_agent_brief(self.execution_agent_id or "execution")

    def _set_pending_execution_brief(self, payload: Mapping[str, Any] | None) -> None:
        self._set_pending_agent_brief(self.execution_agent_id or "execution", payload)

    def _prefetch_completed(self) -> bool:
        return coerce_bool(self.mission.extra.get("prefetch_completed"), False)

    def _set_prefetch_completed(self, value: bool) -> None:
        self.mission.extra["prefetch_completed"] = bool(value)

    def _recent_events(self) -> list[dict[str, Any]]:
        payload = self.state.extra.get("recent_events", [])
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    def _append_recent_event(self, *, kind: str, summary: str, details: Mapping[str, Any] | None = None) -> None:
        events = self._recent_events()
        events.append(
            {
                "recorded_at": utc_now(),
                "kind": kind,
                "summary": summary,
                "details": dict(details or {}),
            }
        )
        self.state.extra["recent_events"] = events[-25:]

    def _request_scheduler_yield(self) -> None:
        self.state.extra["scheduler_yield"] = True

    def _consume_scheduler_yield(self) -> bool:
        return coerce_bool(self.state.extra.pop("scheduler_yield", False), False)

    def _running_agents_snapshot(self) -> list[dict[str, Any]]:
        agents: list[dict[str, Any]] = []
        for run in self._running_agent_runs():
            agents.append(
                {
                    "id": coerce_str(run.get("agent_id")).strip() or "agent",
                    "status": coerce_str(run.get("status")).strip() or "running",
                    "slice_key": coerce_str(run.get("slice_key")).strip(),
                    "phase_title": coerce_str(run.get("phase_title")).strip(),
                    "started_at": coerce_str(run.get("started_at")).strip(),
                    "project_root": coerce_str(run.get("project_root")).strip(),
                    "worktree_path": coerce_str(run.get("worktree_path")).strip(),
                    "brief": coerce_str(run.get("brief")).strip(),
                }
            )
        supervisor_focus = coerce_str(self.state.active_agent).strip()
        if supervisor_focus and supervisor_focus not in {coerce_str(item.get("id")).strip() for item in agents}:
            agents.append({"id": supervisor_focus, "status": "supervisor_focus"})
        return agents

    def _queued_slices_snapshot(self) -> list[dict[str, Any]]:
        queued: list[dict[str, Any]] = []
        for contract in self._planned_slice_queue():
            queued.append(
                {
                    "slice_key": coerce_str(contract.get("slice_key")).strip(),
                    "phase_title": coerce_str(
                        contract.get("selected_phase", {}).get("title")
                        if isinstance(contract.get("selected_phase"), Mapping)
                        else ""
                    ).strip(),
                    "status": "prefetched",
                }
            )
        for item in self._completed_agent_queue():
            queued.append(
                {
                    "agent_id": coerce_str(item.get("agent_id")).strip(),
                    "slice_key": coerce_str(item.get("slice_key")).strip(),
                    "phase_title": coerce_str(item.get("phase_title")).strip() or coerce_str(item.get("current_slice")).strip(),
                    "status": coerce_str(item.get("status")).strip() or "queued",
                }
            )
        return queued

    def _latest_design_contract(self) -> dict[str, Any]:
        latest_design_artifacts = self._latest_artifacts().get("design", [])
        if not latest_design_artifacts:
            return {}
        try:
            payload = self._load_json(str(latest_design_artifacts[-1]))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, Mapping) else {}

    def _agent_statuses_snapshot(self) -> list[dict[str, Any]]:
        active_agent = coerce_str(self.state.active_agent).strip()
        runtime_status = self._runtime_status()
        pending_gate_id = coerce_str(self.state.extra.get("pending_gate_id")).strip()
        communication_brief = self._communication_brief() or {}
        latest_human_reply = self._latest_human_reply() or {}
        cleanup_mode = self._cleanup_mode()
        running_agent_runs = self._running_agent_runs()
        completed_agent_queue = self._completed_agent_queue()
        planned_slice_queue = self._planned_slice_queue()
        pending_agent_briefs = self._pending_agent_briefs()
        latest_design_contract = self._latest_design_contract()
        latest_phase = latest_design_contract.get("selected_phase", {})
        if not isinstance(latest_phase, Mapping):
            latest_phase = {}
        latest_phase_title = coerce_str(latest_phase.get("title")).strip()
        statuses: list[dict[str, Any]] = []

        for spec in self.specs:
            agent_id = spec["id"]
            running_for_agent = [
                item
                for item in running_agent_runs
                if coerce_str(item.get("agent_id")).strip() == agent_id
            ]
            queued_for_agent = [
                item
                for item in completed_agent_queue
                if coerce_str(item.get("agent_id")).strip() == agent_id
            ]
            pending_brief = pending_agent_briefs.get(agent_id, {})
            if not isinstance(pending_brief, Mapping):
                pending_brief = {}
            worktree_path = ""
            current_slice = ""
            current_brief = coerce_str(pending_brief.get("summary")).strip()
            if running_for_agent:
                first_run = running_for_agent[0]
                worktree_path = coerce_str(first_run.get("worktree_path")).strip()
                current_slice = coerce_str(first_run.get("phase_title")).strip() or coerce_str(first_run.get("slice_key")).strip()
                if not current_brief:
                    current_brief = coerce_str(first_run.get("brief")).strip()
            elif queued_for_agent:
                first_queue = queued_for_agent[0]
                current_slice = coerce_str(first_queue.get("phase_title")).strip() or coerce_str(first_queue.get("slice_key")).strip()
                if not current_brief:
                    current_brief = coerce_str(first_queue.get("summary")).strip()
            elif agent_id == self.design_agent_id and latest_phase_title:
                current_slice = latest_phase_title
            elif agent_id == self.execution_agent_id and latest_phase_title:
                current_slice = latest_phase_title
            entry = {
                "id": agent_id,
                "name": spec["name"],
                "status": "idle",
                "summary": "Idle.",
                "details": [],
                "queued": len(queued_for_agent),
                "running": len(running_for_agent),
                "blocked": bool(pending_gate_id and coerce_str(self.state.extra.get("blocked_agent")).strip() == agent_id),
                "worktree": worktree_path,
                "current_slice": current_slice,
                "current_brief": current_brief,
            }
            if agent_id == self.communication_agent_id:
                if latest_human_reply:
                    entry["status"] = "reply_buffered"
                    entry["summary"] = "A human reply is buffered for supervisor resume."
                elif pending_gate_id:
                    entry["status"] = "waiting_human"
                    entry["summary"] = "Waiting for a human decision through the communication page."
                    entry["details"] = [f"gate_id={pending_gate_id}"]
                elif communication_brief:
                    entry["status"] = "opening_gate"
                    entry["summary"] = "Preparing a supervisor decision brief for the human page."
                elif active_agent == agent_id:
                    entry["status"] = "running"
                    entry["summary"] = "Processing communication-side work."
            elif agent_id == self.design_agent_id:
                if running_for_agent:
                    entry["status"] = "running"
                    entry["summary"] = "Design is running in the background."
                elif planned_slice_queue:
                    queued_titles = [
                        coerce_str(item.get("selected_phase", {}).get("title") if isinstance(item.get("selected_phase"), Mapping) else "").strip()
                        for item in planned_slice_queue
                    ]
                    queued_titles = [item for item in queued_titles if item]
                    entry["status"] = "prefetched"
                    entry["summary"] = "The next slice is already prefetched."
                    entry["details"] = queued_titles[:3]
                elif queued_for_agent:
                    entry["status"] = coerce_str(queued_for_agent[0].get("status")).strip() or "queued"
                    entry["summary"] = "A completed design artifact is waiting for supervisor routing."
                elif latest_phase_title:
                    entry["status"] = "ready"
                    entry["summary"] = f"Current contract is ready for {latest_phase_title}."
                elif active_agent == agent_id:
                    entry["status"] = "planning"
                    entry["summary"] = "Preparing the current design contract."
            elif agent_id == self.execution_agent_id:
                if running_for_agent:
                    titles = [coerce_str(item.get("phase_title")).strip() or coerce_str(item.get("slice_key")).strip() for item in running_for_agent]
                    entry["status"] = "running"
                    entry["summary"] = "Executing approved slices in the background."
                    entry["details"] = [item for item in titles if item][:3]
                    worktree_paths = [
                        coerce_str(item.get("worktree_path")).strip()
                        for item in running_for_agent
                        if coerce_str(item.get("worktree_path")).strip()
                    ]
                    if worktree_paths:
                        entry["details"].append(f"worktree={worktree_paths[0]}")
                elif queued_for_agent:
                    titles = [coerce_str(item.get("phase_title")).strip() or coerce_str(item.get("slice_key")).strip() for item in queued_for_agent]
                    entry["status"] = "waiting_audit"
                    entry["summary"] = "Execution finished and is waiting for audit."
                    entry["details"] = [item for item in titles if item][:3]
                elif pending_brief:
                    entry["status"] = "retrying"
                    entry["summary"] = "Supervisor routed the latest audit findings back to execution."
                    entry["details"] = _normalize_text_list(pending_brief.get("findings", []))[:3]
                elif active_agent == agent_id:
                    entry["status"] = "launching"
                    entry["summary"] = "Launching or polling execution work."
                elif latest_phase_title:
                    entry["status"] = "ready"
                    entry["summary"] = f"Ready to execute {latest_phase_title}."
            elif agent_id == self.audit_agent_id:
                if running_for_agent:
                    entry["status"] = "running"
                    entry["summary"] = "Audit is reviewing evidence in the background."
                elif queued_for_agent:
                    titles = [coerce_str(item.get("phase_title")).strip() or coerce_str(item.get("slice_key")).strip() for item in queued_for_agent]
                    entry["status"] = "queued"
                    entry["summary"] = "A completed audit verdict is waiting for supervisor routing."
                    entry["details"] = [item for item in titles if item][:3]
                elif self._completed_execution_queue():
                    titles = [coerce_str(item.get("phase_title")).strip() or coerce_str(item.get("slice_key")).strip() for item in self._completed_execution_queue()]
                    entry["status"] = "queued"
                    entry["summary"] = "Audit work is queued."
                    entry["details"] = [item for item in titles if item][:3]
                elif active_agent == agent_id:
                    entry["status"] = "auditing"
                    entry["summary"] = "Reviewing completed execution evidence."
            elif agent_id == self.cleanup_agent_id:
                if cleanup_mode:
                    entry["status"] = cleanup_mode
                    entry["summary"] = f"Running cleanup in {cleanup_mode} mode."
                elif runtime_status == "completed":
                    entry["status"] = "standby"
                    entry["summary"] = "Waiting for the next maintenance or recovery request."
            else:
                if active_agent == agent_id:
                    entry["status"] = "running"
                    entry["summary"] = "Running."
            if entry["blocked"]:
                entry["status"] = "blocked"
                entry["summary"] = "Supervisor paused this agent behind an explicit human gate."
            statuses.append(entry)
        return statuses

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
            if self._current_running_execution() or self._completed_execution_queue():
                return False
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
        if self.state.active_agent:
            return
        if self.audit_agent_id and self._current_running_agent(self.audit_agent_id):
            self.state.active_agent = self.audit_agent_id
            self._save_runtime()
            return
        if self._completed_execution_queue() and self.audit_agent_id:
            self.state.active_agent = self.audit_agent_id
            self._save_runtime()
            return
        if self.design_agent_id and self._current_running_agent(self.design_agent_id):
            self.state.active_agent = self.design_agent_id
            self._save_runtime()
            return
        if self._current_running_execution():
            if not self._planned_slice_queue() and not self._prefetch_completed() and self.design_agent_id:
                self.state.active_agent = self.design_agent_id
            elif self.execution_agent_id:
                self.state.active_agent = self.execution_agent_id
            self._save_runtime()
            return
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

    def _planned_slice_queue(self) -> list[dict[str, Any]]:
        payload = self.mission.extra.get("planned_slice_queue", [])
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    def _set_planned_slice_queue(self, contracts: Sequence[Mapping[str, Any]]) -> None:
        self.mission.extra["planned_slice_queue"] = [dict(item) for item in contracts if isinstance(item, Mapping)]

    def _find_running_agent(self, agent_id: str, slice_key: str) -> dict[str, Any] | None:
        normalized_agent_id = coerce_str(agent_id).strip()
        normalized_slice_key = coerce_str(slice_key).strip()
        if not normalized_agent_id:
            return None
        for item in self._running_agent(agent_id):
            if not normalized_slice_key or coerce_str(item.get("slice_key")).strip() == normalized_slice_key:
                return item
        return None

    def _current_running_agent(self, agent_id: str) -> dict[str, Any] | None:
        runs = self._running_agent(agent_id)
        return dict(runs[0]) if runs else None

    def _upsert_running_agent(self, payload: Mapping[str, Any]) -> None:
        agent_id = coerce_str(payload.get("agent_id")).strip()
        slice_key = coerce_str(payload.get("slice_key")).strip()
        if not agent_id:
            return
        runs = self._running_agent_runs()
        updated = False
        next_runs: list[dict[str, Any]] = []
        for item in runs:
            same_agent = coerce_str(item.get("agent_id")).strip() == agent_id
            same_slice = coerce_str(item.get("slice_key")).strip() == slice_key
            if same_agent and same_slice and not updated:
                merged = dict(item)
                merged.update(dict(payload))
                next_runs.append(merged)
                updated = True
            else:
                next_runs.append(dict(item))
        if not updated:
            next_runs.append(dict(payload))
        self._set_running_agent_runs(next_runs)

    def _remove_running_agent(self, agent_id: str, slice_key: str) -> dict[str, Any] | None:
        normalized_agent_id = coerce_str(agent_id).strip()
        normalized_slice_key = coerce_str(slice_key).strip()
        removed: dict[str, Any] | None = None
        remaining: list[dict[str, Any]] = []
        for item in self._running_agent_runs():
            same_agent = coerce_str(item.get("agent_id")).strip() == normalized_agent_id
            same_slice = coerce_str(item.get("slice_key")).strip() == normalized_slice_key
            if same_agent and same_slice and removed is None:
                removed = dict(item)
                continue
            remaining.append(dict(item))
        self._set_running_agent_runs(remaining)
        return removed

    def _queue_completed_agent(self, payload: Mapping[str, Any]) -> None:
        queue = self._completed_agent_queue()
        artifact_path = coerce_str(payload.get("artifact_path") or payload.get("execution_artifact_path")).strip()
        agent_id = coerce_str(payload.get("agent_id")).strip()
        if artifact_path and any(
            coerce_str(item.get("artifact_path") or item.get("execution_artifact_path")).strip() == artifact_path
            and coerce_str(item.get("agent_id")).strip() == agent_id
            for item in queue
        ):
            return
        queue.append(dict(payload))
        self._set_completed_agent_queue(queue)

    def _consume_completed_agent(self, agent_id: str, artifact_path: str) -> None:
        normalized_agent_id = coerce_str(agent_id).strip()
        normalized_artifact = coerce_str(artifact_path).strip()
        queue = [
            dict(item)
            for item in self._completed_agent_queue()
            if not (
                coerce_str(item.get("agent_id")).strip() == normalized_agent_id
                and coerce_str(item.get("artifact_path") or item.get("execution_artifact_path")).strip() == normalized_artifact
            )
        ]
        self._set_completed_agent_queue(queue)

    def _find_running_execution(self, slice_key: str) -> dict[str, Any] | None:
        return self._find_running_agent(self.execution_agent_id or "execution", slice_key)

    def _current_running_execution(self) -> dict[str, Any] | None:
        return self._current_running_agent(self.execution_agent_id or "execution")

    def _upsert_running_execution(self, payload: Mapping[str, Any]) -> None:
        next_payload = dict(payload)
        next_payload["agent_id"] = self.execution_agent_id or "execution"
        self._upsert_running_agent(next_payload)

    def _remove_running_execution(self, slice_key: str) -> dict[str, Any] | None:
        return self._remove_running_agent(self.execution_agent_id or "execution", slice_key)

    def _queue_completed_execution(self, payload: Mapping[str, Any]) -> None:
        next_payload = dict(payload)
        next_payload["agent_id"] = self.execution_agent_id or "execution"
        next_payload["artifact_path"] = coerce_str(payload.get("artifact_path") or payload.get("execution_artifact_path")).strip()
        self._queue_completed_agent(next_payload)

    def _consume_completed_execution(self, artifact_path: str) -> None:
        self._consume_completed_agent(self.execution_agent_id or "execution", artifact_path)

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
            "managed_worktrees": self._managed_worktrees(),
        }
        if agent_id == self.communication_agent_id:
            inputs["communication_brief"] = self._communication_brief()
            inputs["latest_human_reply"] = self._latest_human_reply()
        if agent_id == self.execution_agent_id:
            inputs["pending_execution_brief"] = self._pending_execution_brief()
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
        return _execute_scheduler_turn(
            self,
            turn,
            new_id=_new_id,
            preferred_planning_doc=_preferred_planning_doc,
            design_contract_from_docs=_design_contract_from_docs,
            contract_for_supervisor_decision=_contract_for_supervisor_decision,
            count_sequence_items=_count_sequence_items,
            cleanup_runtime_temp_files=_cleanup_runtime_temp_files,
            project_hygiene_findings=_project_hygiene_findings,
            launch_execution_subagent=_launch_execution_subagent,
            run_verification_command=_run_verification_command,
            verification_acceptance_from_runs=_verification_acceptance_from_runs,
            verification_scope_findings=_verification_scope_findings,
            verification_specs=_verification_specs,
        )

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

    def _build_execution_retry_brief(
        self,
        report: Mapping[str, Any],
        *,
        reopen_streak: int,
    ) -> dict[str, Any]:
        design_contract = report.get("design_contract", {})
        if not isinstance(design_contract, Mapping):
            design_contract = {}
        selected_phase = design_contract.get("selected_phase", {})
        if not isinstance(selected_phase, Mapping):
            selected_phase = {}
        findings = _normalize_text_list(report.get("findings", []))
        phase_title = coerce_str(selected_phase.get("title")).strip() or "the current slice"
        return {
            "brief_id": _new_id("execution-retry"),
            "decision": "retry_execution",
            "slice_key": coerce_str(design_contract.get("slice_key")).strip(),
            "phase_title": phase_title,
            "findings": findings,
            "reopen_streak": reopen_streak,
            "design_contract": dict(design_contract),
            "created_at": utc_now(),
            "summary": f"Retry {phase_title} after audit findings are addressed inside the assigned worktree.",
        }

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
        self._set_pending_execution_brief(None)
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
            design_status = coerce_str(report.get("design_status")).strip()
            artifacts = report.get("artifacts", [])
            contract_artifact_path = str(artifacts[-1]) if isinstance(artifacts, list) and artifacts else ""
            if contract_artifact_path:
                self._consume_completed_agent(self.design_agent_id or "design", contract_artifact_path)
            if design_status == "failed" or status == "failed":
                self.state.active_agent = ""
                self.mission.status = "failed"
                self.state.extra["failure_reason"] = "design background worker failed"
                self._set_runtime_status("failed")
                self._save_runtime()
                return
            if design_status in {"launched", "running"}:
                next_agent = ""
                if self._completed_execution_queue() and self.audit_agent_id:
                    next_agent = self.audit_agent_id
                self.state.active_agent = next_agent
                self._request_scheduler_yield()
                self._save_runtime()
                return
            if design_status == "completed":
                if (
                    self._current_running_execution()
                    or self._current_running_agent(self.audit_agent_id or "")
                    or self._completed_execution_queue()
                ):
                    self.state.active_agent = self.audit_agent_id if self._completed_execution_queue() and self.audit_agent_id else ""
                    self._request_scheduler_yield()
                    self._save_runtime()
                else:
                    self._complete_mission()
                return
            if self._completed_execution_queue() and self.audit_agent_id:
                self.state.active_agent = self.audit_agent_id
                self._save_runtime()
                return
            if self._current_running_execution():
                self.state.active_agent = ""
                self._request_scheduler_yield()
            else:
                self.state.active_agent = self.execution_agent_id or ""
            if not self.state.active_agent and not self._current_running_execution():
                self._complete_mission()
            else:
                self._save_runtime()
            return

        if agent_id == self.execution_agent_id:
            execution_status = coerce_str(report.get("execution_status")).strip()
            if execution_status == "failed" or status == "failed":
                self.state.active_agent = ""
                self.mission.status = "failed"
                self.state.extra["failure_reason"] = coerce_str(report.get("failure_reason")).strip() or "execution background worker failed"
                self._set_runtime_status("failed")
                self._save_runtime()
                return
            if execution_status in {"launched", "running"}:
                self._set_pending_execution_brief(None)
                next_agent = ""
                if self._completed_execution_queue() and self.audit_agent_id:
                    next_agent = self.audit_agent_id
                elif (
                    not self._planned_slice_queue()
                    and not self._prefetch_completed()
                    and self.design_agent_id
                    and not self._current_running_agent(self.design_agent_id)
                ):
                    next_agent = self.design_agent_id
                self.state.active_agent = next_agent
                self._request_scheduler_yield()
                self._save_runtime()
                return
            self._set_pending_execution_brief(None)
            self.state.active_agent = self.audit_agent_id or ""
            if not self.state.active_agent:
                self._complete_mission()
            else:
                self._save_runtime()
            return

        if agent_id == self.audit_agent_id:
            if coerce_str(report.get("audit_status")).strip() in {"launched", "running"}:
                next_agent = ""
                if (
                    self._current_running_execution()
                    and not self._planned_slice_queue()
                    and not self._prefetch_completed()
                    and self.design_agent_id
                    and not self._current_running_agent(self.design_agent_id)
                ):
                    next_agent = self.design_agent_id
                self.state.active_agent = next_agent
                self._request_scheduler_yield()
                self._save_runtime()
                return
            audit_status = coerce_str(report.get("audit_status") or status).strip() or status
            if audit_status == "failed" or status == "failed":
                self.state.active_agent = ""
                self.mission.status = "failed"
                self.state.extra["failure_reason"] = "audit background worker failed"
                self._set_runtime_status("failed")
                self._save_runtime()
                return
            artifacts = report.get("artifacts", [])
            audit_artifact_path = str(artifacts[-1]) if isinstance(artifacts, list) and artifacts else ""
            if audit_artifact_path:
                self._consume_completed_agent(self.audit_agent_id or "audit", audit_artifact_path)
            audit_payload = self._load_json(audit_artifact_path) if audit_artifact_path else {}
            execution_artifact_path = ""
            if isinstance(audit_payload, Mapping):
                execution_artifact_path = coerce_str(audit_payload.get("execution_artifact_path")).strip()
            if execution_artifact_path:
                self._consume_completed_execution(execution_artifact_path)
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
                self._set_pending_execution_brief(None)
                self.state.extra.pop("last_failure_findings", None)
                self.state.extra.pop("failure_reason", None)
                try:
                    self._promote_execution_worktree(audit_design_contract)
                    self._release_execution_worktree(audit_design_contract)
                except WorktreeError as exc:
                    self.state.active_agent = ""
                    self.mission.status = "failed"
                    self.state.extra["failure_reason"] = f"supervisor could not promote the accepted worktree: {exc}"
                    self.state.extra["last_failure_findings"] = [str(exc)]
                    self._set_runtime_status("failed")
                    self._save_runtime()
                    return
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
                self._set_pending_execution_brief(None)
                self.state.extra["last_failure_findings"] = audit_findings
                self._release_execution_worktree(audit_design_contract)
                self.state.active_agent = self.design_agent_id or self._default_work_entry_agent()
                self._save_runtime()
                return
            reopen_streak = self._record_audit_reopen(
                audit_findings,
                design_contract=audit_design_contract,
            )
            self.state.extra["last_failure_findings"] = audit_findings
            if (
                coerce_str(audit_design_contract.get("execution_scope")).strip() == "external_project"
                and reopen_streak >= 2
            ):
                self._release_execution_worktree(audit_design_contract)
                self._auto_replan_stalled_slice(
                    audit_payload if isinstance(audit_payload, Mapping) else report,
                    repeat_count=reopen_streak,
                )
                return
            self._set_pending_execution_brief(
                self._build_execution_retry_brief(
                    audit_payload if isinstance(audit_payload, Mapping) else report,
                    reopen_streak=reopen_streak,
                )
            )
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
        self.state.extra.pop("scheduler_yield", None)
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
            agent_id = self.state.active_agent
            if not agent_id:
                break
            steps.extend(self._run_agent_until_stable(agent_id))
            if self._consume_scheduler_yield():
                break
        return SchedulerResult(
            status=self._runtime_status(),
            steps=steps,
            pending_gate_id=coerce_str(self.state.extra.get("pending_gate_id")) or None,
            mission=self.mission,
            state=self.state,
        )

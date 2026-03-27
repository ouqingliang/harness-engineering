from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

from ..runtime_state import coerce_str, utc_now
from .background_runtime import launch_background_agent
from .support import (
    DEFAULT_EXECUTION_OUTPUT,
    HARNESS_ROOT,
    _find_codex_executable,
    _git_status_snapshot,
    _normalize_text_list,
    _write_json,
)


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
    workspace_root: Path,
    canonical_project_root: Path,
    design_contract: Mapping[str, Any],
    baseline_docs: Sequence[str],
    planning_doc: str,
    human_decisions: Sequence[Any],
    supervisor_brief: Mapping[str, Any] | None = None,
) -> str:
    selected_phase = design_contract.get("selected_phase", {})
    if not isinstance(selected_phase, Mapping):
        selected_phase = {}
    lines = [
        "You are the Harness execution-agent for AIMA-refactor.",
        f"Assigned worktree: {workspace_root}",
        f"Canonical project root (supervisor-owned): {canonical_project_root}",
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
    if isinstance(supervisor_brief, Mapping) and supervisor_brief:
        findings = _normalize_text_list(supervisor_brief.get("findings", []))
        decision = coerce_str(supervisor_brief.get("decision")).strip()
        if decision:
            lines.append(f"- supervisor retry route: {decision}")
        if findings:
            lines.append("- audit findings to address before the next audit:")
            for item in findings[:8]:
                lines.append(f"  - {item}")
    lines.extend(
        [
            "",
            "Execution rules:",
            "- Treat the assigned worktree as the only writable repository root for this slice.",
            "- Do not edit files under the canonical project root directly.",
            "- Use subagents for code modification work whenever the implementation can be decomposed safely.",
            "- Modify the repository directly under the assigned worktree before claiming progress.",
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


def _prepare_execution_request(
    *,
    workspace_root: Path,
    canonical_project_root: Path,
    design_contract: Mapping[str, Any],
    baseline_docs: Sequence[str],
    planning_doc: str,
    human_decisions: Sequence[Any],
    supervisor_brief: Mapping[str, Any] | None,
    request_path: Path,
    result_path: Path,
) -> dict[str, Any]:
    prompt = _execution_prompt(
        workspace_root=workspace_root,
        canonical_project_root=canonical_project_root,
        design_contract=design_contract,
        baseline_docs=baseline_docs,
        planning_doc=planning_doc,
        human_decisions=human_decisions,
        supervisor_brief=supervisor_brief,
    )
    request_payload = {
        "workspace_root": str(workspace_root),
        "canonical_project_root": str(canonical_project_root),
        "baseline_docs": list(baseline_docs),
        "planning_doc": planning_doc,
        "design_contract": dict(design_contract),
        "supervisor_brief": dict(supervisor_brief) if isinstance(supervisor_brief, Mapping) else {},
        "prompt": prompt,
        "codex_executable": _find_codex_executable(),
        "schema_path": str(request_path.with_name(request_path.stem + "-schema.json")),
        "output_path": str(result_path.with_suffix(".message.json")),
        "recorded_at": utc_now(),
    }
    _write_json(request_path, request_payload)
    return request_payload


def _run_execution_subagent_from_saved_request(
    *,
    request_path: Path,
    result_path: Path,
    launcher_state_path: Path,
    launcher_run_path: Path,
) -> dict[str, Any]:
    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    workspace_root = Path(coerce_str(request_payload.get("workspace_root"))).resolve()
    prompt = coerce_str(request_payload.get("prompt")).strip()
    codex_executable = coerce_str(request_payload.get("codex_executable")).strip()
    started_at = coerce_str(request_payload.get("recorded_at")).strip() or utc_now()
    schema_path = Path(coerce_str(request_payload.get("schema_path")).strip())
    output_path = Path(coerce_str(request_payload.get("output_path")).strip())
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
            "pre_git_status": _git_status_snapshot(workspace_root),
            "post_git_status": _git_status_snapshot(workspace_root),
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
    pre_git_status = _git_status_snapshot(workspace_root)
    command = [
        codex_executable,
        "exec",
        prompt,
        "-C",
        str(workspace_root),
        "--dangerously-bypass-approvals-and-sandbox",
        "--output-schema",
        str(schema_path),
        "-o",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        cwd=str(workspace_root),
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
    post_git_status = _git_status_snapshot(workspace_root)
    payload = {
        "ok": completed.returncode == 0,
        "command": command,
        "cwd": str(workspace_root),
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


def _launch_execution_subagent(
    *,
    workspace_root: Path,
    canonical_project_root: Path,
    design_contract: Mapping[str, Any],
    baseline_docs: Sequence[str],
    planning_doc: str,
    human_decisions: Sequence[Any],
    supervisor_brief: Mapping[str, Any] | None,
    request_path: Path,
    result_path: Path,
    launcher_state_path: Path,
    launcher_run_path: Path,
) -> dict[str, Any]:
    request_payload = _prepare_execution_request(
        workspace_root=workspace_root,
        canonical_project_root=canonical_project_root,
        design_contract=design_contract,
        baseline_docs=baseline_docs,
        planning_doc=planning_doc,
        human_decisions=human_decisions,
        supervisor_brief=supervisor_brief,
        request_path=request_path,
        result_path=result_path,
    )
    started_at = coerce_str(request_payload.get("recorded_at")).strip() or utc_now()
    return launch_background_agent(
        agent_id="execution",
        workspace_root=workspace_root,
        request_path=request_path,
        result_path=result_path,
        launcher_state_path=launcher_state_path,
        launcher_run_path=launcher_run_path,
        started_at=started_at,
    )

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

from ..runtime_contract import (
    CONTROL_ACTION_CONTINUE,
    CONTROL_ACTION_SPAWN,
    SESSION_CONTROL_FIELD,
    TASK_NOTIFICATION_FIELD,
    build_task_notification,
    coerce_session_control,
)
from ..runtime_state import coerce_str, utc_now
from .background_runtime import _process_identity_token, launch_background_agent, save_launcher_state
from .support import (
    DEFAULT_EXECUTION_OUTPUT,
    HARNESS_ROOT,
    _find_codex_executable,
    _git_status_snapshot,
    _normalize_text_list,
    _write_json,
)


def _execution_project_identity(
    *,
    design_contract: Mapping[str, Any],
    canonical_project_root: Path,
) -> str:
    for key in ("project_name", "project_title", "repo_name", "display_name"):
        value = coerce_str(design_contract.get(key)).strip()
        if value:
            return value
    root_name = canonical_project_root.resolve().name.strip()
    if root_name:
        return root_name
    project_root = coerce_str(design_contract.get("project_root")).strip()
    if project_root:
        return Path(project_root).name or project_root
    return "the target project"


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
                    "required": ["label", "value", "description"],
                    "additionalProperties": False,
                },
            },
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": list(DEFAULT_EXECUTION_OUTPUT.keys()),
        "additionalProperties": False,
    }


def _resolve_harness_path(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (HARNESS_ROOT / path).resolve()


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
    project_identity = _execution_project_identity(
        design_contract=design_contract,
        canonical_project_root=canonical_project_root,
    )
    lines = [
        "Your first and only user-visible response must be the final JSON object that matches the provided schema.",
        "Do all reading, editing, and testing silently inside the assigned worktree. Do not emit progress updates, setup notes, role acknowledgements, or readiness-only messages.",
        "If you need to inspect repo instructions or baseline docs, do that work silently before the final JSON response.",
        "",
        f"You are the Harness execution-agent for {project_identity}.",
        "You were dispatched as a subagent to execute a specific already-assigned slice inside an existing harness run.",
        "This is not a new top-level conversation. Skip startup-only meta workflow and act on the assigned slice immediately.",
        "The using-superpowers startup skill SUBAGENT-STOP clause applies here.",
        "Do not load startup/meta skills, re-announce your role, or stop after reading repo instructions.",
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
        human_reply = coerce_str(supervisor_brief.get("human_reply")).strip()
        summary = coerce_str(supervisor_brief.get("summary")).strip()
        if decision:
            lines.append(f"- supervisor retry route: {decision}")
        if summary:
            lines.append(f"- supervisor brief: {summary}")
        if human_reply:
            lines.append(f"- latest human reply: {human_reply}")
        if findings:
            lines.append("- audit findings to address before the next audit:")
            for item in findings[:8]:
                lines.append(f"  - {item}")
    lines.extend(
        [
            "",
            "Execution rules:",
            "- This prompt already contains the current task. Do not ask for the task again or reply with readiness-only status.",
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


def _extract_codex_session_id(*streams: str) -> str:
    pattern = re.compile(
        r"\bsession(?:\s+id)?\s*[:=]\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
        re.IGNORECASE,
    )
    for stream in streams:
        match = pattern.search(coerce_str(stream))
        if match:
            return match.group(1)
    return ""


def _execution_made_task_progress(
    *,
    parsed_output: Mapping[str, Any],
    pre_git_status: Mapping[str, Any] | None,
    post_git_status: Mapping[str, Any] | None,
) -> bool:
    status = coerce_str(parsed_output.get("status")).strip().lower()
    summary = coerce_str(parsed_output.get("summary")).strip()
    if status not in {"", "unknown"}:
        return True
    if summary:
        return True
    if bool(parsed_output.get("needs_human")):
        return True
    if _normalize_text_list(parsed_output.get("changed_paths", [])):
        return True
    if _normalize_text_list(parsed_output.get("verification_notes", [])):
        return True
    if _normalize_text_list(parsed_output.get("notes", [])):
        return True
    pre_entries = list(pre_git_status.get("entries", [])) if isinstance(pre_git_status, Mapping) else []
    post_entries = list(post_git_status.get("entries", [])) if isinstance(post_git_status, Mapping) else []
    return bool(pre_entries or post_entries)


def _session_requested_task_again_without_progress(
    *,
    session_id: str,
    parsed_output: Mapping[str, Any],
    stdout: str,
    stderr: str,
    pre_git_status: Mapping[str, Any] | None,
    post_git_status: Mapping[str, Any] | None,
) -> bool:
    if not coerce_str(session_id).strip():
        return False
    if _execution_made_task_progress(
        parsed_output=parsed_output,
        pre_git_status=pre_git_status,
        post_git_status=post_git_status,
    ):
        return False
    combined = f"{coerce_str(stdout)}\n{coerce_str(stderr)}".lower()
    ready_markers = (
        "send the first task when you're ready",
        "send the specific task when you're ready",
        "send the task you want executed",
        "provide the task",
        "provide the specific task",
        "provide the task, and i'll handle it directly",
        "provide the task, and i’ll handle it directly",
        "provide the next task, and i'll handle it directly",
        "provide the next task, and i’ll handle it directly",
        "give me the task",
        "give me the next concrete task",
        "give me the next concrete task, and i'll execute it end-to-end",
        "give me the next concrete task, and i’ll execute it end-to-end",
        "send the first task when you’re ready",
        "send the specific task when you’re ready",
        "i'll carry it through here",
        "i’ll carry it through here",
        "send the first task when you",
        "send the specific task when you",
        "using `using-superpowers` to align",
        "aligning to the repo and harness role first",
        "loading the required base skill",
        "i'll follow the repo instructions and pull in the relevant local skills",
        "i鈥檒l follow the repo instructions and pull in the relevant local skills",
        "configured as the harness execution-agent",
        "i'll follow the local skill rules",
        "i鈥檒l follow the local skill rules",
        "aligned with the repo instructions in",
    )
    if any(marker in combined for marker in ready_markers):
        return True
    # Any schema-empty, zero-progress execution reply should be retried as a
    # fresh task dispatch instead of being treated as a terminal attempt.
    return True


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
    resolved_request_path = _resolve_harness_path(request_path)
    resolved_result_path = _resolve_harness_path(result_path)
    resolved_schema_path = resolved_request_path.with_name(resolved_request_path.stem + "-schema.json")
    resolved_output_path = resolved_result_path.with_suffix(".message.json")
    prompt = _execution_prompt(
        workspace_root=workspace_root,
        canonical_project_root=canonical_project_root,
        design_contract=design_contract,
        baseline_docs=baseline_docs,
        planning_doc=planning_doc,
        human_decisions=human_decisions,
        supervisor_brief=supervisor_brief,
    )
    resume_session_id = (
        coerce_str(supervisor_brief.get("resume_session_id")).strip()
        if isinstance(supervisor_brief, Mapping)
        else ""
    )
    session_control = coerce_session_control(
        {
            "action": CONTROL_ACTION_CONTINUE if resume_session_id else CONTROL_ACTION_SPAWN,
            "session": resume_session_id,
        }
    )
    request_payload = {
        "workspace_root": str(workspace_root),
        "canonical_project_root": str(canonical_project_root),
        "baseline_docs": list(baseline_docs),
        "planning_doc": planning_doc,
        "design_contract": dict(design_contract),
        "supervisor_brief": dict(supervisor_brief) if isinstance(supervisor_brief, Mapping) else {},
        SESSION_CONTROL_FIELD: session_control,
        "resume_session_id": resume_session_id,
        "execution_artifact_path": (
            coerce_str(supervisor_brief.get("execution_artifact_path")).strip()
            if isinstance(supervisor_brief, Mapping)
            else ""
        ),
        "prompt": prompt,
        "codex_executable": _find_codex_executable(),
        "schema_path": str(resolved_schema_path),
        "output_path": str(resolved_output_path),
        "recorded_at": utc_now(),
    }
    _write_json(resolved_request_path, request_payload)
    return request_payload


def _run_execution_subagent_from_saved_request(
    *,
    request_path: Path,
    result_path: Path,
    launcher_state_path: Path,
    launcher_run_path: Path,
) -> dict[str, Any]:
    request_path = request_path.resolve()
    result_path = result_path.resolve()
    launcher_state_path = launcher_state_path.resolve()
    launcher_run_path = launcher_run_path.resolve()
    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    workspace_root = Path(coerce_str(request_payload.get("workspace_root"))).resolve()
    prompt = coerce_str(request_payload.get("prompt")).strip()
    codex_executable = coerce_str(request_payload.get("codex_executable")).strip()
    resume_session_id = coerce_str(request_payload.get("resume_session_id")).strip()
    request_session_control = coerce_session_control(
        request_payload.get(SESSION_CONTROL_FIELD)
        if request_payload.get(SESSION_CONTROL_FIELD) is not None
        else {
            "action": CONTROL_ACTION_CONTINUE if resume_session_id else CONTROL_ACTION_SPAWN,
            "session": resume_session_id,
        }
    )
    if request_session_control.get("action") == CONTROL_ACTION_CONTINUE:
        resume_session_id = coerce_str(request_session_control.get("session")).strip() or resume_session_id
    else:
        resume_session_id = ""
    started_at = coerce_str(request_payload.get("recorded_at")).strip() or utc_now()
    schema_path = _resolve_harness_path(Path(coerce_str(request_payload.get("schema_path")).strip()))
    output_path = _resolve_harness_path(Path(coerce_str(request_payload.get("output_path")).strip()))
    launcher_state_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_run_path.parent.mkdir(parents=True, exist_ok=True)
    save_launcher_state(
        launcher_state_path=launcher_state_path,
        request_path=request_path,
        result_path=result_path,
        payload={
            "status": "running",
            "active_run_id": launcher_run_path.stem,
            "last_request_path": str(request_path),
            "last_result_path": str(result_path),
            "last_cycle_id": request_path.parent.name,
            "started_at": started_at,
            "heartbeat_at": utc_now(),
            "pid": os.getpid(),
            "pid_executable": sys.executable,
            "pid_identity": _process_identity_token(os.getpid()),
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
            "session_id": resume_session_id,
            TASK_NOTIFICATION_FIELD: build_task_notification(
                session=resume_session_id,
                status="terminal",
                summary="codex executable was not found on PATH",
                result=dict(DEFAULT_EXECUTION_OUTPUT),
                output_file=output_path,
            ),
        }
        _write_json(result_path, payload)
        save_launcher_state(
            launcher_state_path=launcher_state_path,
            request_path=request_path,
            result_path=result_path,
            payload={
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

    pre_git_status = _git_status_snapshot(workspace_root)
    if request_session_control.get("action") == CONTROL_ACTION_CONTINUE and resume_session_id:
        command = [
            codex_executable,
            "exec",
            "-C",
            str(workspace_root),
            "resume",
            resume_session_id,
            "-",
            "--dangerously-bypass-approvals-and-sandbox",
            "-o",
            str(output_path),
        ]
    else:
        schema_path.write_text(
            json.dumps(_execution_output_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        command = [
            codex_executable,
            "exec",
            "-",
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
        input=prompt,
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
    session_id = _extract_codex_session_id(completed.stdout, completed.stderr) or resume_session_id
    session_state = (
        "requested_task_again"
        if _session_requested_task_again_without_progress(
            session_id=session_id,
            parsed_output=parsed_output,
            stdout=completed.stdout,
            stderr=completed.stderr,
            pre_git_status=pre_git_status,
            post_git_status=post_git_status,
        )
        else "terminal"
    )
    task_notification = build_task_notification(
        session=session_id,
        status=session_state,
        summary=coerce_str(parsed_output.get("summary")).strip() or f"Execution session reported {session_state}.",
        result=parsed_output,
        output_file=output_path,
    )
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
        "session_id": session_id,
        "session_state": session_state,
        TASK_NOTIFICATION_FIELD: task_notification,
    }
    _write_json(result_path, payload)
    _write_json(launcher_run_path, payload)
    save_launcher_state(
        launcher_state_path=launcher_state_path,
        request_path=request_path,
        result_path=result_path,
        payload={
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

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..project_context import project_root_from_doc_root
from ..runtime_state import coerce_bool, coerce_int, coerce_str, utc_now
from ..runner_bridge import RunnerTurn
from .background_runtime import launch_background_agent, load_launcher_status, running_status
from .support import DEFAULT_EXECUTION_OUTPUT, _normalize_text_list, _write_json


def execute_turn(
    scheduler: Any,
    turn: RunnerTurn,
    *,
    new_id: Callable[[str], str],
    preferred_planning_doc: Callable[[Mapping[str, Any]], str],
    design_contract_from_docs: Callable[..., dict[str, Any]],
    contract_for_supervisor_decision: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]],
    count_sequence_items: Callable[[Any], int],
    cleanup_runtime_temp_files: Callable[[Path], list[dict[str, str]]],
    project_hygiene_findings: Callable[[Path], list[dict[str, str]]],
    launch_execution_subagent: Callable[..., dict[str, Any]],
    run_verification_command: Callable[[Mapping[str, Any]], dict[str, Any]],
    verification_acceptance_from_runs: Callable[..., tuple[bool, list[str]]],
    verification_scope_findings: Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], list[str]],
    verification_specs: Callable[..., list[dict[str, Any]]],
) -> dict[str, Any]:
    agent_id = turn.agent_spec["id"]
    inputs = turn.handoff.get("inputs", {})
    doc_bundle = turn.mission.get("doc_bundle", {})
    latest_artifacts = inputs.get("latest_artifacts", {})
    doc_root = Path(turn.mission.get("doc_root", scheduler.paths.memory_root)).resolve()
    project_root = (
        Path(coerce_str(turn.mission.get("project_root") or inputs.get("project_root"))).resolve()
        if coerce_str(turn.mission.get("project_root") or inputs.get("project_root")).strip()
        else project_root_from_doc_root(doc_root)
    )

    if agent_id == scheduler.communication_agent_id:
        return _execute_communication_turn(scheduler, turn, inputs)
    if agent_id == scheduler.design_agent_id:
        return _execute_design_turn(
            scheduler,
            turn,
            inputs,
            doc_bundle=doc_bundle,
            doc_root=doc_root,
            project_root=project_root,
            new_id=new_id,
            preferred_planning_doc=preferred_planning_doc,
            design_contract_from_docs=design_contract_from_docs,
            contract_for_supervisor_decision=contract_for_supervisor_decision,
        )
    if agent_id == scheduler.execution_agent_id:
        return _execute_execution_turn(
            scheduler,
            turn,
            inputs,
            latest_artifacts=latest_artifacts,
            doc_root=doc_root,
            project_root=project_root,
            new_id=new_id,
            launch_execution_subagent=launch_execution_subagent,
            run_verification_command=run_verification_command,
            verification_acceptance_from_runs=verification_acceptance_from_runs,
            verification_scope_findings=verification_scope_findings,
            verification_specs=verification_specs,
        )
    if agent_id == scheduler.audit_agent_id:
        return _execute_audit_turn(
            scheduler,
            turn,
            latest_artifacts=latest_artifacts,
            count_sequence_items=count_sequence_items,
            verification_acceptance_from_runs=verification_acceptance_from_runs,
            verification_scope_findings=verification_scope_findings,
        )
    if agent_id == scheduler.cleanup_agent_id:
        return _execute_cleanup_turn(
            scheduler,
            turn,
            inputs,
            project_root=project_root,
            cleanup_runtime_temp_files=cleanup_runtime_temp_files,
            project_hygiene_findings=project_hygiene_findings,
        )
    return {
        "status": "completed",
        "summary": f"Processed {agent_id}.",
        "artifacts": [],
    }


def _execute_communication_turn(
    scheduler: Any,
    turn: RunnerTurn,
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
    artifact_path = scheduler._artifact_path(turn, "idle")
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


def _execute_design_turn(
    scheduler: Any,
    turn: RunnerTurn,
    inputs: Mapping[str, Any],
    *,
    doc_bundle: Mapping[str, Any],
    doc_root: Path,
    project_root: Path,
    new_id: Callable[[str], str],
    preferred_planning_doc: Callable[[Mapping[str, Any]], str],
    design_contract_from_docs: Callable[..., dict[str, Any]],
    contract_for_supervisor_decision: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    if int(doc_bundle.get("doc_count", 0)) == 0:
        return {
            "status": "blocked",
            "summary": "No UTF-8 docs were discovered under the provided doc root.",
            "questions": [
                {
                    "question_id": new_id("question"),
                    "agent": "design",
                    "question": "褰撳墠 doc 涓荤洰褰曚笅娌℃湁鍙鐨勬€讳綋瑙勫垝/璁捐鏂囨。锛屾槸鍚﹂渶瑕侀噸鏂版寚瀹氭枃妗ｇ洰褰曪紵",
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
        selected_primary_doc = preferred_planning_doc(doc_bundle)
    gate_signals = doc_bundle.get("gate_signals", [])
    human_decisions = inputs.get("human_decisions", [])
    if gate_signals and not human_decisions:
        gate_signal = gate_signals[0]
        return {
            "status": "blocked",
            "summary": "Design detected a decision gate in the planning docs.",
            "questions": [
                {
                    "question_id": new_id("question"),
                    "agent": "design",
                    "question": f"{gate_signal['relative_path']}:{gate_signal['line_number']} ???????????{gate_signal['prompt']}",
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
                    "question_id": new_id("question"),
                    "agent": "design",
                    "question": "搴旇浼樺厛浠ュ摢涓鍒掓枃妗ｄ綔涓哄綋鍓嶄富绾垮叆鍙ｏ紵",
                    "blocking": False,
                    "importance": "low",
                    "tags": ["path"],
                    "context": {"candidate_paths": candidate_paths},
                }
            ],
        }
    agent_id = scheduler.design_agent_id or "design"
    pending_supervisor_decision = inputs.get("pending_supervisor_decision")
    design_run_key = f"design::{selected_primary_doc or turn.cycle_id}"
    existing_run = scheduler._current_running_agent(agent_id)
    worktree_entry = scheduler._ensure_agent_worktree(
        agent_id=agent_id,
        slice_key=design_run_key,
        canonical_project_root=project_root,
        phase_title="design",
    )
    design_workspace_root = Path(coerce_str(worktree_entry.get("path")).strip()).resolve()
    launcher_dir = scheduler.paths.artifacts_dir / "launchers" / "design"
    if existing_run is None:
        request_artifact_path = scheduler._artifact_path(turn, "design-request")
        result_artifact_path = scheduler._artifact_path(turn, "design-result")
        launcher_state_path = launcher_dir / "state.json"
        launcher_run_path = launcher_dir / "runs" / f"{turn.cycle_id}-{turn.sequence:02d}.json"
    else:
        request_artifact_path = Path(coerce_str(existing_run.get("request_path")).strip())
        result_artifact_path = Path(coerce_str(existing_run.get("result_path")).strip())
        launcher_state_path = Path(coerce_str(existing_run.get("launcher_state_path")).strip())
        launcher_run_path = Path(coerce_str(existing_run.get("launcher_run_path")).strip())
        if coerce_str(existing_run.get("worktree_path")).strip():
            design_workspace_root = Path(coerce_str(existing_run.get("worktree_path")).strip()).resolve()
    if existing_run is None:
        request_payload = {
            "doc_bundle": dict(doc_bundle) if isinstance(doc_bundle, Mapping) else {},
            "doc_root": str(doc_root),
            "project_root": str(project_root),
            "selected_primary_doc": selected_primary_doc,
            "completed_slices": list(inputs.get("completed_slices", [])) if isinstance(inputs.get("completed_slices", []), list) else [],
            "maintenance_findings": _normalize_text_list(inputs.get("maintenance_findings", [])),
            "pending_supervisor_decision": dict(pending_supervisor_decision) if isinstance(pending_supervisor_decision, Mapping) else {},
            "planned_slice_queue": scheduler._planned_slice_queue(),
            "assigned_worktree": str(design_workspace_root),
            "recorded_at": utc_now(),
        }
        _write_json(request_artifact_path, request_payload)
        launch_result = launch_background_agent(
            agent_id=agent_id,
            workspace_root=design_workspace_root,
            request_path=request_artifact_path,
            result_path=result_artifact_path,
            launcher_state_path=launcher_state_path,
            launcher_run_path=launcher_run_path,
            started_at=coerce_str(request_payload.get("recorded_at")).strip() or utc_now(),
        )
        scheduler._upsert_running_agent(
            {
                "agent_id": agent_id,
                "slice_key": design_run_key,
                "phase_title": "design",
                "status": "running" if launch_result.get("ok") else "failed",
                "started_at": launch_result.get("started_at", utc_now()),
                "request_path": str(request_artifact_path),
                "result_path": str(result_artifact_path),
                "launcher_state_path": str(launcher_state_path),
                "launcher_run_path": str(launcher_run_path),
                "project_root": str(project_root),
                "worktree_path": str(design_workspace_root),
                "brief": f"primary_doc={selected_primary_doc}" if selected_primary_doc else "derive the next design contract",
                "pid": launch_result.get("pid"),
            }
        )
        scheduler._append_recent_event(
            kind="design_launch",
            summary="Design launched in background.",
            details={"worktree_path": str(design_workspace_root), "selected_primary_doc": selected_primary_doc},
        )
        if launch_result.get("ok") and not result_artifact_path.exists():
            return {
                "status": "running",
                "summary": "Design launched in background.",
                "design_status": "launched",
                "artifacts": [str(request_artifact_path)],
            }
    launcher_status = load_launcher_status(launcher_state_path)
    if not result_artifact_path.exists():
        launcher_run_status = running_status(launcher_status)
        if launcher_run_status in {"failed", "completed"}:
            scheduler._remove_running_agent(agent_id, design_run_key)
            scheduler._release_agent_worktree(
                agent_id=agent_id,
                slice_key=design_run_key,
                canonical_project_root=str(project_root),
            )
            failure_reason = coerce_str(launcher_status.get("stale_reason")).strip() or "design background worker exited without writing a result artifact"
            return {
                "status": "failed",
                "summary": "Design background worker failed.",
                "design_status": "failed",
                "artifacts": [str(request_artifact_path)],
                "failure_reason": failure_reason,
            }
        scheduler._upsert_running_agent(
            {
                "agent_id": agent_id,
                "slice_key": design_run_key,
                "phase_title": "design",
                "status": launcher_run_status,
                "last_polled_at": utc_now(),
                "request_path": str(request_artifact_path),
                "result_path": str(result_artifact_path),
                "launcher_state_path": str(launcher_state_path),
                "launcher_run_path": str(launcher_run_path),
                "project_root": str(project_root),
                "worktree_path": str(design_workspace_root),
                "brief": f"primary_doc={selected_primary_doc}" if selected_primary_doc else "derive the next design contract",
            }
        )
        return {
            "status": "running",
            "summary": "Design is still running in background.",
            "design_status": "running",
            "artifacts": [str(request_artifact_path)],
        }
    try:
        design_result = scheduler._load_json(str(result_artifact_path))
    except json.JSONDecodeError:
        return {
            "status": "running",
            "summary": "Design is finalizing artifacts.",
            "design_status": "running",
            "artifacts": [str(request_artifact_path)],
        }
    scheduler._remove_running_agent(agent_id, design_run_key)
    scheduler._release_agent_worktree(
        agent_id=agent_id,
        slice_key=design_run_key,
        canonical_project_root=str(project_root),
    )
    if not coerce_bool(design_result.get("ok"), False):
        return {
            "status": "failed",
            "summary": "Design background worker failed.",
            "design_status": "failed",
            "artifacts": [str(request_artifact_path), str(result_artifact_path)],
        }
    design_contract = design_result.get("design_contract", {})
    if not isinstance(design_contract, Mapping):
        design_contract = {}
    queue_payload = design_result.get("planned_slice_queue", [])
    scheduler.mission.extra["selected_primary_doc"] = coerce_str(design_result.get("selected_primary_doc")).strip() or selected_primary_doc
    scheduler.mission.extra["project_root"] = str(project_root)
    scheduler._set_planned_slice_queue(queue_payload if isinstance(queue_payload, list) else [])
    scheduler._set_prefetch_completed(coerce_bool(design_result.get("prefetch_completed"), False))
    if isinstance(pending_supervisor_decision, Mapping) and pending_supervisor_decision:
        scheduler._consume_pending_supervisor_decision(design_contract)
    artifact_path = scheduler._artifact_path(turn, "contract")
    _write_json(artifact_path, design_contract)
    selected_phase = design_contract.get("selected_phase", {})
    phase_title = (
        coerce_str(selected_phase.get("title")).strip()
        if isinstance(selected_phase, Mapping)
        else ""
    ) or "design"
    scheduler._queue_completed_agent(
        {
            "agent_id": agent_id,
            "artifact_path": str(artifact_path),
            "slice_key": coerce_str(design_contract.get("slice_key")).strip() or design_run_key,
            "phase_title": phase_title,
            "status": "waiting_supervisor",
            "summary": "Completed design contract is waiting for supervisor routing.",
        }
    )
    if coerce_str(design_result.get("design_status")).strip() == "completed":
        return {
            "status": "completed",
            "summary": "Design found no remaining planned slices to execute.",
            "design_status": "completed",
            "artifacts": [str(request_artifact_path), str(result_artifact_path), str(artifact_path)],
        }
    return {
        "status": "completed",
        "summary": f"Prepared the next slice from {doc_bundle.get('doc_count', 0)} document(s).",
        "design_status": "ready",
        "artifacts": [str(request_artifact_path), str(result_artifact_path), str(artifact_path)],
    }


def _execute_execution_turn(
    scheduler: Any,
    turn: RunnerTurn,
    inputs: Mapping[str, Any],
    *,
    latest_artifacts: Mapping[str, Any],
    doc_root: Path,
    project_root: Path,
    new_id: Callable[[str], str],
    launch_execution_subagent: Callable[..., dict[str, Any]],
    run_verification_command: Callable[[Mapping[str, Any]], dict[str, Any]],
    verification_acceptance_from_runs: Callable[..., tuple[bool, list[str]]],
    verification_scope_findings: Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], list[str]],
    verification_specs: Callable[..., list[dict[str, Any]]],
) -> dict[str, Any]:
    design_artifacts = latest_artifacts.get("design", [])
    design_contract = scheduler._load_json(design_artifacts[-1]) if design_artifacts else {}
    if not isinstance(design_contract, Mapping) or not design_contract:
        return {
            "status": "blocked",
            "summary": "Execution could not find a usable design contract.",
            "questions": [
                {
                    "question_id": new_id("question"),
                    "agent": "execution",
                    "question": "Execution is missing a valid design contract. Should supervisor send this slice back to design?",
                    "blocking": False,
                    "importance": "medium",
                    "tags": ["path"],
                    "context": {"reason": "missing_design_contract"},
                }
            ],
            "artifacts": [],
        }
    canonical_project_root = Path(coerce_str(design_contract.get("project_root") or project_root)).resolve()
    supervisor_brief = (
        dict(inputs.get("pending_execution_brief", {}))
        if isinstance(inputs.get("pending_execution_brief", {}), Mapping)
        else {}
    )
    baseline_docs = _normalize_text_list(design_contract.get("baseline_docs", []))
    planning_doc = coerce_str(design_contract.get("selected_planning_doc")).strip()
    selected_phase = design_contract.get("selected_phase", {})
    if not isinstance(selected_phase, Mapping):
        selected_phase = {}
    slice_key = coerce_str(design_contract.get("slice_key")).strip()
    phase_title = coerce_str(selected_phase.get("title")).strip() or "the active slice"
    worktree_entry = scheduler._ensure_execution_worktree(
        slice_key=slice_key or f"execution-{turn.cycle_id}-{turn.sequence}",
        canonical_project_root=canonical_project_root,
        phase_title=phase_title,
    )
    execution_workspace_root = Path(coerce_str(worktree_entry.get("path")).strip()).resolve()
    execution_contract = dict(design_contract)
    execution_contract["canonical_project_root"] = str(canonical_project_root)
    execution_contract["assigned_worktree"] = str(execution_workspace_root)
    execution_contract["worktree_name"] = coerce_str(worktree_entry.get("name")).strip()
    if supervisor_brief:
        execution_contract["supervisor_retry_brief"] = supervisor_brief
    active_run = scheduler._find_running_execution(slice_key) if slice_key else None
    if active_run is None:
        current_run = scheduler._current_running_execution()
        if current_run is not None and slice_key and coerce_str(current_run.get("slice_key")).strip() != slice_key:
            active_run = current_run
    launcher_dir = scheduler.paths.artifacts_dir / "launchers" / "codex_exec"
    if active_run is not None:
        request_artifact_path = Path(coerce_str(active_run.get("request_path")).strip())
        result_artifact_path = Path(coerce_str(active_run.get("result_path")).strip())
        launcher_state_path = Path(coerce_str(active_run.get("launcher_state_path")).strip())
        launcher_run_path = Path(coerce_str(active_run.get("launcher_run_path")).strip())
        if coerce_str(active_run.get("worktree_path")).strip():
            execution_workspace_root = Path(coerce_str(active_run.get("worktree_path")).strip()).resolve()
            execution_contract["assigned_worktree"] = str(execution_workspace_root)
    else:
        request_artifact_path = scheduler._artifact_path(turn, "codex-request")
        result_artifact_path = scheduler._artifact_path(turn, "codex-result")
        launcher_state_path = launcher_dir / "state.json"
        launcher_run_path = launcher_dir / "runs" / f"{turn.cycle_id}-{turn.sequence:02d}.json"
    if active_run is None:
        launch_result = launch_execution_subagent(
            workspace_root=execution_workspace_root,
            canonical_project_root=canonical_project_root,
            design_contract=execution_contract,
            baseline_docs=baseline_docs,
            planning_doc=planning_doc,
            human_decisions=inputs.get("human_decisions", []),
            supervisor_brief=supervisor_brief,
            request_path=request_artifact_path,
            result_path=result_artifact_path,
            launcher_state_path=launcher_state_path,
            launcher_run_path=launcher_run_path,
        )
        scheduler._upsert_running_execution(
            {
                "slice_key": slice_key,
                "phase_title": phase_title,
                "status": "running" if launch_result.get("ok") else "failed",
                "started_at": launch_result.get("started_at", utc_now()),
                "request_path": str(request_artifact_path),
                "result_path": str(result_artifact_path),
                "launcher_state_path": str(launcher_state_path),
                "launcher_run_path": str(launcher_run_path),
                "design_contract_path": str(design_artifacts[-1]) if design_artifacts else "",
                "project_root": str(canonical_project_root),
                "worktree_path": str(execution_workspace_root),
                "pid": launch_result.get("pid"),
            }
        )
        scheduler._append_recent_event(
            kind="execution_launch",
            summary=f"Execution launched in background for {phase_title}.",
            details={
                "slice_key": slice_key,
                "project_root": str(canonical_project_root),
                "worktree_path": str(execution_workspace_root),
            },
        )
        if launch_result.get("ok") and not result_artifact_path.exists():
            return {
                "status": "running",
                "summary": f"Execution launched in background for {phase_title}.",
                "execution_status": "launched",
                "slice_key": slice_key,
                "phase_title": phase_title,
                "artifacts": [],
            }
    launcher_status = load_launcher_status(launcher_state_path)
    if not result_artifact_path.exists():
        launcher_run_status = running_status(launcher_status)
        if launcher_run_status in {"failed", "completed"}:
            scheduler._remove_running_execution(slice_key)
            failure_reason = coerce_str(launcher_status.get("stale_reason")).strip() or f"execution background worker exited without writing a result artifact for {phase_title}"
            return {
                "status": "failed",
                "summary": f"Execution background worker failed for {phase_title}.",
                "execution_status": "failed",
                "slice_key": slice_key,
                "phase_title": phase_title,
                "artifacts": [str(request_artifact_path)],
                "failure_reason": failure_reason,
            }
        scheduler._upsert_running_execution(
            {
                "slice_key": slice_key,
                "phase_title": phase_title,
                "status": launcher_run_status,
                "last_polled_at": utc_now(),
                "request_path": str(request_artifact_path),
                "result_path": str(result_artifact_path),
                "launcher_state_path": str(launcher_state_path),
                "launcher_run_path": str(launcher_run_path),
                "design_contract_path": str(design_artifacts[-1]) if design_artifacts else "",
                "project_root": str(canonical_project_root),
                "worktree_path": str(execution_workspace_root),
            }
        )
        return {
            "status": "running",
            "summary": f"Execution is still running in background for {phase_title}.",
            "execution_status": "running",
            "slice_key": slice_key,
            "phase_title": phase_title,
            "artifacts": [],
        }
    try:
        execution_result = scheduler._load_json(str(result_artifact_path))
    except json.JSONDecodeError:
        return {
            "status": "running",
            "summary": f"Execution is finalizing artifacts for {phase_title}.",
            "execution_status": "running",
            "slice_key": slice_key,
            "phase_title": phase_title,
            "artifacts": [],
        }
    scheduler._remove_running_execution(slice_key)
    execution_output = execution_result.get("parsed_output", {})
    if not isinstance(execution_output, Mapping):
        execution_output = dict(DEFAULT_EXECUTION_OUTPUT)
    session_state = coerce_str(execution_result.get("session_state")).strip().lower()
    resume_session_id = coerce_str(execution_result.get("session_id")).strip()
    if session_state == "requested_task_again":
        artifact_path = scheduler._artifact_path(turn, "execution")
        current_attempt = coerce_int(supervisor_brief.get("restart_attempt"), 0) if isinstance(supervisor_brief, Mapping) else 0
        resume_same_session = current_attempt == 0 and bool(resume_session_id)
        resume_brief = {
            "brief_id": new_id("execution-restart"),
            "decision": "continue_current_slice" if resume_same_session else "restart_current_slice_session",
            "slice_key": slice_key,
            "phase_title": phase_title,
            "resume_session_id": resume_session_id if resume_same_session else "",
            "execution_artifact_path": str(artifact_path),
            "restart_attempt": current_attempt + 1,
            "created_at": utc_now(),
            "summary": (
                f"Resume the same execution session for {phase_title}; it asked for the task again before making progress."
                if resume_same_session
                else f"Start a fresh execution session for {phase_title}; the previous session asked for the task again without making progress."
            ),
        }
        if resume_session_id and not resume_same_session:
            resume_brief["previous_session_id"] = resume_session_id
        _write_json(
            artifact_path,
            {
                "goal": turn.mission.get("goal", ""),
                "project_root": str(canonical_project_root),
                "workspace_root": str(execution_workspace_root),
                "selected_primary_doc": execution_contract.get("selected_primary_doc") or inputs.get("selected_primary_doc", ""),
                "design_contract": execution_contract,
                "supervisor_brief": supervisor_brief,
                "execution_subagent": execution_result,
                "execution_output": execution_output,
                "verification_expectation": execution_contract.get("verification_expectation", []),
                "verification_specs": [],
                "verification_commands": [],
                "work_items": execution_contract.get("work_items", []),
                "target_paths": execution_contract.get("target_paths", []),
                "verification_runs": [],
                "verification_status": "restart_pending",
                "verification_findings": [],
                "resume_brief": resume_brief,
                "recorded_at": utc_now(),
            },
        )
        return {
            "status": "running",
            "summary": (
                f"Execution session asked for the task again for {phase_title}; resuming the same session once with the existing task."
                if resume_same_session
                else f"Execution session asked for the task again for {phase_title}; restarting the same slice in a fresh session."
            ),
            "execution_status": "paused",
            "slice_key": slice_key,
            "phase_title": phase_title,
            "resume_brief": resume_brief,
            "execution_artifact_path": str(artifact_path),
            "artifacts": [str(request_artifact_path), str(result_artifact_path), str(artifact_path)],
        }
    if session_state == "ready_for_brief":
        artifact_path = scheduler._artifact_path(turn, "execution")
        current_attempt = coerce_int(supervisor_brief.get("resume_attempt"), 0) if isinstance(supervisor_brief, Mapping) else 0
        next_attempt = current_attempt + 1
        resume_same_session = current_attempt == 0 and bool(resume_session_id)
        resume_brief = {
            "brief_id": new_id("execution-resume"),
            "decision": "continue_current_slice" if resume_same_session else "restart_current_slice_session",
            "slice_key": slice_key,
            "phase_title": phase_title,
            "resume_session_id": resume_session_id if resume_same_session else "",
            "execution_artifact_path": str(artifact_path),
            "resume_attempt": next_attempt,
            "created_at": utc_now(),
            "summary": (
                f"Continue the same execution session for {phase_title}. This is still the current task, not a new task."
                if resume_same_session
                else f"Start a fresh execution session for {phase_title} because the last paused session never accepted the task brief."
            ),
        }
        if resume_session_id and not resume_same_session:
            resume_brief["previous_resume_session_id"] = resume_session_id
        _write_json(
            artifact_path,
            {
                "goal": turn.mission.get("goal", ""),
                "project_root": str(canonical_project_root),
                "workspace_root": str(execution_workspace_root),
                "selected_primary_doc": execution_contract.get("selected_primary_doc") or inputs.get("selected_primary_doc", ""),
                "design_contract": execution_contract,
                "supervisor_brief": supervisor_brief,
                "execution_subagent": execution_result,
                "execution_output": execution_output,
                "verification_expectation": execution_contract.get("verification_expectation", []),
                "verification_specs": [],
                "verification_commands": [],
                "work_items": execution_contract.get("work_items", []),
                "target_paths": execution_contract.get("target_paths", []),
                "verification_runs": [],
                "verification_status": "resume_pending",
                "verification_findings": [],
                "resume_brief": resume_brief,
                "recorded_at": utc_now(),
            },
        )
        return {
            "status": "running",
            "summary": (
                f"Execution session is ready to continue for {phase_title}."
                if resume_same_session
                else f"Execution needs a fresh session restart for {phase_title} after a no-task reply."
            ),
            "execution_status": "paused",
            "slice_key": slice_key,
            "phase_title": phase_title,
            "resume_brief": resume_brief,
            "execution_artifact_path": str(artifact_path),
            "artifacts": [str(request_artifact_path), str(result_artifact_path), str(artifact_path)],
        }
    needs_human = bool(execution_output.get("needs_human"))
    if needs_human:
        artifact_path = scheduler._artifact_path(turn, "execution")
        _write_json(
            artifact_path,
            {
                "goal": turn.mission.get("goal", ""),
                "project_root": str(canonical_project_root),
                "workspace_root": str(execution_workspace_root),
                "selected_primary_doc": execution_contract.get("selected_primary_doc") or inputs.get("selected_primary_doc", ""),
                "design_contract": execution_contract,
                "supervisor_brief": supervisor_brief,
                "execution_subagent": execution_result,
                "execution_output": execution_output,
                "verification_expectation": execution_contract.get("verification_expectation", []),
                "verification_specs": [],
                "verification_commands": [],
                "work_items": execution_contract.get("work_items", []),
                "target_paths": execution_contract.get("target_paths", []),
                "verification_runs": [],
                "verification_status": "pending_human",
                "verification_findings": [],
                "session_state": session_state,
                "recorded_at": utc_now(),
            },
        )
        return {
            "status": "blocked",
            "summary": coerce_str(execution_output.get("summary")).strip() or "Execution needs a decision before it can continue.",
            "questions": [
                {
                    "question_id": new_id("question"),
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
                        "resume_session_id": resume_session_id,
                        "execution_artifact_path": str(artifact_path),
                    },
                }
            ],
            "artifacts": [str(request_artifact_path), str(result_artifact_path), str(artifact_path)],
        }
    specs = verification_specs(
        execution_contract,
        project_root=execution_workspace_root,
        doc_root=doc_root,
    )
    verification_runs = [run_verification_command(spec) for spec in specs]
    verification_ok, verification_findings = verification_acceptance_from_runs(
        verification_runs,
        expected_count=len(specs),
    )
    scope_findings = verification_scope_findings(execution_contract, verification_runs)
    if scope_findings:
        verification_ok = False
        verification_findings = list(verification_findings) + scope_findings
    artifact_path = scheduler._artifact_path(turn, "execution")
    _write_json(
        artifact_path,
        {
            "goal": turn.mission.get("goal", ""),
            "project_root": str(canonical_project_root),
            "workspace_root": str(execution_workspace_root),
            "selected_primary_doc": execution_contract.get("selected_primary_doc") or inputs.get("selected_primary_doc", ""),
            "design_contract": execution_contract,
            "supervisor_brief": supervisor_brief,
            "execution_subagent": execution_result,
            "execution_output": execution_output,
            "verification_expectation": execution_contract.get("verification_expectation", []),
            "verification_specs": specs,
            "verification_commands": [spec.get("command", []) for spec in specs],
            "work_items": execution_contract.get("work_items", []),
            "target_paths": execution_contract.get("target_paths", []),
            "verification_runs": verification_runs,
            "verification_status": "passed" if verification_ok else "failed",
            "verification_findings": verification_findings,
            "session_state": session_state,
            "recorded_at": utc_now(),
        },
    )
    scheduler._queue_completed_execution(
        {
            "slice_key": slice_key,
            "phase_title": phase_title,
            "execution_artifact_path": str(artifact_path),
            "completed_at": utc_now(),
        }
    )
    scheduler._append_recent_event(
        kind="execution_completed",
        summary=f"Execution finished for {phase_title}; waiting for audit.",
        details={"slice_key": slice_key, "execution_artifact_path": str(artifact_path)},
    )
    return {
        "status": "completed",
        "summary": f"Ran {len(verification_runs)} verification command(s); {sum(1 for run in verification_runs if run.get('returncode') == 0)} passed.",
        "artifacts": [str(request_artifact_path), str(result_artifact_path), str(artifact_path)],
        "execution_status": "completed",
        "slice_key": slice_key,
        "phase_title": phase_title,
        "execution_artifact_path": str(artifact_path),
    }


def _execute_audit_turn(
    scheduler: Any,
    turn: RunnerTurn,
    *,
    latest_artifacts: Mapping[str, Any],
    count_sequence_items: Callable[[Any], int],
    verification_acceptance_from_runs: Callable[..., tuple[bool, list[str]]],
    verification_scope_findings: Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], list[str]],
) -> dict[str, Any]:
    queued_execution = scheduler._completed_execution_queue()
    execution_artifact_path = ""
    if queued_execution:
        execution_artifact_path = coerce_str(queued_execution[0].get("execution_artifact_path")).strip()
    execution_artifacts = latest_artifacts.get("execution", [])
    if not execution_artifact_path and execution_artifacts:
        execution_artifact_path = str(execution_artifacts[-1])
    agent_id = scheduler.audit_agent_id or "audit"
    design_contract = {}
    canonical_project_root = project_root_from_doc_root(Path(turn.mission.get("doc_root", scheduler.paths.memory_root)).resolve())
    slice_key = execution_artifact_path or f"audit::{turn.cycle_id}"
    phase_title = "audit"
    if execution_artifact_path:
        execution_plan = scheduler._load_json(execution_artifact_path)
        design_contract = execution_plan.get("design_contract", {})
        if isinstance(design_contract, Mapping):
            canonical_project_root = Path(
                coerce_str(design_contract.get("canonical_project_root") or design_contract.get("project_root"))
            ).resolve()
            selected_phase = design_contract.get("selected_phase", {})
            if isinstance(selected_phase, Mapping):
                phase_title = coerce_str(selected_phase.get("title")).strip() or "audit"
            slice_key = coerce_str(design_contract.get("slice_key")).strip() or slice_key
    existing_run = scheduler._find_running_agent(agent_id, slice_key)
    worktree_entry = scheduler._ensure_agent_worktree(
        agent_id=agent_id,
        slice_key=slice_key,
        canonical_project_root=canonical_project_root,
        phase_title=phase_title,
    )
    audit_workspace_root = Path(coerce_str(worktree_entry.get("path")).strip()).resolve()
    launcher_dir = scheduler.paths.artifacts_dir / "launchers" / "audit"
    if existing_run is None:
        request_artifact_path = scheduler._artifact_path(turn, "audit-request")
        result_artifact_path = scheduler._artifact_path(turn, "audit-result")
        launcher_state_path = launcher_dir / "state.json"
        launcher_run_path = launcher_dir / "runs" / f"{turn.cycle_id}-{turn.sequence:02d}.json"
    else:
        request_artifact_path = Path(coerce_str(existing_run.get("request_path")).strip())
        result_artifact_path = Path(coerce_str(existing_run.get("result_path")).strip())
        launcher_state_path = Path(coerce_str(existing_run.get("launcher_state_path")).strip())
        launcher_run_path = Path(coerce_str(existing_run.get("launcher_run_path")).strip())
        if coerce_str(existing_run.get("worktree_path")).strip():
            audit_workspace_root = Path(coerce_str(existing_run.get("worktree_path")).strip()).resolve()
    if existing_run is None:
        request_payload = {
            "execution_artifact_path": execution_artifact_path,
            "assigned_worktree": str(audit_workspace_root),
            "recorded_at": utc_now(),
        }
        _write_json(request_artifact_path, request_payload)
        launch_result = launch_background_agent(
            agent_id=agent_id,
            workspace_root=audit_workspace_root,
            request_path=request_artifact_path,
            result_path=result_artifact_path,
            launcher_state_path=launcher_state_path,
            launcher_run_path=launcher_run_path,
            started_at=coerce_str(request_payload.get("recorded_at")).strip() or utc_now(),
        )
        scheduler._upsert_running_agent(
            {
                "agent_id": agent_id,
                "slice_key": slice_key,
                "phase_title": phase_title,
                "status": "running" if launch_result.get("ok") else "failed",
                "started_at": launch_result.get("started_at", utc_now()),
                "request_path": str(request_artifact_path),
                "result_path": str(result_artifact_path),
                "launcher_state_path": str(launcher_state_path),
                "launcher_run_path": str(launcher_run_path),
                "project_root": str(canonical_project_root),
                "worktree_path": str(audit_workspace_root),
                "brief": f"audit {phase_title}",
                "pid": launch_result.get("pid"),
            }
        )
        scheduler._append_recent_event(
            kind="audit_launch",
            summary=f"Audit launched in background for {phase_title}.",
            details={"slice_key": slice_key, "worktree_path": str(audit_workspace_root)},
        )
        if launch_result.get("ok") and not result_artifact_path.exists():
            return {
                "status": "running",
                "summary": f"Audit launched in background for {phase_title}.",
                "audit_status": "launched",
                "artifacts": [str(request_artifact_path)],
            }
    launcher_status = load_launcher_status(launcher_state_path)
    if not result_artifact_path.exists():
        launcher_run_status = running_status(launcher_status)
        if launcher_run_status in {"failed", "completed"}:
            scheduler._remove_running_agent(agent_id, slice_key)
            scheduler._release_agent_worktree(
                agent_id=agent_id,
                slice_key=slice_key,
                canonical_project_root=str(canonical_project_root),
            )
            failure_reason = coerce_str(launcher_status.get("stale_reason")).strip() or f"audit background worker exited without writing a result artifact for {phase_title}"
            return {
                "status": "failed",
                "summary": "Audit background worker failed.",
                "audit_status": "failed",
                "artifacts": [str(request_artifact_path)],
                "failure_reason": failure_reason,
            }
        scheduler._upsert_running_agent(
            {
                "agent_id": agent_id,
                "slice_key": slice_key,
                "phase_title": phase_title,
                "status": launcher_run_status,
                "last_polled_at": utc_now(),
                "request_path": str(request_artifact_path),
                "result_path": str(result_artifact_path),
                "launcher_state_path": str(launcher_state_path),
                "launcher_run_path": str(launcher_run_path),
                "project_root": str(canonical_project_root),
                "worktree_path": str(audit_workspace_root),
                "brief": f"audit {phase_title}",
            }
        )
        return {
            "status": "running",
            "summary": f"Audit is still running in background for {phase_title}.",
            "audit_status": "running",
            "artifacts": [str(request_artifact_path)],
        }
    try:
        audit_result = scheduler._load_json(str(result_artifact_path))
    except json.JSONDecodeError:
        return {
            "status": "running",
            "summary": f"Audit is finalizing artifacts for {phase_title}.",
            "audit_status": "running",
            "artifacts": [str(request_artifact_path)],
        }
    scheduler._remove_running_agent(agent_id, slice_key)
    scheduler._release_agent_worktree(
        agent_id=agent_id,
        slice_key=slice_key,
        canonical_project_root=str(canonical_project_root),
    )
    if not coerce_bool(audit_result.get("ok"), False):
        return {
            "status": "failed",
            "summary": "Audit background worker failed.",
            "audit_status": "failed",
            "artifacts": [str(request_artifact_path), str(result_artifact_path)],
        }
    artifact_path = scheduler._artifact_path(turn, "verdict")
    _write_json(
        artifact_path,
        {
            "audit_status": audit_result.get("audit_status", "reopen_execution"),
            "accepted": coerce_bool(audit_result.get("accepted"), False),
            "findings": audit_result.get("findings", []),
            "verification_commands": audit_result.get("verification_commands", []),
            "verification_runs": audit_result.get("verification_runs", []),
            "design_contract": audit_result.get("design_contract", {}),
            "execution_artifact_path": audit_result.get("execution_artifact_path", execution_artifact_path),
            "recorded_at": utc_now(),
        },
    )
    scheduler._queue_completed_agent(
        {
            "agent_id": agent_id,
            "artifact_path": str(artifact_path),
            "slice_key": slice_key,
            "phase_title": phase_title,
            "status": "waiting_supervisor",
            "summary": f"Audit completed for {phase_title}.",
        }
    )
    audit_status = coerce_str(audit_result.get("audit_status")).strip() or "reopen_execution"
    return {
        "status": audit_status,
        "summary": {
            "accepted": "Audit accepted the current round.",
            "reopen_execution": "Audit reopened the round and returned it to execution.",
            "replan_design": "Audit requested a new design contract before execution can continue.",
        }.get(audit_status, "Audit completed."),
        "artifacts": [str(request_artifact_path), str(result_artifact_path), str(artifact_path)],
        "audit_status": audit_status,
        "findings": audit_result.get("findings", []),
        "design_contract": audit_result.get("design_contract", {}),
        "verification_commands": audit_result.get("verification_commands", []),
        "execution_artifact_path": audit_result.get("execution_artifact_path", execution_artifact_path),
    }


def _execute_cleanup_turn(
    scheduler: Any,
    turn: RunnerTurn,
    inputs: Mapping[str, Any],
    *,
    project_root: Path,
    cleanup_runtime_temp_files: Callable[[Path], list[dict[str, str]]],
    project_hygiene_findings: Callable[[Path], list[dict[str, str]]],
) -> dict[str, Any]:
    cleanup_mode = coerce_str(inputs.get("cleanup_mode")).strip() or "round-close"
    runtime_actions = cleanup_runtime_temp_files(scheduler.paths.harness_root)
    repo_hygiene_findings = project_hygiene_findings(project_root) if cleanup_mode == "maintenance" else []
    stale_turn_identity = bool(turn.state.get("cycle_id") or turn.state.get("sequence"))
    stale_pending_gate = False
    pending_gate_id = coerce_str(turn.state.get("pending_gate_id")).strip()
    if cleanup_mode == "recovery" and pending_gate_id:
        try:
            turn.communication_store.get_gate(pending_gate_id)
        except KeyError:
            stale_pending_gate = True
    artifact_path = scheduler._artifact_path(turn, cleanup_mode)
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

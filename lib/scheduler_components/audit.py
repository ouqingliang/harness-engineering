from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from ..runtime_state import coerce_bool, coerce_int, coerce_str, utc_now
from .background_runtime import _process_identity_token, save_launcher_state
from .verification import _verification_acceptance_from_runs, _verification_scope_findings
from .support import _write_json


def _count_sequence_items(value: Any) -> int:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value)
    return 0


def _write_launcher_state(
    *,
    launcher_state_path: Path,
    launcher_run_path: Path,
    request_path: Path,
    result_path: Path,
    started_at: str,
    status: str,
    exit_code: int | None = None,
) -> None:
    payload = {
        "status": status,
        "agent_id": "audit",
        "active_run_id": launcher_run_path.stem if status == "running" else "",
        "last_request_path": str(request_path),
        "last_result_path": str(result_path),
    }
    if status == "running":
        payload["last_cycle_id"] = request_path.parent.name
        payload["started_at"] = started_at
        payload["heartbeat_at"] = utc_now()
        payload["pid"] = os.getpid()
        payload["pid_executable"] = sys.executable
        payload["pid_identity"] = _process_identity_token(os.getpid())
    else:
        payload["completed_at"] = utc_now()
        if exit_code is not None:
            payload["last_exit_code"] = exit_code
    save_launcher_state(
        launcher_state_path=launcher_state_path,
        request_path=request_path,
        result_path=result_path,
        payload=payload,
    )


def _audit_result_from_request(request_payload: Mapping[str, Any]) -> dict[str, Any]:
    execution_artifact_path = coerce_str(request_payload.get("execution_artifact_path")).strip()
    execution_plan = json.loads(Path(execution_artifact_path).read_text(encoding="utf-8")) if execution_artifact_path else {}
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
    return {
        "ok": True,
        "audit_status": audit_status,
        "accepted": audit_status == "accepted",
        "findings": findings,
        "verification_commands": verification_commands,
        "verification_runs": verification_runs,
        "design_contract": design_contract,
        "execution_artifact_path": execution_artifact_path,
    }


def run_saved_audit_request(
    *,
    request_path: Path,
    result_path: Path,
    launcher_state_path: Path,
    launcher_run_path: Path,
) -> dict[str, Any]:
    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    started_at = coerce_str(request_payload.get("recorded_at")).strip() or utc_now()
    launcher_state_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_run_path.parent.mkdir(parents=True, exist_ok=True)
    _write_launcher_state(
        launcher_state_path=launcher_state_path,
        launcher_run_path=launcher_run_path,
        request_path=request_path,
        result_path=result_path,
        started_at=started_at,
        status="running",
    )
    try:
        payload = _audit_result_from_request(request_payload)
        payload["started_at"] = started_at
        payload["completed_at"] = utc_now()
        _write_json(result_path, payload)
        _write_json(launcher_run_path, payload)
        _write_launcher_state(
            launcher_state_path=launcher_state_path,
            launcher_run_path=launcher_run_path,
            request_path=request_path,
            result_path=result_path,
            started_at=started_at,
            status="completed",
            exit_code=0,
        )
        return payload
    except Exception as exc:
        payload = {
            "ok": False,
            "audit_status": "failed",
            "started_at": started_at,
            "completed_at": utc_now(),
            "stderr": f"{exc.__class__.__name__}: {exc}",
        }
        _write_json(result_path, payload)
        _write_json(launcher_run_path, payload)
        _write_launcher_state(
            launcher_state_path=launcher_state_path,
            launcher_run_path=launcher_run_path,
            request_path=request_path,
            result_path=result_path,
            started_at=started_at,
            status="failed",
            exit_code=-1,
        )
        return payload

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..runtime_state import coerce_str, utc_now
from .support import _normalize_text_list, _write_json


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
        "agent_id": "design",
        "active_run_id": launcher_run_path.stem if status == "running" else "",
        "last_request_path": str(request_path),
        "last_result_path": str(result_path),
    }
    if status == "running":
        payload["last_cycle_id"] = request_path.parent.name
        payload["started_at"] = started_at
    else:
        payload["completed_at"] = utc_now()
        if exit_code is not None:
            payload["last_exit_code"] = exit_code
    _write_json(launcher_state_path, payload)


def _design_result_from_request(request_payload: Mapping[str, Any]) -> dict[str, Any]:
    from ..scheduler import _contract_for_supervisor_decision, _design_contract_from_docs

    doc_bundle = request_payload.get("doc_bundle", {})
    if not isinstance(doc_bundle, Mapping):
        doc_bundle = {}
    selected_primary_doc = coerce_str(request_payload.get("selected_primary_doc")).strip()
    doc_root = Path(coerce_str(request_payload.get("doc_root"))).resolve()
    project_root = Path(coerce_str(request_payload.get("project_root"))).resolve()
    planned_slice_queue = request_payload.get("planned_slice_queue", [])
    if not isinstance(planned_slice_queue, list):
        planned_slice_queue = []
    maintenance_findings = _normalize_text_list(request_payload.get("maintenance_findings", []))
    completed_slices = request_payload.get("completed_slices", [])
    pending_supervisor_decision = request_payload.get("pending_supervisor_decision")

    if planned_slice_queue:
        design_contract = dict(planned_slice_queue[0]) if isinstance(planned_slice_queue[0], Mapping) else {}
        remaining_queue = [dict(item) for item in planned_slice_queue[1:] if isinstance(item, Mapping)]
    else:
        design_contract = _design_contract_from_docs(
            doc_root=doc_root,
            project_root=project_root,
            doc_bundle=doc_bundle,
            selected_primary_doc=selected_primary_doc,
            maintenance_findings=maintenance_findings,
            completed_slices=completed_slices,
        )
        remaining_queue = []

    if isinstance(pending_supervisor_decision, Mapping) and pending_supervisor_decision:
        design_contract = _contract_for_supervisor_decision(design_contract, pending_supervisor_decision)
        remaining_queue = []

    next_contract = _design_contract_from_docs(
        doc_root=doc_root,
        project_root=project_root,
        doc_bundle=doc_bundle,
        selected_primary_doc=selected_primary_doc,
        maintenance_findings=maintenance_findings,
        completed_slices=completed_slices,
        reserved_slice_keys=[coerce_str(design_contract.get("slice_key")).strip()],
    )
    queue_payload = list(remaining_queue)
    next_slice_key = coerce_str(next_contract.get("slice_key")).strip()
    if (
        coerce_str(next_contract.get("work_status")).strip() == "ready"
        and next_slice_key
        and next_slice_key != coerce_str(design_contract.get("slice_key")).strip()
        and not any(
            coerce_str(item.get("slice_key")).strip() == next_slice_key
            for item in queue_payload
            if isinstance(item, Mapping)
        )
    ):
        queue_payload.append(next_contract)
    return {
        "ok": True,
        "design_status": "completed" if coerce_str(design_contract.get("work_status")).strip() == "completed" else "ready",
        "selected_primary_doc": coerce_str(design_contract.get("selected_primary_doc")).strip() or selected_primary_doc,
        "design_contract": design_contract,
        "planned_slice_queue": queue_payload,
        "prefetch_completed": coerce_str(next_contract.get("work_status")).strip() == "completed",
        "next_contract": next_contract,
    }


def run_saved_design_request(
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
        payload = _design_result_from_request(request_payload)
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
            "design_status": "failed",
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

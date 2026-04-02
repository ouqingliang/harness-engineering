from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from ..runtime_state import (
    HARNESS_DIR_NAME,
    append_event_row,
    brief_record_path,
    coerce_int,
    coerce_str,
    ensure_runtime_layout,
    event_log_path,
    gate_record_path,
    inbox_message_path,
    read_json_file,
    session_metadata_path,
    utc_now,
    write_brief_record,
    write_gate_record,
    write_inbox_message,
    write_json_file,
    write_session_metadata,
)
from .support import HARNESS_ROOT, _git_status_snapshot, _write_json

PID_MISMATCH_FAILURE_GRACE_SECONDS = 120
LAUNCHER_HEARTBEAT_STALE_SECONDS = 20


def _read_launcher_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = read_json_file(path)
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _same_launcher_request(
    payload: Mapping[str, Any],
    *,
    request_path: Path,
    result_path: Path,
) -> bool:
    return (
        coerce_str(payload.get("last_request_path")).strip() == str(request_path)
        and coerce_str(payload.get("last_result_path")).strip() == str(result_path)
    )


def _launcher_failure_payload(
    *,
    command: list[str],
    started_at: str,
    workspace_root: Path,
    error_message: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "exit_code": -1,
        "started_at": started_at,
        "completed_at": utc_now(),
        "stdout": "",
        "stderr": error_message,
        "command": command,
        "pre_git_status": _git_status_snapshot(workspace_root),
        "post_git_status": _git_status_snapshot(workspace_root),
    }


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = read_json_file(path)
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _launcher_memory_root(request_path: Path, result_path: Path, launcher_state_path: Path) -> Path:
    for candidate in (request_path, result_path, launcher_state_path):
        resolved = candidate.resolve()
        for parent in (resolved.parent, *resolved.parents):
            if parent.name == HARNESS_DIR_NAME:
                return parent.parent
    return request_path.parents[2] if len(request_path.parents) >= 3 else request_path.parent


def _launcher_record_id(
    *,
    agent_id: str,
    request_path: Path,
    payload: Mapping[str, Any],
    existing_state: Mapping[str, Any],
) -> str:
    active_run_id = coerce_str(payload.get("active_run_id")).strip() or coerce_str(existing_state.get("active_run_id")).strip()
    suffix = active_run_id or request_path.stem
    return f"launcher-{coerce_str(agent_id).strip() or 'unknown'}-{suffix}"


def _persist_launcher_substrate_records(
    *,
    launcher_state_path: Path,
    request_path: Path,
    result_path: Path,
    payload: Mapping[str, Any],
    existing_state: Mapping[str, Any],
) -> None:
    agent_id = coerce_str(payload.get("agent_id") or existing_state.get("agent_id")).strip()
    if not agent_id:
        return
    memory_root = _launcher_memory_root(request_path, result_path, launcher_state_path)
    paths = ensure_runtime_layout(memory_root)
    record_id = _launcher_record_id(
        agent_id=agent_id,
        request_path=request_path,
        payload=payload,
        existing_state=existing_state,
    )
    request_payload = _read_optional_json(request_path)
    result_payload = _read_optional_json(result_path)
    status = coerce_str(payload.get("status") or existing_state.get("status")).strip().lower()
    summary = (
        coerce_str(result_payload.get("summary")).strip()
        or coerce_str(payload.get("summary")).strip()
        or f"{agent_id} launcher {status or 'updated'}"
    )
    session_payload = {
        "session_id": record_id,
        "agent_id": agent_id,
        "status": status,
        "request_path": str(request_path),
        "result_path": str(result_path),
        "launcher_state_path": str(launcher_state_path),
        "active_run_id": coerce_str(payload.get("active_run_id")).strip() or coerce_str(existing_state.get("active_run_id")).strip(),
        "pid": coerce_int(payload.get("pid") if payload.get("pid") is not None else existing_state.get("pid"), 0),
        "heartbeat_at": coerce_str(payload.get("heartbeat_at") or existing_state.get("heartbeat_at")).strip(),
        "started_at": coerce_str(payload.get("started_at") or existing_state.get("started_at")).strip(),
        "completed_at": coerce_str(payload.get("completed_at") or existing_state.get("completed_at")).strip(),
    }
    write_session_metadata(session_metadata_path(memory_root, record_id), session_payload)
    inbox_payload = {
        "message_id": f"{record_id}-request",
        "agent_id": agent_id,
        "request_path": str(request_path),
        "result_path": str(result_path),
        "assigned_worktree": coerce_str(request_payload.get("assigned_worktree")).strip(),
        "selected_primary_doc": coerce_str(request_payload.get("selected_primary_doc")).strip(),
        "recorded_at": coerce_str(payload.get("heartbeat_at") or payload.get("completed_at") or utc_now()).strip() or utc_now(),
    }
    write_inbox_message(inbox_message_path(memory_root, f"{record_id}-request"), inbox_payload)
    write_brief_record(
        brief_record_path(memory_root, record_id),
        {
            "brief_id": record_id,
            "agent_id": agent_id,
            "summary": summary,
            "status": status,
            "result_path": str(result_path),
        },
    )
    gate_id = coerce_str(result_payload.get("gate_id") or payload.get("gate_id")).strip()
    if gate_id:
        write_gate_record(
            gate_record_path(memory_root, gate_id),
            {
                "gate_id": gate_id,
                "agent_id": agent_id,
                "status": status or "open",
                "title": summary,
                "result_path": str(result_path),
            },
        )
    artifact_record_path = paths.artifacts_dir / "launchers" / f"{record_id}.json"
    write_json_file(
        artifact_record_path,
        {
            "record_id": record_id,
            "agent_id": agent_id,
            "request_path": str(request_path),
            "result_path": str(result_path),
            "request": request_payload,
            "result": result_payload,
            "launcher_state": dict(payload),
        },
    )
    append_event_row(
        event_log_path(memory_root, record_id),
        {
            "event": f"launcher.{status or 'updated'}",
            "agent_id": agent_id,
            "session": record_id,
            "status": status,
            "summary": summary,
            "request_path": str(request_path),
            "result_path": str(result_path),
        },
    )


def save_launcher_state(
    *,
    launcher_state_path: Path,
    request_path: Path,
    result_path: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = dict(payload)
    status = coerce_str(normalized.get("status")).strip().lower()
    existing_state = _read_launcher_state(launcher_state_path)
    if status == "running" and _same_launcher_request(existing_state, request_path=request_path, result_path=result_path):
        for key in (
            "pid",
            "pid_executable",
            "pid_identity",
            "heartbeat_at",
            "agent_id",
            "active_run_id",
            "last_cycle_id",
            "started_at",
        ):
            existing_value = existing_state.get(key)
            if existing_value not in (None, "") and key not in normalized:
                normalized[key] = existing_value
    _write_json(launcher_state_path, normalized)
    _persist_launcher_substrate_records(
        launcher_state_path=launcher_state_path,
        request_path=request_path,
        result_path=result_path,
        payload=normalized,
        existing_state=existing_state,
    )
    return normalized


def launch_background_agent(
    *,
    agent_id: str,
    workspace_root: Path,
    request_path: Path,
    result_path: Path,
    launcher_state_path: Path,
    launcher_run_path: Path,
    started_at: str,
) -> dict[str, Any]:
    launcher_state_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_run_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_script = HARNESS_ROOT / "runners" / "codex_agent_launcher.py"
    command = [
        sys.executable,
        str(launcher_script),
        "--agent-id",
        agent_id,
        "--request-path",
        str(request_path),
        "--result-path",
        str(result_path),
        "--launcher-state-path",
        str(launcher_state_path),
        "--launcher-run-path",
        str(launcher_run_path),
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=str(HARNESS_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        failure_payload = _launcher_failure_payload(
            command=command,
            started_at=started_at,
            workspace_root=workspace_root,
            error_message=f"{exc.__class__.__name__}: {exc}",
        )
        _write_json(result_path, failure_payload)
        _write_json(launcher_run_path, failure_payload)
        _write_json(
            launcher_state_path,
            {
                "status": "failed",
                "agent_id": agent_id,
                "active_run_id": "",
                "last_request_path": str(request_path),
                "last_result_path": str(result_path),
                "last_exit_code": -1,
                "completed_at": failure_payload["completed_at"],
            },
        )
        return {
            "ok": False,
            "pid": None,
            "command": command,
            "started_at": started_at,
            "launch_error": failure_payload["stderr"],
        }
    launch_state = {
        "status": "running",
        "agent_id": agent_id,
        "active_run_id": launcher_run_path.stem,
        "last_request_path": str(request_path),
        "last_result_path": str(result_path),
        "last_cycle_id": request_path.parent.name,
        "started_at": started_at,
        "heartbeat_at": utc_now(),
        "pid": process.pid,
        "pid_executable": sys.executable,
        "pid_identity": _process_identity_token(process.pid),
    }
    existing_state = _read_launcher_state(launcher_state_path)
    if _same_launcher_request(existing_state, request_path=request_path, result_path=result_path):
        existing_status = coerce_str(existing_state.get("status")).strip().lower()
        if existing_status in {"completed", "failed"}:
            launch_state = dict(existing_state)
        else:
            launch_state = dict(existing_state) | launch_state
    save_launcher_state(
        launcher_state_path=launcher_state_path,
        request_path=request_path,
        result_path=result_path,
        payload=launch_state,
    )
    return {
        "ok": True,
        "pid": process.pid,
        "command": command,
        "started_at": started_at,
    }


def _parse_utc(text: Any) -> datetime | None:
    normalized = coerce_str(text).strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _orphaned_running_reason(path: Path, normalized: Mapping[str, Any]) -> str:
    if coerce_str(normalized.get("status")).strip().lower() != "running":
        return ""
    pid = coerce_int(normalized.get("pid"), 0)
    if pid > 0:
        return ""
    started_at = _parse_utc(normalized.get("started_at"))
    if started_at is None:
        return ""
    if (datetime.now(timezone.utc) - started_at).total_seconds() < 15:
        return ""
    missing: list[str] = []
    active_run_id = coerce_str(normalized.get("active_run_id")).strip()
    if active_run_id:
        run_path = path.parent / "runs" / f"{active_run_id}.json"
        if not run_path.exists():
            missing.append("launcher run record")
    result_path = Path(coerce_str(normalized.get("last_result_path")).strip()) if coerce_str(normalized.get("last_result_path")).strip() else None
    if result_path is None or not result_path.exists():
        missing.append("result artifact")
    if not missing:
        return ""
    missing_text = ", ".join(missing)
    return f"background worker no longer has a live pid and is missing {missing_text}"


def _running_state_age_seconds(normalized: Mapping[str, Any]) -> float | None:
    started_at = _parse_utc(normalized.get("started_at"))
    if started_at is None:
        return None
    return (datetime.now(timezone.utc) - started_at).total_seconds()


def _elapsed_since(timestamp: Any) -> float | None:
    parsed = _parse_utc(timestamp)
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _has_recent_heartbeat(normalized: Mapping[str, Any]) -> bool:
    heartbeat_age = _elapsed_since(normalized.get("heartbeat_at"))
    return heartbeat_age is not None and heartbeat_age < LAUNCHER_HEARTBEAT_STALE_SECONDS


def load_launcher_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = read_json_file(path)
    except Exception:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    normalized = dict(payload)
    status = coerce_str(normalized.get("status")).strip().lower()
    pid = coerce_int(normalized.get("pid"), 0)
    has_recent_heartbeat = _has_recent_heartbeat(normalized)
    result_path_raw = coerce_str(normalized.get("last_result_path")).strip()
    result_exists = bool(result_path_raw) and Path(result_path_raw).exists()
    if status == "running" and result_exists:
        if coerce_str(normalized.get("pid_mismatch_detected_at")).strip():
            normalized.pop("pid_mismatch_detected_at", None)
            _write_json(path, normalized)
        return normalized
    if status == "running" and pid > 0 and not _pid_is_alive(pid) and not has_recent_heartbeat:
        normalized["status"] = "failed"
        normalized["active_run_id"] = ""
        normalized["completed_at"] = utc_now()
        normalized["last_exit_code"] = coerce_int(normalized.get("last_exit_code"), -1)
        normalized["stale_reason"] = f"background worker pid {pid} is no longer running"
        _write_json(path, normalized)
    elif status == "running" and pid > 0 and not _pid_matches_launcher(pid, normalized) and not has_recent_heartbeat:
        active_run_id = coerce_str(normalized.get("active_run_id")).strip()
        run_exists = bool(active_run_id) and (path.parent / "runs" / f"{active_run_id}.json").exists()
        mismatch_detected_at = coerce_str(normalized.get("pid_mismatch_detected_at")).strip()
        if not mismatch_detected_at:
            normalized["pid_mismatch_detected_at"] = utc_now()
            _write_json(path, normalized)
        elif not result_exists and not run_exists and (_elapsed_since(mismatch_detected_at) or 0) >= PID_MISMATCH_FAILURE_GRACE_SECONDS:
            normalized["status"] = "failed"
            normalized["active_run_id"] = ""
            normalized["completed_at"] = utc_now()
            normalized["last_exit_code"] = coerce_int(normalized.get("last_exit_code"), -1)
            normalized["stale_reason"] = f"background worker pid {pid} now belongs to a different process"
            _write_json(path, normalized)
    elif status == "running" and coerce_str(normalized.get("pid_mismatch_detected_at")).strip():
        normalized.pop("pid_mismatch_detected_at", None)
        _write_json(path, normalized)
    elif status == "running" and not has_recent_heartbeat:
        stale_reason = _orphaned_running_reason(path, normalized)
        if stale_reason:
            normalized["status"] = "failed"
            normalized["active_run_id"] = ""
            normalized["completed_at"] = utc_now()
            normalized["last_exit_code"] = coerce_int(normalized.get("last_exit_code"), -1)
            normalized["stale_reason"] = stale_reason
            _write_json(path, normalized)
    return normalized


def running_status(launcher_status: Mapping[str, Any]) -> str:
    return coerce_str(launcher_status.get("status")).strip() or "running"


def _pid_is_alive(pid: int) -> bool:
    normalized_pid = int(pid)
    if normalized_pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, normalized_pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True
    try:
        os.kill(normalized_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _process_executable_path(pid: int) -> str:
    normalized_pid = int(pid)
    if normalized_pid <= 0:
        return ""
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            buffer_size = 32768
            buffer = ctypes.create_unicode_buffer(buffer_size)
            size = ctypes.c_ulong(buffer_size)
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, normalized_pid)
            if not handle:
                return ""
            try:
                if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return buffer.value
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return ""
        return ""
    proc_exe = Path(f"/proc/{normalized_pid}/exe")
    if proc_exe.exists():
        try:
            return str(proc_exe.resolve())
        except OSError:
            return ""
    return ""


def _process_identity_token(pid: int) -> str:
    normalized_pid = int(pid)
    if normalized_pid <= 0:
        return ""
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, normalized_pid)
            if not handle:
                return ""
            try:
                creation = FILETIME()
                exit_time = FILETIME()
                kernel = FILETIME()
                user = FILETIME()
                if not ctypes.windll.kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return ""
                value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                return str(value)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return ""
    proc_stat = Path(f"/proc/{normalized_pid}/stat")
    if proc_stat.exists():
        try:
            fields = proc_stat.read_text(encoding="utf-8").split()
        except OSError:
            return ""
        if len(fields) >= 22:
            return fields[21]
    return ""


def _pid_matches_launcher(pid: int, payload: Mapping[str, Any]) -> bool:
    expected_identity = coerce_str(payload.get("pid_identity")).strip()
    if expected_identity:
        actual_identity = _process_identity_token(pid)
        if actual_identity:
            return actual_identity == expected_identity
    expected_executable = coerce_str(payload.get("pid_executable")).strip()
    if not expected_executable:
        return True
    actual_executable = _process_executable_path(pid)
    if not actual_executable:
        return True
    expected_name = Path(expected_executable).name.lower()
    actual_name = Path(actual_executable).name.lower()
    if expected_name and actual_name and expected_name != actual_name:
        return False
    return True

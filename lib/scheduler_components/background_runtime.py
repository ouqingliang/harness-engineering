from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from ..runtime_state import coerce_int, coerce_str, utc_now
from .support import HARNESS_ROOT, _git_status_snapshot, _write_json


def _read_launcher_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
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
        "pid": process.pid,
    }
    existing_state = _read_launcher_state(launcher_state_path)
    if _same_launcher_request(existing_state, request_path=request_path, result_path=result_path):
        existing_status = coerce_str(existing_state.get("status")).strip().lower()
        if existing_status in {"completed", "failed"}:
            launch_state = dict(existing_state)
        else:
            launch_state = dict(existing_state) | launch_state
    _write_json(launcher_state_path, launch_state)
    return {
        "ok": True,
        "pid": process.pid,
        "command": command,
        "started_at": started_at,
    }


def load_launcher_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    normalized = dict(payload)
    status = coerce_str(normalized.get("status")).strip().lower()
    pid = coerce_int(normalized.get("pid"), 0)
    if status == "running" and pid > 0 and not _pid_is_alive(pid):
        normalized["status"] = "failed"
        normalized["active_run_id"] = ""
        normalized["completed_at"] = utc_now()
        normalized["last_exit_code"] = coerce_int(normalized.get("last_exit_code"), -1)
        normalized["stale_reason"] = f"background worker pid {pid} is no longer running"
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

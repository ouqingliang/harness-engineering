from __future__ import annotations

import argparse
import os
from pathlib import Path
import threading
import time
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.scheduler_components.audit import run_saved_audit_request
from lib.scheduler_components.background_runtime import _process_identity_token, save_launcher_state
from lib.scheduler_components.design import run_saved_design_request
from lib.scheduler_components.execution import _run_execution_subagent_from_saved_request
from lib.runtime_state import utc_now


def _heartbeat_loop(
    *,
    agent_id: str,
    request_path: Path,
    result_path: Path,
    launcher_state_path: Path,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(5):
        save_launcher_state(
            launcher_state_path=launcher_state_path,
            request_path=request_path,
            result_path=result_path,
            payload={
                "status": "running",
                "agent_id": agent_id,
                "last_request_path": str(request_path),
                "last_result_path": str(result_path),
                "heartbeat_at": utc_now(),
            },
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a saved background agent request for the harness.")
    parser.add_argument("--agent-id", required=True, choices=("design", "execution", "audit"))
    parser.add_argument("--request-path", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--launcher-state-path", required=True)
    parser.add_argument("--launcher-run-path", required=True)
    args = parser.parse_args(argv)

    common_kwargs = {
        "request_path": Path(args.request_path).resolve(),
        "result_path": Path(args.result_path).resolve(),
        "launcher_state_path": Path(args.launcher_state_path).resolve(),
        "launcher_run_path": Path(args.launcher_run_path).resolve(),
    }
    save_launcher_state(
        launcher_state_path=common_kwargs["launcher_state_path"],
        request_path=common_kwargs["request_path"],
        result_path=common_kwargs["result_path"],
        payload={
            "status": "running",
            "agent_id": args.agent_id,
            "last_request_path": str(common_kwargs["request_path"]),
            "last_result_path": str(common_kwargs["result_path"]),
            "heartbeat_at": utc_now(),
            "pid": os.getpid(),
            "pid_executable": sys.executable,
            "pid_identity": _process_identity_token(os.getpid()),
        },
    )
    stop_event = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        kwargs={
            "agent_id": args.agent_id,
            "request_path": common_kwargs["request_path"],
            "result_path": common_kwargs["result_path"],
            "launcher_state_path": common_kwargs["launcher_state_path"],
            "stop_event": stop_event,
        },
        daemon=True,
    )
    heartbeat.start()
    try:
        if args.agent_id == "design":
            run_saved_design_request(**common_kwargs)
            return 0
        if args.agent_id == "audit":
            run_saved_audit_request(**common_kwargs)
            return 0
        _run_execution_subagent_from_saved_request(**common_kwargs)
        return 0
    finally:
        stop_event.set()
        heartbeat.join(timeout=1)


if __name__ == "__main__":
    raise SystemExit(main())

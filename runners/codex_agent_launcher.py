from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.scheduler_components.audit import run_saved_audit_request
from lib.scheduler_components.design import run_saved_design_request
from lib.scheduler_components.execution import _run_execution_subagent_from_saved_request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a saved background agent request for the harness.")
    parser.add_argument("--agent-id", required=True, choices=("design", "execution", "audit"))
    parser.add_argument("--request-path", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--launcher-state-path", required=True)
    parser.add_argument("--launcher-run-path", required=True)
    args = parser.parse_args(argv)

    common_kwargs = {
        "request_path": Path(args.request_path),
        "result_path": Path(args.result_path),
        "launcher_state_path": Path(args.launcher_state_path),
        "launcher_run_path": Path(args.launcher_run_path),
    }
    if args.agent_id == "design":
        run_saved_design_request(**common_kwargs)
        return 0
    if args.agent_id == "audit":
        run_saved_audit_request(**common_kwargs)
        return 0
    _run_execution_subagent_from_saved_request(**common_kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

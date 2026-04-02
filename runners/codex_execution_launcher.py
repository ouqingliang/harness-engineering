from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.scheduler_components.execution import _run_execution_subagent_from_saved_request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a saved execution request for the harness in the background.")
    parser.add_argument("--request-path", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--launcher-state-path", required=True)
    parser.add_argument("--launcher-run-path", required=True)
    args = parser.parse_args(argv)

    _run_execution_subagent_from_saved_request(
        request_path=Path(args.request_path).resolve(),
        result_path=Path(args.result_path).resolve(),
        launcher_state_path=Path(args.launcher_state_path).resolve(),
        launcher_run_path=Path(args.launcher_run_path).resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

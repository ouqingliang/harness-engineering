from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_docs(doc_root: Path, *, with_gate: bool) -> None:
    doc_root.mkdir(parents=True, exist_ok=True)
    body = "# Soak Demo\n\nVerify the long-running harness loop.\n"
    if with_gate:
        body += "\n[decision-gate] Human confirmation is required before continuing.\n"
    (doc_root / "README.md").write_text(body, encoding="utf-8")


def _runtime_files(memory_root: Path) -> tuple[Path, Path]:
    harness_root = memory_root / ".harness"
    return harness_root / "mission.json", harness_root / "state.json"


def _wait_for_status(
    process: subprocess.Popen[str],
    *,
    memory_root: Path,
    expected: str,
    timeout_seconds: float,
) -> dict[str, object]:
    mission_file, state_file = _runtime_files(memory_root)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=5)
            raise RuntimeError(
                "run exited unexpectedly:\n"
                f"returncode={process.returncode}\nstdout={stdout}\nstderr={stderr}"
            )
        if mission_file.exists() and state_file.exists():
            mission_payload = json.loads(mission_file.read_text(encoding="utf-8"))
            state_payload = json.loads(state_file.read_text(encoding="utf-8"))
            if mission_payload.get("status") == expected:
                return {"mission": mission_payload, "state": state_payload}
        time.sleep(0.2)
    raise RuntimeError(f"run did not reach {expected!r} within {timeout_seconds} seconds")


def _start_run(python_executable: str, *, doc_root: Path, memory_root: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            python_executable,
            "main.py",
            "run",
            "--doc-root",
            str(doc_root),
            "--memory-root",
            str(memory_root),
            "--port",
            "0",
            "--no-browser",
            "--reset",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _stop_run(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        process.kill()
    stdout, stderr = process.communicate(timeout=10)
    return stdout, stderr


def _run_completion_case(python_executable: str, root: Path) -> dict[str, object]:
    doc_root = root / "docs-complete"
    memory_root = root / "memory-complete"
    _write_docs(doc_root, with_gate=False)
    process = _start_run(python_executable, doc_root=doc_root, memory_root=memory_root)
    try:
        runtime = _wait_for_status(process, memory_root=memory_root, expected="completed", timeout_seconds=30)
        stdout, stderr = _stop_run(process)
        return {
            "case": "completion",
            "status": runtime["mission"]["status"],
            "round": runtime["state"].get("current_round"),
            "stdout_tail": stdout.splitlines()[-2:],
            "stderr_tail": stderr.splitlines()[-2:],
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def _run_gate_resume_case(python_executable: str, root: Path) -> dict[str, object]:
    doc_root = root / "docs-gate"
    memory_root = root / "memory-gate"
    _write_docs(doc_root, with_gate=True)
    process = _start_run(python_executable, doc_root=doc_root, memory_root=memory_root)
    try:
        paused = _wait_for_status(process, memory_root=memory_root, expected="waiting_human", timeout_seconds=30)
        gate_id = str(paused["state"].get("pending_gate_id") or "")
        if not gate_id:
            raise RuntimeError("waiting_human state did not expose a gate id")

        reply = subprocess.run(
            [
                python_executable,
                "main.py",
                "reply",
                "--memory-root",
                str(memory_root),
                "--gate-id",
                gate_id,
                "--message",
                "Continue the mainline implementation.",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if reply.returncode != 0:
            raise RuntimeError(f"gate reply failed:\nstdout={reply.stdout}\nstderr={reply.stderr}")

        resumed = _wait_for_status(process, memory_root=memory_root, expected="completed", timeout_seconds=30)
        stdout, stderr = _stop_run(process)
        return {
            "case": "gate_resume",
            "gate_id": gate_id,
            "status": resumed["mission"]["status"],
            "round": resumed["state"].get("current_round"),
            "stdout_tail": stdout.splitlines()[-4:],
            "stderr_tail": stderr.splitlines()[-2:],
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a lightweight long-running harness soak loop.")
    parser.add_argument("--iterations", type=int, default=3, help="number of soak iterations")
    parser.add_argument("--python", default=sys.executable, help="python executable used to invoke the CLI")
    parser.add_argument("--keep-temp", action="store_true", help="keep the temporary workspace for inspection")
    args = parser.parse_args(argv)

    work_context = tempfile.TemporaryDirectory(prefix="harness-soak-")
    try:
        root = Path(work_context.name)
        started_at = time.time()
        iterations: list[dict[str, object]] = []
        for index in range(args.iterations):
            iteration_root = root / f"iteration-{index + 1:02d}"
            iteration_root.mkdir(parents=True, exist_ok=True)
            iterations.append(
                {
                    "iteration": index + 1,
                    "completion": _run_completion_case(args.python, iteration_root),
                    "gate_resume": _run_gate_resume_case(args.python, iteration_root),
                }
            )
        summary = {
            "ok": True,
            "iterations": args.iterations,
            "duration_seconds": round(time.time() - started_at, 3),
            "workspace": str(root),
            "results": iterations,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.keep_temp:
            work_context.cleanup = lambda: None  # type: ignore[method-assign]
        return 0
    finally:
        work_context.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

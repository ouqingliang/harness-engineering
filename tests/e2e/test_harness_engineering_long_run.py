from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from lib.runtime_state import ensure_runtime_root, load_mission, load_state


def _fake_codex_env(temp_path: Path) -> dict[str, str]:
    bin_dir = temp_path / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script_path = temp_path / "fake_codex.py"
    script_path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import json",
                "import sys",
                "from pathlib import Path",
                "",
                "args = sys.argv[1:]",
                "output_path = None",
                "for index, value in enumerate(args):",
                "    if value == '-o' and index + 1 < len(args):",
                "        output_path = Path(args[index + 1])",
                "payload = {",
                "    'status': 'implemented',",
                "    'summary': 'Fake codex completed the slice.',",
                "    'changed_paths': ['README.md'],",
                "    'verification_notes': [],",
                "    'needs_human': False,",
                "    'human_question': '',",
                "    'why_not_auto_answered': '',",
                "    'required_reply_shape': '',",
                "    'decision_tags': [],",
                "    'options': [],",
                "    'notes': ['Execution used subagents for modification work.'],",
                "}",
                "if output_path is not None:",
                "    output_path.parent.mkdir(parents=True, exist_ok=True)",
                "    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')",
                "print(json.dumps(payload, ensure_ascii=False))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    launcher_path = bin_dir / "codex.cmd"
    launcher_path.write_text(f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n', encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class HarnessEngineeringCliTests(unittest.TestCase):
    def test_cli_run_reaches_completed_but_stays_alive(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            doc_root = temp_path / "docs"
            memory_root = temp_path / "memory"
            env = _fake_codex_env(temp_path)
            doc_root.mkdir()
            (doc_root / "README.md").write_text("# CLI Demo\n\nOverall planning.\n", encoding="utf-8")

            process = subprocess.Popen(
                [
                    sys.executable,
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
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
            try:
                deadline = time.time() + 30
                while time.time() < deadline:
                    if process.poll() is not None:
                        stdout, stderr = process.communicate(timeout=5)
                        self.fail(f"run exited early:\nstdout={stdout}\nstderr={stderr}")
                    paths = ensure_runtime_root(memory_root)
                    if paths.mission_file.exists():
                        mission = load_mission(memory_root)
                        if mission.status == "completed":
                            break
                    time.sleep(0.2)
                else:
                    self.fail("run did not reach completed status in time")

                self.assertIsNone(process.poll(), "process should remain alive after completed status")
                process.kill()
                stdout, stderr = process.communicate(timeout=10)
                self.assertIn('"status": "completed"', stdout)
                self.assertIn("human reply page:", stdout)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_cli_run_waits_for_human_page_and_resumes_after_reply(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            doc_root = temp_path / "docs"
            memory_root = temp_path / "memory"
            env = _fake_codex_env(temp_path)
            doc_root.mkdir()
            (doc_root / "README.md").write_text(
                "# CLI Demo\n\n[decision-gate] Human confirmation is required before continuing.\n",
                encoding="utf-8",
            )

            process = subprocess.Popen(
                [
                    sys.executable,
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
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
            try:
                gate_id = ""
                deadline = time.time() + 20
                while time.time() < deadline:
                    if process.poll() is not None:
                        stdout, stderr = process.communicate(timeout=5)
                        self.fail(f"run exited early before waiting_human:\nstdout={stdout}\nstderr={stderr}")
                    paths = ensure_runtime_root(memory_root)
                    if paths.state_file.exists() and paths.mission_file.exists():
                        mission = load_mission(memory_root)
                        state = load_state(memory_root)
                        gate_id = str(state.extra.get("pending_gate_id") or "")
                        if mission.status == "waiting_human" and gate_id:
                            break
                    time.sleep(0.2)
                self.assertTrue(gate_id, "run did not reach waiting_human in time")

                reply = subprocess.run(
                    [
                        sys.executable,
                        "main.py",
                        "reply",
                        "--memory-root",
                        str(memory_root),
                        "--gate-id",
                        gate_id,
                        "--message",
                        "Continue the mainline implementation.",
                    ],
                    cwd=repo_root,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                self.assertEqual(reply.returncode, 0, reply.stderr)

                deadline = time.time() + 30
                while time.time() < deadline:
                    if process.poll() is not None:
                        stdout, stderr = process.communicate(timeout=5)
                        self.fail(f"run exited early before completion:\nstdout={stdout}\nstderr={stderr}")
                    mission = load_mission(memory_root)
                    if mission.status == "completed":
                        break
                    time.sleep(0.2)
                else:
                    self.fail("run did not resume to completed status in time")

                self.assertIsNone(process.poll(), "process should remain alive after resume completion")
                process.kill()
                stdout, stderr = process.communicate(timeout=10)
                self.assertIn('"status": "waiting_human"', stdout)
                self.assertIn('"status": "completed"', stdout)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()

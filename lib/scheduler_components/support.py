from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..runtime_state import coerce_str


HARNESS_ROOT = Path(__file__).resolve().parents[2]
CODEX_EXECUTABLE_NAMES = ("codex.cmd", "codex.exe", "codex")
DEFAULT_EXECUTION_OUTPUT = {
    "status": "unknown",
    "summary": "",
    "changed_paths": [],
    "verification_notes": [],
    "needs_human": False,
    "human_question": "",
    "why_not_auto_answered": "",
    "required_reply_shape": "",
    "decision_tags": [],
    "options": [],
    "notes": [],
}


def _write_json(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _command_display(command: Sequence[str]) -> str:
    return " ".join(command)


def _normalize_text_list(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [coerce_str(item).strip() for item in value if coerce_str(item).strip()]
    text = coerce_str(value).strip()
    return [text] if text else []


def _find_codex_executable() -> str:
    for name in CODEX_EXECUTABLE_NAMES:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return ""


def _git_status_snapshot(project_root: Path) -> dict[str, Any]:
    command = ["git", "status", "--short"]
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "ok": False,
            "command": command,
            "cwd": str(project_root),
            "returncode": -1,
            "stdout": "",
            "stderr": f"{exc.__class__.__name__}: {exc}",
            "entries": [],
        }
    entries = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "cwd": str(project_root),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "entries": entries,
    }

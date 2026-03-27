from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any, Mapping, Sequence

from ..project_context import path_within, same_path
from ..runtime_state import coerce_str, utc_now
from .support import HARNESS_ROOT, _command_display


def _harness_default_verification_command() -> list[str]:
    return [sys.executable, "-m", "unittest", "tests.test_runtime_files", "-v"]


def _normalize_verification_command(candidate: Any) -> list[str]:
    if isinstance(candidate, str):
        return shlex.split(candidate, posix=sys.platform != "win32")
    if isinstance(candidate, Sequence) and not isinstance(candidate, (bytes, bytearray, str)):
        return [str(item) for item in candidate if str(item)]
    if isinstance(candidate, Mapping):
        raw_command = candidate.get("command", candidate.get("argv", []))
        return _normalize_verification_command(raw_command)
    return []


def _normalize_env_mapping(candidate: Any) -> dict[str, str]:
    if not isinstance(candidate, Mapping):
        return {}
    return {
        coerce_str(key).strip(): coerce_str(value)
        for key, value in candidate.items()
        if coerce_str(key).strip()
    }


def _resolve_cwd(candidate: Any, *, project_root: Path, default_cwd: Path) -> Path:
    raw = coerce_str(candidate).strip()
    if not raw:
        return default_cwd
    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _generic_external_verification_spec(*, project_root: Path, doc_root: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; import sys; "
            "project = Path(sys.argv[1]); doc = Path(sys.argv[2]); "
            "assert project.exists(), project; assert doc.exists(), doc; "
            "print(project); print(doc)"
        ),
        str(project_root),
        str(doc_root),
    ]
    return {
        "command": command,
        "command_display": _command_display(command),
        "cwd": str(project_root),
        "env": {},
        "source": "generic_external_default",
    }


def _default_verification_specs(*, project_root: Path, doc_root: Path) -> list[dict[str, Any]]:
    if same_path(project_root, HARNESS_ROOT):
        command = _harness_default_verification_command()
        return [
            {
                "command": command,
                "command_display": _command_display(command),
                "cwd": str(HARNESS_ROOT),
                "env": {},
                "source": "harness_default",
            }
        ]
    return [_generic_external_verification_spec(project_root=project_root, doc_root=doc_root)]


def _parse_shell_verification_spec(raw_command: str, *, project_root: Path, default_cwd: Path) -> dict[str, Any] | None:
    command_text = raw_command.strip()
    if not command_text:
        return None
    if command_text.startswith("(") and command_text.endswith(")"):
        command_text = command_text[1:-1].strip()
    cwd = default_cwd
    env: dict[str, str] = {}
    command: list[str] = []
    for segment in [item.strip() for item in command_text.split("&&") if item.strip()]:
        tokens = shlex.split(segment, posix=True)
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) >= 2:
            cwd = _resolve_cwd(tokens[1], project_root=project_root, default_cwd=default_cwd)
            continue
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", tokens[0]):
            key, value = tokens.pop(0).split("=", 1)
            env[key] = value
        if tokens:
            command = [str(item) for item in tokens]
    if not command:
        return None
    return {
        "command": command,
        "command_display": command_text,
        "cwd": str(cwd),
        "env": env,
        "source": "shell_text",
    }


def _normalize_verification_spec(
    candidate: Any,
    *,
    project_root: Path,
    default_cwd: Path,
) -> dict[str, Any] | None:
    if isinstance(candidate, Mapping):
        command = _normalize_verification_command(candidate)
        if not command and candidate.get("raw"):
            return _parse_shell_verification_spec(
                coerce_str(candidate.get("raw")),
                project_root=project_root,
                default_cwd=default_cwd,
            )
        if not command:
            return None
        cwd = _resolve_cwd(candidate.get("cwd"), project_root=project_root, default_cwd=default_cwd)
        env = _normalize_env_mapping(candidate.get("env"))
        return {
            "command": command,
            "command_display": coerce_str(candidate.get("command_display")).strip() or _command_display(command),
            "cwd": str(cwd),
            "env": env,
            "source": coerce_str(candidate.get("source")).strip() or "mapping",
        }
    if isinstance(candidate, str):
        parsed = _parse_shell_verification_spec(candidate, project_root=project_root, default_cwd=default_cwd)
        if parsed is not None:
            return parsed
    command = _normalize_verification_command(candidate)
    if not command:
        return None
    return {
        "command": command,
        "command_display": _command_display(command),
        "cwd": str(default_cwd),
        "env": {},
        "source": "argv",
    }


def _verification_specs(
    design_contract: Mapping[str, Any],
    *,
    project_root: Path,
    doc_root: Path,
) -> list[dict[str, Any]]:
    if os.environ.get("HARNESS_VERIFICATION_SUBPROCESS"):
        return _default_verification_specs(project_root=HARNESS_ROOT, doc_root=HARNESS_ROOT)

    raw_expectation = design_contract.get("verification_expectation", [])
    if isinstance(raw_expectation, (str, bytes, bytearray)):
        candidates: Sequence[Any] = [raw_expectation]
    elif isinstance(raw_expectation, Sequence):
        candidates = raw_expectation
    else:
        candidates = []

    default_cwd = _resolve_cwd(
        design_contract.get("assigned_worktree") or design_contract.get("project_root"),
        project_root=project_root,
        default_cwd=project_root,
    )
    specs = [
        _normalize_verification_spec(candidate, project_root=project_root, default_cwd=default_cwd)
        for candidate in candidates
    ]
    normalized_specs = [spec for spec in specs if spec]
    workspace_root_text = coerce_str(design_contract.get("assigned_worktree")).strip()
    canonical_root_text = coerce_str(
        design_contract.get("canonical_project_root") or design_contract.get("project_root")
    ).strip()
    if workspace_root_text and canonical_root_text:
        workspace_root = Path(workspace_root_text).resolve()
        canonical_root = Path(canonical_root_text).resolve()
        normalized_specs = [
            _remap_verification_spec_to_workspace(
                spec,
                workspace_root=workspace_root,
                canonical_root=canonical_root,
            )
            for spec in normalized_specs
        ]
    if normalized_specs:
        return normalized_specs
    return _default_verification_specs(project_root=project_root, doc_root=doc_root)


def _remap_verification_spec_to_workspace(
    spec: Mapping[str, Any],
    *,
    workspace_root: Path,
    canonical_root: Path,
) -> dict[str, Any]:
    rewritten = dict(spec)
    cwd_text = coerce_str(rewritten.get("cwd")).strip()
    if cwd_text:
        try:
            cwd = Path(cwd_text).resolve()
        except OSError:
            cwd = None
        if cwd == canonical_root:
            rewritten["cwd"] = str(workspace_root)
    command = rewritten.get("command", [])
    if isinstance(command, Sequence) and not isinstance(command, (str, bytes, bytearray)):
        remapped_command: list[str] = []
        for item in command:
            text = coerce_str(item).strip()
            replacement = text
            if text:
                try:
                    candidate = Path(text).resolve()
                except OSError:
                    candidate = None
                if candidate == canonical_root:
                    replacement = str(workspace_root)
                elif candidate is not None and path_within(candidate, canonical_root):
                    replacement = str(workspace_root / candidate.relative_to(canonical_root))
            remapped_command.append(replacement)
        rewritten["command"] = remapped_command
        rewritten["command_display"] = _command_display(remapped_command)
    return rewritten


def _run_verification_command(spec: Mapping[str, Any]) -> dict[str, Any]:
    command = [str(item) for item in spec.get("command", [])]
    cwd = Path(coerce_str(spec.get("cwd"))).resolve()
    env_overrides = _normalize_env_mapping(spec.get("env"))
    started_at = utc_now()
    try:
        child_env = dict(os.environ)
        child_env["HARNESS_VERIFICATION_SUBPROCESS"] = "1"
        child_env.update(env_overrides)
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            env=child_env,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except Exception as exc:  # pragma: no cover - defensive, recorded in artifact
        returncode = -1
        stdout = ""
        stderr = f"{exc.__class__.__name__}: {exc}"
    return {
        "command": command,
        "command_display": coerce_str(spec.get("command_display")).strip() or _command_display(command),
        "cwd": str(cwd),
        "env": env_overrides,
        "source": coerce_str(spec.get("source")).strip(),
        "started_at": started_at,
        "completed_at": utc_now(),
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _verification_acceptance_from_runs(
    runs: Sequence[Mapping[str, Any]],
    *,
    expected_count: int = 0,
) -> tuple[bool, list[str]]:
    findings: list[str] = []
    if not runs:
        return False, ["Execution did not record any verification runs."]

    if expected_count and len(runs) < expected_count:
        findings.append(
            f"Execution recorded {len(runs)} verification run(s) but expected {expected_count}."
        )

    for run in runs:
        command = run.get("command_display") or run.get("command") or []
        returncode = run.get("returncode")
        if returncode != 0:
            findings.append(f"Verification command {command!r} returned {returncode}.")

    return not findings, findings


def _verification_section(text: str) -> str:
    match = re.search(r"^##\s+Verification\b", text, flags=re.MULTILINE)
    if not match:
        return ""
    tail = text[match.end() :]
    next_section = re.search(r"^##\s+", tail, flags=re.MULTILINE)
    if next_section:
        tail = tail[: next_section.start()]
    return tail


def _verification_expectation_from_text(text: str, *, project_root: Path, doc_root: Path) -> list[dict[str, Any]]:
    section = _verification_section(text)
    if not section:
        return _default_verification_specs(project_root=project_root, doc_root=doc_root)
    blocks = re.findall(r"```(?:bash|sh|shell)?\s*(.*?)```", section, flags=re.DOTALL | re.IGNORECASE)
    specs: list[dict[str, Any]] = []
    for block in blocks:
        for raw_line in block.splitlines():
            command_text = raw_line.strip()
            if not command_text:
                continue
            parsed = _parse_shell_verification_spec(
                command_text,
                project_root=project_root,
                default_cwd=project_root,
            )
            if parsed is not None:
                specs.append(parsed)
    return specs or _default_verification_specs(project_root=project_root, doc_root=doc_root)


def _verification_scope_findings(
    design_contract: Mapping[str, Any],
    verification_runs: Sequence[Mapping[str, Any]],
) -> list[str]:
    findings: list[str] = []
    execution_scope = coerce_str(design_contract.get("execution_scope")).strip()
    workspace_root_text = coerce_str(
        design_contract.get("assigned_worktree") or design_contract.get("project_root")
    ).strip()
    if not execution_scope or not workspace_root_text:
        return findings
    workspace_root = Path(workspace_root_text)
    for run in verification_runs:
        command_display = coerce_str(run.get("command_display")).strip()
        cwd_text = coerce_str(run.get("cwd")).strip()
        if not cwd_text:
            findings.append("Execution recorded a verification run without a cwd.")
            continue
        cwd = Path(cwd_text)
        if execution_scope == "external_project":
            if same_path(cwd, HARNESS_ROOT):
                findings.append(
                    "Execution verified the harness repository instead of the target project root."
                )
            elif not path_within(cwd, workspace_root):
                findings.append(
                    f"Verification cwd {cwd} is outside the assigned worktree {workspace_root}."
                )
        if "tests.test_runtime_files" in command_display:
            findings.append("Execution fell back to harness-only runtime file tests.")
    return findings

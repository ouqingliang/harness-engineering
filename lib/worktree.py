from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


class WorktreeError(RuntimeError):
    pass


def _run_git(project_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def worktree_common_dir(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    probe = _run_git(path, ["rev-parse", "--git-common-dir"])
    if probe.returncode != 0:
        return None
    common_dir = probe.stdout.strip()
    if not common_dir:
        return None
    common_path = Path(common_dir)
    if not common_path.is_absolute():
        common_path = (path / common_path).resolve()
    return common_path.resolve()


def _slug(text: str, *, default: str = "worktree", max_length: int = 48) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    compact = "-".join(part for part in cleaned.split("-") if part)
    if not compact:
        compact = default
    return compact[:max_length].rstrip("-") or default


def _worktree_name(label: str, key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f"{_slug(label)}-{digest}"


def _effective_worktrees_dir(worktrees_dir: Path, project_root: Path) -> Path:
    configured_root = worktrees_dir.resolve()
    if os.name != "nt":
        return configured_root
    override_root = os.environ.get("AIMA_HARNESS_WORKTREE_ROOT", "").strip()
    base_root = Path(override_root) if override_root else Path(tempfile.gettempdir()) / "aima-harness-worktrees"
    repo_key = hashlib.sha1(str(project_root.resolve()).encode("utf-8")).hexdigest()[:12]
    return (base_root / repo_key).resolve()


def ensure_supervised_worktree(
    *,
    worktrees_dir: Path,
    project_root: Path,
    key: str,
    label: str,
) -> dict[str, str]:
    canonical_root = project_root.resolve()
    effective_worktrees_dir = _effective_worktrees_dir(worktrees_dir, canonical_root)
    repo_common_dir = worktree_common_dir(canonical_root)
    name = _worktree_name(label, key)
    if repo_common_dir is None:
        return {
            "name": name,
            "path": str(canonical_root),
            "project_root": str(canonical_root),
        }
    effective_worktrees_dir.mkdir(parents=True, exist_ok=True)
    path = (effective_worktrees_dir / name).resolve()
    if path.exists():
        current_common_dir = worktree_common_dir(path)
        if current_common_dir == repo_common_dir:
            return {
                "name": name,
                "path": str(path),
                "project_root": str(repo_common_dir.parent),
            }
        shutil.rmtree(path, ignore_errors=True)
    created = _run_git(canonical_root, ["worktree", "add", "--detach", "--force", str(path), "HEAD"])
    if created.returncode != 0:
        raise WorktreeError(created.stderr.strip() or created.stdout.strip() or "git worktree add failed")
    return {
        "name": name,
        "path": str(path),
        "project_root": str(repo_common_dir.parent),
    }


def _status_lines(worktree_root: Path) -> list[str]:
    completed = _run_git(worktree_root, ["status", "--short", "--untracked-files=all"])
    if completed.returncode != 0:
        raise WorktreeError(completed.stderr.strip() or completed.stdout.strip() or "git status failed")
    return [line.rstrip("\n") for line in completed.stdout.splitlines() if line.strip()]


def _copy_path(source: Path, target: Path) -> None:
    if source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _parse_status_entry(line: str) -> tuple[str, list[str]]:
    code = line[:2]
    raw = line[3:].strip()
    if "->" in raw and ("R" in code or "C" in code):
        parts = [part.strip() for part in raw.split("->", 1)]
        return "rename", parts
    if code == "??":
        return "copy", [raw]
    if "D" in code:
        return "delete", [raw]
    return "copy", [raw]


def promote_worktree_to_project_root(
    *,
    worktree_root: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    worktree_root = worktree_root.resolve()
    canonical_root = project_root.resolve()
    if worktree_root == canonical_root:
        return []
    actions: list[dict[str, Any]] = []
    for line in _status_lines(worktree_root):
        action, paths = _parse_status_entry(line)
        if action == "rename" and len(paths) == 2:
            old_path = canonical_root / paths[0]
            new_source = worktree_root / paths[1]
            new_target = canonical_root / paths[1]
            if old_path.exists():
                if old_path.is_dir():
                    shutil.rmtree(old_path)
                else:
                    old_path.unlink()
            if new_source.exists():
                _copy_path(new_source, new_target)
            actions.append({"action": "rename", "from": str(old_path), "to": str(new_target)})
            continue
        relative_path = paths[0]
        source = worktree_root / relative_path
        target = canonical_root / relative_path
        if action == "delete":
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            actions.append({"action": "delete", "path": str(target)})
            continue
        if not source.exists():
            continue
        _copy_path(source, target)
        actions.append({"action": "copy", "path": str(target)})
    return actions


def remove_supervised_worktree(*, project_root: Path, worktree_root: Path) -> None:
    canonical_root = project_root.resolve()
    worktree_root = worktree_root.resolve()
    if worktree_root == canonical_root:
        return
    completed = _run_git(canonical_root, ["worktree", "remove", "--force", str(worktree_root)])
    if completed.returncode != 0 and worktree_root.exists():
        shutil.rmtree(worktree_root, ignore_errors=True)
    if worktree_common_dir(canonical_root) is not None:
        _run_git(canonical_root, ["worktree", "prune", "--expire", "now"])

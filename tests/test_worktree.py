from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import worktree as worktree_module
from lib.worktree import _effective_worktrees_dir, promote_worktree_to_project_root


class WorktreePathTests(unittest.TestCase):
    def test_effective_worktrees_dir_uses_short_windows_root_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured_worktrees_dir = root / "runtime-memory" / ".harness" / "worktrees"
            project_root = root / "repo"
            override_root = root / "wt"
            expected = (override_root / hashlib.sha1(str(project_root.resolve()).encode("utf-8")).hexdigest()[:12]).resolve()

            with patch("lib.worktree.os.name", "nt"), patch.dict(
                os.environ,
                {"AIMA_HARNESS_WORKTREE_ROOT": str(override_root)},
                clear=False,
            ):
                actual = _effective_worktrees_dir(configured_worktrees_dir, project_root)

            self.assertEqual(actual, expected)

    def test_effective_worktrees_dir_keeps_runtime_root_on_non_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured_worktrees_dir = root / "runtime-memory" / ".harness" / "worktrees"
            project_root = root / "repo"

            with patch("lib.worktree.os.name", "posix"):
                actual = _effective_worktrees_dir(configured_worktrees_dir, project_root)

            self.assertEqual(actual, configured_worktrees_dir.resolve())

    def test_promote_worktree_to_project_root_ignores_runtime_harness_copies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            worktree_root = root / "worktree"
            project_root = root / "project"
            runtime_source = worktree_root / ".harness" / "artifacts" / "launchers" / "codex_exec" / "runs"
            runtime_source.mkdir(parents=True)
            (runtime_source / "artifact.txt").write_text("runtime", encoding="utf-8")
            project_doc_source = worktree_root / "docs"
            project_doc_source.mkdir(parents=True)
            (project_doc_source / "notes.txt").write_text("hello", encoding="utf-8")
            copied_targets: list[Path] = []

            def record_copy(source: Path, target: Path) -> None:
                copied_targets.append(target)
                self.assertNotIn(".harness", target.parts)

            with patch(
                "lib.worktree._status_lines",
                return_value=[
                    "?? .harness/artifacts/launchers/codex_exec/runs",
                    "?? docs/notes.txt",
                ],
            ), patch("lib.worktree._copy_path", side_effect=record_copy):
                actions = promote_worktree_to_project_root(
                    worktree_root=worktree_root,
                    project_root=project_root,
                )

            self.assertEqual(copied_targets, [project_root / "docs" / "notes.txt"])
            self.assertEqual(
                actions,
                [{"action": "copy", "path": str(project_root / "docs" / "notes.txt")}],
            )

    def test_promote_worktree_to_project_root_ignores_runtime_harness_deletes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            worktree_root = root / "worktree"
            project_root = root / "project"
            runtime_target = project_root / ".harness" / "artifacts" / "launchers" / "codex_exec" / "runs"
            runtime_target.mkdir(parents=True)
            project_doc_target = project_root / "docs" / "obsolete.txt"
            project_doc_target.parent.mkdir(parents=True)
            project_doc_target.write_text("old", encoding="utf-8")
            rmtree_calls: list[Path] = []
            unlink_calls: list[Path] = []

            def record_rmtree(path: str | Path, *args: object, **kwargs: object) -> None:
                target = Path(path)
                rmtree_calls.append(target)
                self.assertNotIn(".harness", target.parts)

            def record_unlink(self: Path, *args: object, **kwargs: object) -> None:
                unlink_calls.append(self)

            with patch(
                "lib.worktree._status_lines",
                return_value=[
                    "D  .harness/artifacts/launchers/codex_exec/runs",
                    "D  docs/obsolete.txt",
                ],
            ), patch("lib.worktree.shutil.rmtree", side_effect=record_rmtree), patch(
                "pathlib.Path.unlink",
                new=record_unlink,
            ):
                actions = promote_worktree_to_project_root(
                    worktree_root=worktree_root,
                    project_root=project_root,
                )

            self.assertEqual(rmtree_calls, [])
            self.assertEqual(unlink_calls, [project_doc_target])
            self.assertEqual(
                actions,
                [{"action": "delete", "path": str(project_doc_target)}],
            )

    def test_ensure_supervised_worktree_initializes_submodules_when_gitmodules_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            worktrees_dir = root / "worktrees"
            project_root = root / "project"
            project_root.mkdir()
            (project_root / ".gitmodules").write_text(
                (
                    "[submodule \"harness-engineering\"]\n"
                    "\tpath = harness-engineering\n"
                    "\turl = https://github.com/example/harness-engineering.git\n"
                ),
                encoding="utf-8",
            )

            repo_common_dir = root / "repo-common"
            git_calls: list[tuple[Path, tuple[str, ...]]] = []

            def fake_run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
                git_calls.append((cwd, tuple(args)))
                if args[:4] == ["config", "--file", ".gitmodules", "--get-regexp"]:
                    pattern = args[4]
                    if pattern == r"^submodule\..*\.path$":
                        return subprocess.CompletedProcess(
                            args=["git", *args],
                            returncode=0,
                            stdout="submodule.harness-engineering.path harness-engineering\n",
                            stderr="",
                        )
                    if pattern == r"^submodule\..*\.url$":
                        return subprocess.CompletedProcess(
                            args=["git", *args],
                            returncode=0,
                            stdout="submodule.harness-engineering.url https://github.com/example/harness-engineering.git\n",
                            stderr="",
                        )
                return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="", stderr="")

            expected_path = (
                worktrees_dir.resolve()
                / f"execution-{hashlib.sha1('slice-1'.encode('utf-8')).hexdigest()[:10]}"
            ).resolve()

            with patch("lib.worktree.os.name", "posix"), patch.object(
                worktree_module,
                "worktree_common_dir",
                side_effect=lambda path: repo_common_dir if path == project_root else None,
            ), patch.object(worktree_module, "_run_git", side_effect=fake_run_git):
                info = worktree_module.ensure_supervised_worktree(
                    worktrees_dir=worktrees_dir,
                    project_root=project_root,
                    key="slice-1",
                    label="execution",
                )

            self.assertEqual(info["path"], str(expected_path))
            self.assertEqual(
                git_calls,
                [
                    (project_root.resolve(), ("worktree", "add", "--detach", "--force", str(expected_path), "HEAD")),
                    (
                        project_root.resolve(),
                        ("config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"),
                    ),
                    (
                        project_root.resolve(),
                        ("config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.url$"),
                    ),
                    (
                        expected_path,
                        ("submodule", "update", "--init", "--recursive", "--", "harness-engineering"),
                    ),
                ],
            )

    def test_ensure_supervised_worktree_initializes_only_submodules_with_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            worktrees_dir = root / "worktrees"
            project_root = root / "project"
            project_root.mkdir()
            (project_root / ".gitmodules").write_text(
                "\n".join(
                    [
                        '[submodule "harness-engineering"]',
                        "\tpath = harness-engineering",
                        "\turl = https://github.com/example/harness-engineering.git",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            repo_common_dir = root / "repo-common"
            git_calls: list[tuple[Path, tuple[str, ...]]] = []

            def fake_run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
                git_calls.append((cwd, tuple(args)))
                if args[:4] == ["config", "--file", ".gitmodules", "--get-regexp"]:
                    pattern = args[4]
                    if pattern == r"^submodule\..*\.path$":
                        return subprocess.CompletedProcess(
                            args=["git", *args],
                            returncode=0,
                            stdout="submodule.harness-engineering.path harness-engineering\n",
                            stderr="",
                        )
                    if pattern == r"^submodule\..*\.url$":
                        return subprocess.CompletedProcess(
                            args=["git", *args],
                            returncode=0,
                            stdout="submodule.harness-engineering.url https://github.com/example/harness-engineering.git\n",
                            stderr="",
                        )
                return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="", stderr="")

            expected_path = (
                worktrees_dir.resolve()
                / f"execution-{hashlib.sha1('slice-1'.encode('utf-8')).hexdigest()[:10]}"
            ).resolve()

            with patch("lib.worktree.os.name", "posix"), patch.object(
                worktree_module,
                "worktree_common_dir",
                side_effect=lambda path: repo_common_dir if path == project_root else None,
            ), patch.object(worktree_module, "_run_git", side_effect=fake_run_git):
                worktree_module.ensure_supervised_worktree(
                    worktrees_dir=worktrees_dir,
                    project_root=project_root,
                    key="slice-1",
                    label="execution",
                )

            self.assertIn(
                (
                    expected_path,
                    ("submodule", "update", "--init", "--recursive", "--", "harness-engineering"),
                ),
                git_calls,
            )
            self.assertNotIn(
                (expected_path, ("submodule", "update", "--init", "--recursive")),
                git_calls,
            )


if __name__ == "__main__":
    unittest.main()

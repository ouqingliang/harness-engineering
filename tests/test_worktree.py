from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.worktree import _effective_worktrees_dir


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


if __name__ == "__main__":
    unittest.main()

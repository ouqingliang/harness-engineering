from __future__ import annotations

from pathlib import Path


def project_root_from_doc_root(doc_root: Path | str) -> Path:
    root = Path(doc_root).resolve()
    for candidate in (root, *root.parents):
        if (candidate / ".git").exists():
            return candidate
    return root.parent if root.parent != root else root


def same_path(left: Path | str, right: Path | str) -> bool:
    return Path(left).resolve() == Path(right).resolve()


def path_within(path: Path | str, root: Path | str) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False

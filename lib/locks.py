from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class RuntimeLockError(RuntimeError):
    def __init__(self, lock_path: Path, details: dict[str, Any] | None = None) -> None:
        self.lock_path = lock_path
        self.details = dict(details or {})
        owner = self.details.get("pid")
        message = f"runtime lock already exists: {lock_path}"
        if owner:
            message += f" (pid={owner})"
        super().__init__(message)


@dataclass(slots=True)
class RuntimeLock:
    lock_path: Path
    owner: dict[str, Any] | None = None
    acquired: bool = False

    @classmethod
    def for_memory_root(cls, memory_root: Path | str, name: str = "runtime.lock") -> "RuntimeLock":
        return cls(Path(memory_root) / ".harness" / "locks" / name)

    def acquire(self) -> "RuntimeLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        owner = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "acquired_at": _utc_now(),
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(self.lock_path), flags)
        except FileExistsError as exc:
            raise RuntimeLockError(self.lock_path, self.read_owner()) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(owner, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        self.owner = owner
        self.acquired = True
        return self

    def release(self) -> None:
        if not self.acquired:
            return
        self.lock_path.unlink(missing_ok=True)
        self.acquired = False

    def read_owner(self) -> dict[str, Any]:
        if not self.lock_path.exists():
            return {}
        return json.loads(self.lock_path.read_text(encoding="utf-8"))

    def __enter__(self) -> "RuntimeLock":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

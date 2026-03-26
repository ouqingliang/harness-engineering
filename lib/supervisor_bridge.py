from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .scheduler import HarnessScheduler


def _spec_mapping(spec: Any) -> dict[str, Any]:
    if isinstance(spec, dict):
        return {
            "id": str(spec.get("id", "")),
            "name": str(spec.get("name", "")),
            "order": int(spec.get("order", 100)),
            "dependencies": list(spec.get("dependencies", ()) or ()),
            "title": str(spec.get("title", "")),
            "goal": str(spec.get("goal", "")),
        }
    return {
        "id": str(getattr(spec, "agent_id", getattr(spec, "id", ""))),
        "name": str(getattr(spec, "name", "")),
        "order": int(getattr(spec, "order", 100)),
        "dependencies": list(getattr(spec, "dependencies", ()) or ()),
        "title": str(getattr(spec, "title", "")),
        "goal": str(getattr(spec, "goal", "")),
    }


@dataclass
class SupervisorBridge:
    scheduler: HarnessScheduler
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "runtime_root": str(self.scheduler.paths.harness_root),
                "mission": self.scheduler.mission.to_mapping(),
                "state": self.scheduler.state.to_mapping(),
                "agents": [_spec_mapping(spec) for spec in self.scheduler.specs],
            }

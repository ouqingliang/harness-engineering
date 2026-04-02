from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .runtime_state import (
    coerce_str,
    brief_record_path,
    ensure_runtime_layout,
    read_json_file,
    runtime_paths,
    split_known_fields,
    write_json_file,
)


@dataclass(slots=True)
class Handoff:
    from_agent: str
    to_agent: str
    goal: str
    inputs: dict[str, Any] = field(default_factory=dict)
    done_when: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Handoff":
        known, extra = split_known_fields(data, ("from", "to", "goal", "inputs", "done_when"))
        raw_inputs = known.get("inputs", {})
        inputs = dict(raw_inputs) if isinstance(raw_inputs, Mapping) else {"value": raw_inputs}
        return cls(
            from_agent=coerce_str(known.get("from")),
            to_agent=coerce_str(known.get("to")),
            goal=coerce_str(known.get("goal")),
            inputs=inputs,
            done_when=coerce_str(known.get("done_when")),
            extra=extra,
        )

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            "from": self.from_agent,
            "to": self.to_agent,
            "goal": self.goal,
            "inputs": self.inputs,
            "done_when": self.done_when,
        }
        payload.update(self.extra)
        return payload


def handoffs_dir(memory_root: Path | str) -> Path:
    return runtime_paths(memory_root).briefs_dir


def handoff_path(memory_root: Path | str, name: str) -> Path:
    return handoffs_dir(memory_root) / f"{name}.json"


def read_handoff(path: Path) -> Handoff:
    return Handoff.from_mapping(read_json_file(path))


def write_handoff(path: Path, handoff: Handoff) -> Path:
    write_json_file(path, handoff.to_mapping())
    return path


def save_handoff(memory_root: Path | str, name: str, handoff: Handoff) -> Path:
    ensure_runtime_layout(memory_root)
    path = brief_record_path(memory_root, name)
    write_json_file(path, handoff.to_mapping())
    return path

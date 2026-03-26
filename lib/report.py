from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .runtime_state import (
    coerce_str,
    ensure_runtime_layout,
    read_json_file,
    runtime_paths,
    split_known_fields,
    write_json_file,
)


@dataclass(slots=True)
class Report:
    agent: str
    status: str
    summary: str
    artifacts: list[Any] = field(default_factory=list)
    next_hint: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Report":
        known, extra = split_known_fields(data, ("agent", "status", "summary", "artifacts", "next_hint"))
        raw_artifacts = known.get("artifacts", [])
        artifacts = list(raw_artifacts) if isinstance(raw_artifacts, list) else [raw_artifacts]
        return cls(
            agent=coerce_str(known.get("agent")),
            status=coerce_str(known.get("status")),
            summary=coerce_str(known.get("summary")),
            artifacts=artifacts,
            next_hint=coerce_str(known.get("next_hint")),
            extra=extra,
        )

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            "agent": self.agent,
            "status": self.status,
            "summary": self.summary,
            "artifacts": self.artifacts,
            "next_hint": self.next_hint,
        }
        payload.update(self.extra)
        return payload


def reports_dir(memory_root: Path | str) -> Path:
    return runtime_paths(memory_root).reports_dir


def report_path(memory_root: Path | str, name: str) -> Path:
    return reports_dir(memory_root) / f"{name}.json"


def read_report(path: Path) -> Report:
    return Report.from_mapping(read_json_file(path))


def write_report(path: Path, report: Report) -> Path:
    write_json_file(path, report.to_mapping())
    return path


def save_report(memory_root: Path | str, name: str, report: Report) -> Path:
    paths = ensure_runtime_layout(memory_root)
    path = paths.reports_dir / f"{name}.json"
    write_json_file(path, report.to_mapping())
    return path

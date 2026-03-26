from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path
from typing import Any


TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}
PRIMARY_NAME_HINTS = ("readme", "index", "overview", "plan", "design", "architecture")
DECISION_GATE_MARKER = "decision-gate"
DECISION_GATE_ALT_MARKER = "decision_gate"


def _split_gate_tags(raw_tags: str) -> list[str]:
    tags: list[str] = []
    for chunk in re.split(r"[,\s]+", raw_tags.strip()):
        tag = chunk.strip().strip("[](){}<>.,;:")
        if tag:
            tags.append(tag)
    return tags


def _parse_gate_marker(line: str) -> tuple[str, list[str], str] | None:
    stripped = line.strip()
    if not stripped:
        return None

    candidate = stripped
    while candidate and candidate[0] in "-*>#":
        candidate = candidate[1:].lstrip()

    if candidate.startswith("[") and "]" in candidate:
        marker_body = candidate[1 : candidate.index("]")]
        remainder = candidate[candidate.index("]") + 1 :].strip()
        marker_text = marker_body.strip()
        marker_lower = marker_text.lower()
        if not marker_lower.startswith((DECISION_GATE_MARKER, DECISION_GATE_ALT_MARKER)):
            return None
        raw_tags = ""
        if ":" in marker_text:
            raw_tags = marker_text.split(":", 1)[1]
        elif "=" in marker_text:
            raw_tags = marker_text.split("=", 1)[1]
        elif " " in marker_text:
            raw_tags = marker_text.split(" ", 1)[1]
        tags = _split_gate_tags(raw_tags)
        marker = DECISION_GATE_ALT_MARKER if DECISION_GATE_ALT_MARKER in marker_lower else DECISION_GATE_MARKER
        return marker, tags, remainder

    lowered = candidate.lower()
    if lowered == DECISION_GATE_MARKER or lowered.startswith(f"{DECISION_GATE_MARKER} "):
        remainder = candidate[len(DECISION_GATE_MARKER) :].strip()
        return DECISION_GATE_MARKER, [], remainder
    if lowered == DECISION_GATE_ALT_MARKER or lowered.startswith(f"{DECISION_GATE_ALT_MARKER} "):
        remainder = candidate[len(DECISION_GATE_ALT_MARKER) :].strip()
        return DECISION_GATE_ALT_MARKER, [], remainder
    return None


@dataclass(frozen=True, slots=True)
class DocRecord:
    relative_path: str
    title: str
    excerpt: str
    sha256: str
    size_bytes: int

    def to_mapping(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "title": self.title,
            "excerpt": self.excerpt,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def _iter_doc_files(doc_root: Path) -> list[Path]:
    return sorted(path for path in doc_root.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES)


def _title_from_text(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        return stripped[:120]
    return path.stem


def _excerpt_from_text(text: str, limit: int = 240) -> str:
    collapsed = " ".join(part.strip() for part in text.splitlines() if part.strip())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _detect_gate_signals(relative_path: str, text: str) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        parsed = _parse_gate_marker(raw_line)
        if parsed is None:
            continue
        marker, tags, prompt = parsed
        signals.append(
            {
                "relative_path": relative_path,
                "line_number": line_number,
                "marker": marker,
                "tags": tags,
                "tag": tags[0] if tags else "decision_gate",
                "prompt": prompt,
            }
        )
    return signals


def scan_doc_root(doc_root: Path | str) -> list[DocRecord]:
    root = Path(doc_root).resolve()
    if not root.exists():
        return []
    records: list[DocRecord] = []
    for path in _iter_doc_files(root):
        text = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(root).as_posix()
        records.append(
            DocRecord(
                relative_path=relative_path,
                title=_title_from_text(path, text),
                excerpt=_excerpt_from_text(text),
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                size_bytes=path.stat().st_size,
            )
        )
    return records


def build_doc_bundle(doc_root: Path | str) -> dict[str, Any]:
    root = Path(doc_root).resolve()
    records = scan_doc_root(root)
    gate_signals: list[dict[str, Any]] = []
    for record in records:
        text = (root / record.relative_path).read_text(encoding="utf-8")
        gate_signals.extend(_detect_gate_signals(record.relative_path, text))

    primary_docs = [
        record.to_mapping()
        for record in records
        if any(hint in record.relative_path.lower() for hint in PRIMARY_NAME_HINTS)
    ] or [record.to_mapping() for record in records[:3]]

    digest_source = "|".join(f"{record.relative_path}:{record.sha256}" for record in records)
    summary = " / ".join(item["title"] for item in primary_docs[:3]) if primary_docs else ""
    return {
        "doc_root": str(root),
        "doc_count": len(records),
        "doc_digest": hashlib.sha256(digest_source.encode("utf-8")).hexdigest() if digest_source else "",
        "summary": summary,
        "docs": [record.to_mapping() for record in records],
        "primary_docs": primary_docs,
        "gate_signals": gate_signals,
    }

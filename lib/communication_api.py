from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp-{uuid.uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _json_copy(payload: Any) -> Any:
    return json.loads(json.dumps(payload, ensure_ascii=False))


@dataclass(frozen=True)
class CommunicationMessage:
    id: str
    sender: str
    body: str
    created_at: str
    gate_id: str | None = None
    kind: str = "message"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "body": self.body,
            "created_at": self.created_at,
            "gate_id": self.gate_id,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class DecisionGate:
    id: str
    title: str
    prompt: str
    source: str
    severity: str
    status: str
    created_at: str
    updated_at: str
    resolved_at: str | None = None
    resolved_by: str | None = None
    resolution: str | None = None
    context: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "source": self.source,
            "severity": self.severity,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "resolution": self.resolution,
            "context": self.context,
        }


class CommunicationStore:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root)
        self.state_file = (
            self.runtime_root / "launchers" / "communication" / "state.json"
        )
        self._lock = threading.RLock()
        self.ensure()

    def ensure(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            self._save(self._default_state())

    def _default_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "messages": [],
            "gates": [],
            "updated_at": _utc_now(),
        }

    def _load(self) -> dict[str, Any]:
        payload = _read_json(self.state_file, self._default_state())
        if not isinstance(payload, dict):
            raise ValueError("communication state must be a JSON object")
        payload.setdefault("version", 1)
        payload.setdefault("messages", [])
        payload.setdefault("gates", [])
        payload.setdefault("updated_at", _utc_now())
        return payload

    def _save(self, payload: Mapping[str, Any]) -> None:
        _write_json_atomic(self.state_file, dict(payload))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._load())

    def list_messages(self, gate_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            messages = self._load()["messages"]
            if gate_id is None:
                return deepcopy(messages)
            return [
                deepcopy(message)
                for message in messages
                if message.get("gate_id") == gate_id
            ]

    def list_gates(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            gates = self._load()["gates"]
            if status is None:
                return deepcopy(gates)
            return [deepcopy(gate) for gate in gates if gate.get("status") == status]

    def get_gate(self, gate_id: str) -> dict[str, Any]:
        with self._lock:
            for gate in self._load()["gates"]:
                if gate.get("id") == gate_id:
                    return deepcopy(gate)
        raise KeyError(gate_id)

    def append_message(
        self,
        sender: str,
        body: str,
        *,
        gate_id: str | None = None,
        kind: str = "message",
    ) -> dict[str, Any]:
        if not sender:
            raise ValueError("sender is required")
        if not body:
            raise ValueError("body is required")

        with self._lock:
            state = self._load()
            message = CommunicationMessage(
                id=_new_id("msg"),
                sender=sender,
                body=body,
                created_at=_utc_now(),
                gate_id=gate_id,
                kind=kind,
            ).to_dict()
            state["messages"].append(message)
            state["updated_at"] = _utc_now()
            self._save(state)
            return deepcopy(message)

    def open_gate(
        self,
        title: str,
        prompt: str,
        *,
        source: str = "supervisor",
        severity: str = "decision_gate",
        context: str | None = None,
    ) -> dict[str, Any]:
        if not title:
            raise ValueError("title is required")
        if not prompt:
            raise ValueError("prompt is required")

        with self._lock:
            state = self._load()
            now = _utc_now()
            gate = DecisionGate(
                id=_new_id("gate"),
                title=title,
                prompt=prompt,
                source=source,
                severity=severity,
                status="open",
                created_at=now,
                updated_at=now,
                context=context,
            ).to_dict()
            state["gates"].append(gate)
            state["messages"].append(
                CommunicationMessage(
                    id=_new_id("msg"),
                    sender="system",
                    body=prompt,
                    created_at=now,
                    gate_id=gate["id"],
                    kind="gate_opened",
                ).to_dict()
            )
            state["updated_at"] = now
            self._save(state)
            return deepcopy(gate)

    def reply_to_gate(
        self,
        gate_id: str,
        *,
        sender: str,
        body: str,
    ) -> dict[str, Any]:
        if not gate_id:
            raise ValueError("gate_id is required")
        if not sender:
            raise ValueError("sender is required")
        if not body:
            raise ValueError("body is required")

        with self._lock:
            state = self._load()
            gate: dict[str, Any] | None = None
            for candidate in state["gates"]:
                if candidate.get("id") == gate_id:
                    gate = candidate
                    break
            if gate is None:
                raise KeyError(gate_id)
            if gate.get("status") == "resolved":
                raise ValueError(f"gate {gate_id} is already resolved")

            now = _utc_now()
            gate["status"] = "resolved"
            gate["updated_at"] = now
            gate["resolved_at"] = now
            gate["resolved_by"] = sender
            gate["resolution"] = body
            answer = write_human_reply(
                self.runtime_root,
                gate_id=gate_id,
                body=body,
                sender=sender,
            )
            state["messages"].append(
                CommunicationMessage(
                    id=_new_id("msg"),
                    sender=sender,
                    body=body,
                    created_at=now,
                    gate_id=gate_id,
                    kind="gate_reply",
                ).to_dict()
            )
            state["updated_at"] = now
            self._save(state)
            gate["answer_path"] = answer["answer_path"]
            gate["answer_id"] = answer["id"]
            return deepcopy(gate)

    def pending_gate(self) -> dict[str, Any] | None:
        with self._lock:
            for gate in self._load()["gates"]:
                if gate.get("status") == "open":
                    return deepcopy(gate)
        return None


def pending_gates(store: CommunicationStore) -> list[dict[str, Any]]:
    return store.list_gates(status="open")


def write_human_reply(
    runtime_root: Path,
    *,
    gate_id: str,
    body: str,
    sender: str = "human",
    source: str = "human",
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    answers_dir = runtime_root / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    answer_id = _new_id("answer")
    answer_path = answers_dir / f"{answer_id}.json"
    record = {
        "id": answer_id,
        "question_id": gate_id,
        "gate_id": gate_id,
        "answer": body,
        "sender": sender,
        "source": source,
        "created_at": _utc_now(),
        "answer_path": str(answer_path),
    }
    _write_json_atomic(answer_path, record)
    return record


def create_server(
    runtime_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    bridge: Any | None = None,
    communication_store: CommunicationStore | None = None,
    turn_executor: Any | None = None,
) -> Any:
    from lib.runner_bridge import RunnerBridge
    from runners.codex_app_server import CodexAppServer

    store = communication_store or CommunicationStore(Path(runtime_root))
    if bridge is None:
        bridge = RunnerBridge(
            store.runtime_root,
            communication_store=store,
            turn_executor=turn_executor,
        )
    return CodexAppServer((host, port), bridge=bridge, communication_store=store)


def serve(
    runtime_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    bridge: Any | None = None,
    communication_store: CommunicationStore | None = None,
    turn_executor: Any | None = None,
    block: bool = True,
) -> Any:
    server = create_server(
        runtime_root,
        host=host,
        port=port,
        bridge=bridge,
        communication_store=communication_store,
        turn_executor=turn_executor,
    )
    if block:
        with server:
            server.serve_forever()
    return server


def coerce_gate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    gate_payload = dict(payload)
    title = str(gate_payload.get("title", "")).strip()
    prompt = str(gate_payload.get("prompt", "")).strip()
    source = str(gate_payload.get("source", "supervisor")).strip() or "supervisor"
    severity = (
        str(gate_payload.get("severity", "decision_gate")).strip()
        or "decision_gate"
    )
    context = gate_payload.get("context")
    if context is not None:
        context = str(context)
    return {
        "title": title,
        "prompt": prompt,
        "source": source,
        "severity": severity,
        "context": context,
    }

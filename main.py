from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from lib.communication_api import CommunicationStore, create_server
from lib.config_loader import load_config_mapping
from lib.documents import build_doc_bundle
from lib.locks import RuntimeLock, RuntimeLockError
from lib.project_context import project_root_from_doc_root
from lib.runtime_state import (
    HarnessConfig,
    Mission,
    RuntimeState,
    ensure_runtime_root,
    load_mission,
    load_state,
    save_mission,
    save_state,
    utc_now,
)
from lib.scheduler import HarnessScheduler
from lib.supervisor_bridge import SupervisorBridge


SCRIPT_DIR = Path(__file__).resolve().parent
AGENTS_DIR = SCRIPT_DIR / "agents"


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    name: str
    order: int
    dependencies: tuple[str, ...]
    title: str
    goal: str


class HarnessConfigError(RuntimeError):
    pass


def load_agent_spec(path: Path) -> AgentSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    task = payload.get("task", {})
    return AgentSpec(
        agent_id=str(payload["id"]),
        name=str(payload["name"]),
        order=int(payload.get("order", 100)),
        dependencies=tuple(str(item) for item in payload.get("dependencies", [])),
        title=str(task.get("title", "")),
        goal=str(task.get("goal", "")),
    )


def load_all_specs() -> list[AgentSpec]:
    specs: list[AgentSpec] = []
    for agent_id in ("decision", "design", "execution", "verification", "cleanup"):
        agent_json = AGENTS_DIR / f"{agent_id}-agent" / "agent.json"
        if agent_json.exists():
            specs.append(load_agent_spec(agent_json))
    if not specs:
        raise HarnessConfigError(f"no agent specs found under {AGENTS_DIR}")
    return sorted(specs, key=lambda item: (item.order, item.agent_id))


def validate_specs(specs: list[AgentSpec]) -> None:
    by_id = {spec.agent_id: spec for spec in specs}
    for spec in specs:
        for dependency in spec.dependencies:
            if dependency not in by_id:
                raise HarnessConfigError(f"{spec.agent_id} depends on missing agent {dependency}")


def render_plan(specs: list[AgentSpec]) -> list[dict[str, object]]:
    return [
        {
            "id": spec.agent_id,
            "name": spec.name,
            "order": spec.order,
            "dependencies": list(spec.dependencies),
            "title": spec.title,
            "goal": spec.goal,
        }
        for spec in specs
    ]


def merge_config(base: HarnessConfig, **overrides: object) -> HarnessConfig:
    payload = base.to_mapping()
    for key, value in overrides.items():
        if value is not None:
            payload[key] = value
    return HarnessConfig.from_mapping(payload)


def load_harness_config(config_path: Path) -> HarnessConfig:
    return HarnessConfig.from_mapping(load_config_mapping(config_path))


def build_or_update_mission(config: HarnessConfig, *, doc_root: Path) -> Mission:
    doc_bundle = build_doc_bundle(doc_root)
    project_root = project_root_from_doc_root(doc_root)
    goal = config.goal or doc_bundle["summary"] or f"Process docs under {doc_root}"
    return Mission(
        doc_root=str(doc_root.resolve()),
        goal=goal,
        status="active",
        round=0,
        extra={
            "doc_bundle": doc_bundle,
            "doc_count": doc_bundle["doc_count"],
            "doc_digest": doc_bundle["doc_digest"],
            "primary_docs": doc_bundle["primary_docs"],
            "project_root": str(project_root),
            "decision_gate_tags": list(config.decision_gate_tags),
            "cleanup_maintenance_interval_seconds": config.cleanup_maintenance_interval_seconds,
            "human_decisions": [],
            "auto_answers": {},
        },
    )


def load_or_reset_runtime(
    config: HarnessConfig,
    *,
    doc_root: Path,
    force_reset: bool,
) -> tuple[Mission, RuntimeState]:
    paths = ensure_runtime_root(config.memory_root)
    if force_reset or not paths.mission_file.exists():
        mission = build_or_update_mission(config, doc_root=doc_root)
        state = RuntimeState(
            active_agent="design",
            last_successful_agent="",
            retry_count=0,
            last_run_at=utc_now(),
            current_round=0,
            extra={"status": "running"},
        )
        save_mission(paths.memory_root, mission)
        save_state(paths.memory_root, state)
        return mission, state

    mission = load_mission(paths.memory_root)
    state = load_state(paths.memory_root)
    mission_changed = False
    raw_gate_tags = mission.extra.get("decision_gate_tags", [])
    current_gate_tags = list(raw_gate_tags) if isinstance(raw_gate_tags, (list, tuple, set, frozenset)) else []
    if current_gate_tags != list(config.decision_gate_tags):
        mission.extra["decision_gate_tags"] = list(config.decision_gate_tags)
        mission_changed = True
    expected_project_root = str(project_root_from_doc_root(doc_root))
    if str(mission.extra.get("project_root", "")) != expected_project_root:
        mission.extra["project_root"] = expected_project_root
        mission_changed = True
    current_cleanup_interval = int(mission.extra.get("cleanup_maintenance_interval_seconds", 0) or 0)
    if current_cleanup_interval != config.cleanup_maintenance_interval_seconds:
        mission.extra["cleanup_maintenance_interval_seconds"] = config.cleanup_maintenance_interval_seconds
        mission_changed = True
    if mission_changed:
        save_mission(paths.memory_root, mission)
    if Path(mission.doc_root).resolve() != doc_root.resolve():
        mission = build_or_update_mission(config, doc_root=doc_root)
        state = RuntimeState(
            active_agent="design",
            last_successful_agent="",
            retry_count=0,
            last_run_at=utc_now(),
            current_round=0,
            extra={"status": "running"},
        )
        save_mission(paths.memory_root, mission)
        save_state(paths.memory_root, state)
    return mission, state


def command_inspect(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Inspect the local Harness Engineering agent topology.")
    parser.add_argument("--format", choices=("json", "text"), default="text", help="output format")
    args = parser.parse_args(argv)

    try:
        specs = load_all_specs()
        validate_specs(specs)
    except (HarnessConfigError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    plan = render_plan(specs)
    if args.format == "json":
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    print("Harness Engineering agent order:")
    for row in plan:
        deps = ", ".join(row["dependencies"]) if row["dependencies"] else "-"
        print(f"- {row['order']:>3} {row['id']} deps=[{deps}]")
        print(f"  title: {row['title']}")
    return 0


def command_run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the Harness Engineering loop against a doc root.")
    parser.add_argument("--doc-root", required=True, help="project doc root")
    parser.add_argument("--goal", help="override mission goal")
    parser.add_argument("--memory-root", help="override runtime memory root")
    parser.add_argument("--host", help="communication host")
    parser.add_argument("--port", type=int, help="communication port")
    parser.add_argument("--max-turns", type=int, default=20, help="maximum scheduler turns per pass")
    parser.add_argument("--reset", action="store_true", help="reset the runtime for the provided doc root")
    parser.add_argument("--no-browser", action="store_true", help="do not try to open the local human page automatically")
    args = parser.parse_args(argv)

    base_config = load_harness_config(SCRIPT_DIR / "config.yaml")
    default_host = str(base_config.extra.get("communication_host", "127.0.0.1"))
    default_port = int(base_config.extra.get("communication_port", 8765))
    config = base_config
    config = merge_config(
        config,
        doc_root=args.doc_root,
        goal=args.goal,
        memory_root=args.memory_root,
    )

    doc_root = Path(config.doc_root)
    if not doc_root.exists():
        print(f"error: doc root does not exist: {doc_root}", file=sys.stderr)
        return 1

    specs = load_all_specs()
    validate_specs(specs)
    paths = ensure_runtime_root(config.memory_root)
    mission, state = load_or_reset_runtime(config, doc_root=doc_root, force_reset=args.reset)
    scheduler = HarnessScheduler(specs=specs, paths=paths, mission=mission, state=state)

    server = None
    server_thread = None
    try:
        with RuntimeLock.for_memory_root(paths.memory_root):
            server = create_server(
                paths.harness_root,
                host=args.host or default_host,
                port=default_port if args.port is None else args.port,
                bridge=SupervisorBridge(scheduler),
                communication_store=scheduler.communication_store,
            )
            human_url = f"http://{args.host or default_host}:{server.server_port}/"
            print(f"human reply page: {human_url}")
            if not args.no_browser:
                try:
                    webbrowser.open(human_url)
                except Exception:
                    pass
            server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
            server_thread.start()

            try:
                last_status_payload = ""
                while True:
                    result = scheduler.run_until_stable(max_turns=args.max_turns)
                    payload = json.dumps(
                        {
                            "status": result.status,
                            "pending_gate_id": result.pending_gate_id,
                            "round": result.state.current_round,
                            "active_agent": result.state.active_agent,
                            "doc_root": result.mission.doc_root,
                            "project_root": result.mission.extra.get("project_root", ""),
                            "failure_reason": result.state.extra.get("failure_reason", ""),
                            "last_failure_findings": result.state.extra.get("last_failure_findings", []),
                        },
                        ensure_ascii=False,
                    )
                    if payload != last_status_payload:
                        print(payload)
                        last_status_payload = payload
                    time.sleep(config.sleep_seconds)
            except KeyboardInterrupt:
                print(json.dumps({"status": "stopped", "reason": "keyboard_interrupt"}, ensure_ascii=False))
                return 0
            finally:
                server.shutdown()
                server.server_close()
                if server_thread is not None:
                    server_thread.join(timeout=5)
    except RuntimeLockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def command_reply(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Reply to the current pending decision gate.")
    parser.add_argument("--memory-root", default="runtime-memory", help="runtime memory root")
    parser.add_argument("--gate-id", required=True, help="decision gate id")
    parser.add_argument("--message", required=True, help="human reply text")
    parser.add_argument("--sender", default="human", help="sender name")
    args = parser.parse_args(argv)

    store = CommunicationStore(Path(args.memory_root) / ".harness")
    store.reply_to_gate(args.gate_id, sender=args.sender, body=args.message)
    print(json.dumps({"gate_id": args.gate_id, "status": "resolved"}, ensure_ascii=False))
    return 0


def command_status(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Print current harness runtime state.")
    parser.add_argument("--memory-root", default="runtime-memory", help="runtime memory root")
    args = parser.parse_args(argv)

    paths = ensure_runtime_root(args.memory_root)
    mission = load_mission(paths.memory_root)
    state = load_state(paths.memory_root)
    print(json.dumps({"mission": mission.to_mapping(), "state": state.to_mapping()}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    command = args[0] if args and not args[0].startswith("-") else "inspect"
    command_argv = args[1:] if command != "inspect" or (args and not args[0].startswith("-")) else args

    if command == "inspect":
        return command_inspect(command_argv)
    if command == "run":
        return command_run(command_argv)
    if command == "reply":
        return command_reply(command_argv)
    if command == "status":
        return command_status(command_argv)

    print(f"error: unknown command {command!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

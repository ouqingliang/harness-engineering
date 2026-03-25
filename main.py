from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


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
    for agent_json in sorted(SCRIPT_DIR.glob("*-agent/agent.json")):
        specs.append(load_agent_spec(agent_json))
    if not specs:
        raise HarnessConfigError(f"no agent specs found under {SCRIPT_DIR}")
    return sorted(specs, key=lambda item: (item.order, item.agent_id))


def validate_specs(specs: list[AgentSpec]) -> None:
    by_id = {spec.agent_id: spec for spec in specs}
    for spec in specs:
        for dependency in spec.dependencies:
            if dependency not in by_id:
                raise HarnessConfigError(
                    f"{spec.agent_id} depends on missing agent {dependency}"
                )


def render_plan(specs: list[AgentSpec]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for spec in specs:
        rows.append(
            {
                "id": spec.agent_id,
                "name": spec.name,
                "order": spec.order,
                "dependencies": list(spec.dependencies),
                "title": spec.title,
                "goal": spec.goal,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the local Harness Engineering agent topology."
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="text",
        help="output format",
    )
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


if __name__ == "__main__":
    raise SystemExit(main())

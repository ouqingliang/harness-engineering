from __future__ import annotations

import http.client
import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from lib.communication_api import CommunicationStore, create_server, pending_gates
from lib.runner_bridge import RunnerBridge, run_agent
from lib.runtime_state import HarnessConfig, RuntimeState, ensure_runtime_root, save_mission, save_state, utc_now
from lib.scheduler import HarnessScheduler
from lib.supervisor_bridge import SupervisorBridge
from main import build_or_update_mission, load_all_specs, validate_specs


def _request_json(port: int, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, json.loads(data.decode("utf-8"))


def _request_text(
    port: int,
    method: str,
    path: str,
    payload: dict[str, str] | None = None,
) -> tuple[int, str, dict[str, str]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = urlencode(payload).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    response_headers = {key: value for key, value in response.getheaders()}
    connection.close()
    return response.status, data.decode("utf-8"), response_headers


class RunnerBridgeTests(unittest.TestCase):
    def test_run_agent_writes_handoff_and_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / ".harness"
            bridge = RunnerBridge(runtime_root)
            agent_spec = {"id": "design", "name": "Design Agent", "goal": "Define the next slice."}
            mission = {"goal": "refine harness loop", "done_when": ["report written"]}
            handoff = {"from": "supervisor", "goal": "refine harness loop", "inputs": {"doc_root": "/docs"}, "done_when": ["report written"]}
            state = {"runtime_root": str(runtime_root), "cycle_id": "cycle-test", "last_agent": "supervisor"}
            runtime_paths = {
                "runtime_root": runtime_root,
                "handoff_dir": runtime_root / "handoffs",
                "report_dir": runtime_root / "reports",
                "launcher_dir": runtime_root / "launchers" / "codex_app_server",
                "state_file": runtime_root / "launchers" / "codex_app_server" / "state.json",
            }

            result = run_agent(agent_spec, handoff, runtime_paths, mission, state)

            self.assertEqual(result["agent"]["id"], "design")
            self.assertEqual(result["report"]["next_hint"], "supervisor decides next step")
            self.assertTrue(Path(result["handoff_path"]).exists())
            self.assertTrue(Path(result["report_path"]).exists())

    def test_snapshot_reports_low_level_runner_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / ".harness"
            bridge = RunnerBridge(runtime_root)

            snapshot = bridge.snapshot()

            self.assertEqual(snapshot["runtime_root"], str(runtime_root))
            self.assertEqual(snapshot["pending_gates"], [])
            self.assertTrue(Path(snapshot["runtime_paths"]["state_file"]).exists())


class SupervisorBridgeTests(unittest.TestCase):
    def test_snapshot_reports_supervisor_runtime_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            doc_root = root / "docs"
            doc_root.mkdir()
            (doc_root / "README.md").write_text("# Demo\n\nSupervisor snapshot.\n", encoding="utf-8")

            memory_root = root / "memory"
            paths = ensure_runtime_root(memory_root)
            mission = build_or_update_mission(
                HarnessConfig.from_mapping({"memory_root": str(memory_root), "doc_root": str(doc_root), "goal": "snapshot"}),
                doc_root=doc_root,
            )
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

            specs = load_all_specs()
            validate_specs(specs)
            scheduler = HarnessScheduler(specs=specs, paths=paths, mission=mission, state=state)
            bridge = SupervisorBridge(scheduler)

            snapshot = bridge.snapshot()

            self.assertEqual(snapshot["mission"]["goal"], "snapshot")
            self.assertEqual(snapshot["state"]["active_agent"], "design")
            self.assertTrue(any(agent["id"] == "design" for agent in snapshot["agents"]))


class CommunicationServerTests(unittest.TestCase):
    def test_http_surface_handles_messages_gates_replies_and_runtime(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / ".harness"
            store = CommunicationStore(runtime_root)

            class FakeBridge:
                def snapshot(self) -> dict:
                    return {
                        "runtime_root": str(runtime_root),
                        "mission": {"status": "active"},
                        "state": {"active_agent": "design"},
                        "agents": [{"id": "design"}],
                    }

            server = create_server(runtime_root, port=0, bridge=FakeBridge(), communication_store=store)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                status, payload = _request_json(port, "GET", "/health")
                self.assertEqual(status, 200)
                self.assertTrue(payload["ok"])

                status, payload = _request_json(port, "GET", "/runtime")
                self.assertEqual(status, 200)
                self.assertEqual(payload["runtime"]["state"]["active_agent"], "design")

                status, payload = _request_json(port, "POST", "/communication/messages", {"sender": "human", "body": "hello communication agent"})
                self.assertEqual(status, 200)
                self.assertEqual(payload["message"]["sender"], "human")

                status, payload = _request_json(port, "POST", "/communication/gates", {"title": "Need decision", "prompt": "Approve the mainline?"})
                self.assertEqual(status, 200)
                gate_id = payload["gate"]["id"]
                self.assertTrue(pending_gates(store))

                status, payload = _request_json(port, "GET", "/communication/gates")
                self.assertEqual(status, 200)
                self.assertTrue(any(gate["id"] == gate_id and gate["status"] == "open" for gate in payload["gates"]))

                status, body, _ = _request_text(port, "GET", "/")
                self.assertEqual(status, 200)
                self.assertIn("Harness Monitor", body)
                self.assertIn("Draft replies are kept in your browser", body)
                self.assertIn("Need decision", body)
                self.assertIn("Approve the mainline?", body)

                status, _, headers = _request_text(
                    port,
                    "POST",
                    "/human/reply",
                    {"gate_id": gate_id, "sender": "human", "message": "Proceed from the human page"},
                )
                self.assertEqual(status, 303)
                self.assertIn("/?notice=", headers.get("Location", ""))

                status, payload = _request_json(port, "GET", "/communication/gates")
                self.assertEqual(status, 200)
                self.assertTrue(any(gate["id"] == gate_id and gate["status"] == "resolved" for gate in payload["gates"]))

                status, payload = _request_json(port, "POST", "/communication/gates", {"title": "Need another decision", "prompt": "Approve the follow-up?"})
                self.assertEqual(status, 200)
                follow_up_gate_id = payload["gate"]["id"]

                status, payload = _request_json(port, "POST", f"/communication/gates/{follow_up_gate_id}/reply", {"sender": "human", "body": "Proceed"})
                self.assertEqual(status, 200)
                self.assertEqual(payload["gate"]["status"], "resolved")

                status, payload = _request_json(port, "POST", "/run", {})
                self.assertEqual(status, 404)
                self.assertEqual(payload["error"], "not found")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

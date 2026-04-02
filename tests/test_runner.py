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
from lib.runtime_state import (
    inbox_message_path,
    load_jsonl_rows,
    read_inbox_message,
    supervisor_inbox_event_log_path,
)
from lib.supervisor_bridge import SupervisorBridge


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
            communication_state_file = Path(snapshot["runtime_paths"]["communication_state_file"])
            self.assertEqual(communication_state_file.parent.name, "communication")
            self.assertEqual(communication_state_file.parent.parent.name, "inbox")

    def test_communication_agent_is_treated_like_a_regular_runner(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / ".harness"
            bridge = RunnerBridge(runtime_root)
            result = bridge.run_agent(
                {"id": "communication", "name": "Communication Surface", "goal": "Store raw human text."},
                {"goal": "store raw human text", "inputs": {}, "decision_gate": {"title": "Need decision", "prompt": "Approve the mainline?"}},
                mission={"goal": "store raw human text", "decision_gate": {"title": "Need decision", "prompt": "Approve the mainline?"}},
                state={"runtime_root": str(runtime_root), "cycle_id": "cycle-communication", "last_agent": "supervisor"},
            )

            self.assertEqual(result["report"]["status"], "completed")
            self.assertNotIn("gate_id", result["report"])
            self.assertEqual(pending_gates(bridge.communication_store), [])


class SupervisorBridgeTests(unittest.TestCase):
    def test_snapshot_reports_supervisor_runtime_state(self) -> None:
        class FakeScheduler:
            specs = [
                {"id": "design", "name": "Design Agent", "order": 20, "dependencies": (), "title": "Design the next approved slice", "goal": "Define the next slice."}
            ]

            def snapshot(self) -> dict:
                return {
                    "runtime_root": "runtime-root",
                    "mission": {"goal": "snapshot"},
                    "state": {"active_agent": "design"},
                    "agent_statuses": [{"id": "design", "name": "Design Agent", "status": "planning"}],
                    "running_agents": [],
                    "queued_slices": [],
                    "recent_events": [],
                }

        scheduler = FakeScheduler()
        bridge = SupervisorBridge(scheduler)

        snapshot = bridge.snapshot()

        self.assertEqual(snapshot["mission"]["goal"], "snapshot")
        self.assertEqual(snapshot["state"]["active_agent"], "design")
        self.assertIn("agent_statuses", snapshot)
        self.assertIn("running_agents", snapshot)
        self.assertIn("queued_slices", snapshot)
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
                        "agent_statuses": [
                            {
                                "id": "design",
                                "name": "Design Agent",
                                "status": "planning",
                                "summary": "Preparing the current design contract.",
                                "queued": 2,
                                "worktree": "C:/tmp/design-worktree",
                                "current_slice": "Phase 1",
                                "current_brief": "Refine the contract before execution.",
                                "details": [],
                            },
                            {
                                "id": "execution",
                                "name": "Execution Agent",
                                "status": "running",
                                "summary": "Executing approved slices in the background.",
                                "running": 1,
                                "worktree": "C:/tmp/execution-worktree",
                                "current_slice": "Phase 2",
                                "current_brief": "Implement the approved slice in the worktree.",
                                "details": ["Phase 2"],
                            },
                            {
                                "id": "audit",
                                "name": "Audit Agent",
                                "status": "waiting",
                                "summary": "Waiting for supervisor routing.",
                                "waiting": "queued for supervisor",
                                "blocked": True,
                                "worktree": "C:/tmp/audit-worktree",
                                "current_slice": "Phase 2",
                                "current_brief": "Audit verdict is pending.",
                                "details": [],
                            },
                        ],
                        "running_agents": [{"id": "execution", "status": "running", "phase_title": "Phase 2"}],
                        "queued_slices": [{"status": "prefetched", "phase_title": "Phase 3"}],
                        "recent_events": [{"recorded_at": "2026-03-26T00:00:00Z", "summary": "Execution launched"}],
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
                self.assertEqual(payload["runtime"]["agent_statuses"][0]["id"], "design")

                raw_message = "  hello communication agent  \n"
                status, payload = _request_json(port, "POST", "/communication/messages", {"sender": "human", "body": raw_message})
                self.assertEqual(status, 200)
                self.assertEqual(payload["message"]["sender"], "human")
                message_id = payload["message"]["id"]
                self.assertEqual(read_inbox_message(inbox_message_path(runtime_root.parent, message_id))["body"], raw_message)
                self.assertEqual(
                    load_jsonl_rows(supervisor_inbox_event_log_path(runtime_root.parent))[-1]["event"],
                    "communication.message_recorded",
                )

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
                self.assertIn("Agent Status", body)
                self.assertIn("Design Agent", body)
                self.assertIn("Running Agents", body)
                self.assertIn("Queued Work", body)
                self.assertIn("queued", body)
                self.assertIn("running", body)
                self.assertIn("waiting", body)
                self.assertIn("blocked", body)
                self.assertIn("Worktree", body)
                self.assertIn("Current slice", body)
                self.assertIn("Current brief", body)
                self.assertIn("C:/tmp/design-worktree", body)
                self.assertIn("C:/tmp/execution-worktree", body)
                self.assertIn("C:/tmp/audit-worktree", body)
                self.assertIn("Phase 1", body)
                self.assertIn("Phase 2", body)
                self.assertIn("Refine the contract before execution.", body)
                self.assertIn("Implement the approved slice in the worktree.", body)
                self.assertIn("Audit verdict is pending.", body)
                self.assertIn("Need decision", body)
                self.assertIn("Approve the mainline?", body)

                raw_reply = "  Proceed from the human page\nwith UTF-8: ??  "
                status, _, headers = _request_text(
                    port,
                    "POST",
                    "/human/reply",
                    {"gate_id": gate_id, "sender": "human", "message": raw_reply},
                )
                self.assertEqual(status, 303)
                self.assertIn("/?notice=", headers.get("Location", ""))

                status, payload = _request_json(port, "GET", "/communication/gates")
                self.assertEqual(status, 200)
                resolved_gate = next(gate for gate in payload["gates"] if gate["id"] == gate_id)
                self.assertEqual(resolved_gate["status"], "resolved")
                self.assertEqual(read_inbox_message(Path(resolved_gate["answer_path"]))["answer"], raw_reply)
                self.assertEqual(
                    load_jsonl_rows(supervisor_inbox_event_log_path(runtime_root.parent))[-1]["event"],
                    "communication.gate_replied",
                )

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

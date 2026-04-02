from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from lib.communication_api import CommunicationStore
from lib.runtime_state import (
    inbox_message_path,
    load_jsonl_rows,
    read_inbox_message,
    supervisor_inbox_event_log_path,
)


class CommunicationSurfaceTests(unittest.TestCase):
    def test_append_message_persists_raw_text_and_supervisor_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / ".harness"
            store = CommunicationStore(runtime_root)

            raw_body = "  你好，supervisor。\n请保留原文。  "
            message = store.append_message(
                sender="human",
                body=raw_body,
                gate_id="gate-001",
                kind="reply",
            )

            message_path = inbox_message_path(runtime_root.parent, message["id"])
            self.assertEqual(message["body"], raw_body)
            self.assertEqual(read_inbox_message(message_path)["body"], raw_body)

            events = load_jsonl_rows(supervisor_inbox_event_log_path(runtime_root.parent))
            self.assertEqual(events[-1]["event"], "communication.message_recorded")
            self.assertEqual(events[-1]["message_id"], message["id"])
            self.assertEqual(events[-1]["record_path"], str(message_path))

    def test_reply_to_gate_persists_raw_text_and_gate_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / ".harness"
            store = CommunicationStore(runtime_root)
            gate = store.open_gate(title="Need decision", prompt="Approve the mainline?")

            raw_reply = "  保留原文。\n继续 mainline  "
            resolved_gate = store.reply_to_gate(gate["id"], sender="human", body=raw_reply)

            answer_path = Path(resolved_gate["answer_path"])
            answer_record = read_inbox_message(answer_path)
            self.assertEqual(answer_record["answer"], raw_reply)
            self.assertEqual(answer_record["body"], raw_reply)
            self.assertEqual(answer_record["gate_id"], gate["id"])
            self.assertEqual(resolved_gate["answer_path"], str(answer_path))
            self.assertEqual(resolved_gate["answer_id"], answer_record["id"])

            gate_record = store.get_gate(gate["id"])
            self.assertEqual(gate_record["status"], "resolved")
            self.assertEqual(gate_record["answer_path"], str(answer_path))
            self.assertEqual(gate_record["answer_id"], answer_record["id"])

            events = load_jsonl_rows(supervisor_inbox_event_log_path(runtime_root.parent))
            self.assertEqual(events[-1]["event"], "communication.gate_replied")
            self.assertEqual(events[-1]["gate_id"], gate["id"])
            self.assertEqual(events[-1]["record_path"], str(answer_path))


if __name__ == "__main__":
    unittest.main()

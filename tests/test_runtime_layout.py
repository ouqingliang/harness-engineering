from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from lib.runtime_state import (
    Mission,
    RuntimeState,
    append_event_row,
    brief_record_path,
    ensure_runtime_layout,
    event_log_path,
    gate_record_path,
    inbox_message_path,
    load_jsonl_rows,
    read_brief_record,
    read_gate_record,
    read_inbox_message,
    read_session_metadata,
    save_mission,
    save_state,
    session_metadata_path,
    write_brief_record,
    write_gate_record,
    write_inbox_message,
    write_session_metadata,
)


class RuntimeLayoutTests(unittest.TestCase):
    def test_ensure_runtime_layout_creates_only_frozen_shared_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            paths = ensure_runtime_layout(memory_root)

            self.assertEqual(
                {child.name for child in paths.harness_root.iterdir()},
                {"artifacts", "briefs", "events", "gates", "inbox", "sessions", "worktrees"},
            )
            self.assertFalse(paths.mission_file.exists())
            self.assertFalse(paths.state_file.exists())
            for removed_name in ("handoffs", "reports", "questions", "answers", "locks", "launchers"):
                self.assertFalse((paths.harness_root / removed_name).exists())

    def test_frozen_layout_supports_utf8_clean_file_reads_and_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            paths = ensure_runtime_layout(memory_root)

            save_mission(
                memory_root,
                Mission(
                    doc_root="C:/docs/项目主目录",
                    goal="稳定冻结共享运行时布局",
                    status="active",
                    round=1,
                    extra={"note": "保持 UTF-8"},
                ),
            )
            save_state(
                memory_root,
                RuntimeState(
                    active_agent="design-agent",
                    last_successful_agent="execution-agent",
                    retry_count=1,
                    last_run_at="2026-04-02T00:00:00Z",
                    current_round=1,
                    extra={"comment": "继续推进"},
                ),
            )

            session_path = session_metadata_path(memory_root, "session-utf8")
            inbox_path = inbox_message_path(memory_root, "message-utf8")
            gate_path = gate_record_path(memory_root, "gate-utf8")
            brief_path = brief_record_path(memory_root, "brief-utf8")
            event_path = event_log_path(memory_root, "session-utf8")

            write_session_metadata(session_path, {"summary": "会话元数据可读"})
            write_inbox_message(inbox_path, {"body": "来自协调者的消息"})
            write_gate_record(gate_path, {"title": "是否继续发布"})
            write_brief_record(brief_path, {"summary": "给执行代理的简报"})
            append_event_row(event_path, {"event": "session.started", "summary": "第一条事件"})

            self.assertEqual(
                {child.name for child in paths.harness_root.iterdir()},
                {
                    "artifacts",
                    "briefs",
                    "events",
                    "gates",
                    "inbox",
                    "mission.json",
                    "sessions",
                    "state.json",
                    "worktrees",
                },
            )
            self.assertEqual(read_session_metadata(session_path)["summary"], "会话元数据可读")
            self.assertEqual(read_inbox_message(inbox_path)["body"], "来自协调者的消息")
            self.assertEqual(read_gate_record(gate_path)["title"], "是否继续发布")
            self.assertEqual(read_brief_record(brief_path)["summary"], "给执行代理的简报")
            self.assertEqual(load_jsonl_rows(event_path), [{"event": "session.started", "summary": "第一条事件"}])
            self.assertIn("保持 UTF-8", paths.mission_file.read_text(encoding="utf-8"))
            self.assertIn("继续推进", paths.state_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

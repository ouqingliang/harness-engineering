from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from lib.documents import build_doc_bundle


class DocumentGateSignalTests(unittest.TestCase):
    def test_explicit_gate_marker_is_detected_without_keyword_scanning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            doc_root = Path(temp_dir)
            (doc_root / "README.md").write_text(
                "# Demo\n\nArchitecture change is discussed here, but this text is not a gate.\n\n"
                "[decision-gate:architecture_change] Confirm the architecture contract before changing public APIs.\n",
                encoding="utf-8",
            )

            bundle = build_doc_bundle(doc_root)

            self.assertEqual(bundle["doc_count"], 1)
            self.assertEqual(len(bundle["gate_signals"]), 1)
            signal = bundle["gate_signals"][0]
            self.assertEqual(signal["relative_path"], "README.md")
            self.assertEqual(signal["marker"], "decision-gate")
            self.assertEqual(signal["tag"], "architecture_change")
            self.assertEqual(signal["tags"], ["architecture_change"])
            self.assertEqual(signal["prompt"], "Confirm the architecture contract before changing public APIs.")

    def test_marker_only_gate_signal_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            doc_root = Path(temp_dir)
            (doc_root / "design.md").write_text(
                "# Notes\n\n[decision-gate] Wait for the human to confirm the plan.\n",
                encoding="utf-8",
            )

            bundle = build_doc_bundle(doc_root)

            self.assertEqual(len(bundle["gate_signals"]), 1)
            signal = bundle["gate_signals"][0]
            self.assertEqual(signal["marker"], "decision-gate")
            self.assertEqual(signal["tag"], "decision_gate")
            self.assertEqual(signal["tags"], [])
            self.assertEqual(signal["prompt"], "Wait for the human to confirm the plan.")


if __name__ == "__main__":
    unittest.main()

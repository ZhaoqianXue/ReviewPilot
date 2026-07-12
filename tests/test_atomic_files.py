import json
import tempfile
import unittest
from pathlib import Path

from reviewpilot_core.atomic_files import (
    atomic_output_path,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
)


class AtomicFilesTests(unittest.TestCase):
    def test_atomic_output_preserves_previous_file_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "result.jsonl"
            target.write_text('{"old": true}\n', encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "abort"):
                with atomic_output_path(target) as pending:
                    pending.write_text('{"new": true}\n', encoding="utf-8")
                    raise RuntimeError("abort")

            self.assertEqual(target.read_text(encoding="utf-8"), '{"old": true}\n')
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_atomic_write_helpers_replace_text_json_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_path = root / "nested" / "result.txt"
            json_path = root / "result.json"
            jsonl_path = root / "result.jsonl"

            atomic_write_text(text_path, "new text")
            atomic_write_json(json_path, {"label": "证据"})
            atomic_write_jsonl(jsonl_path, [{"row": 1}, {"row": 2}])

            self.assertEqual(text_path.read_text(encoding="utf-8"), "new text")
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8")), {"label": "证据"})
            self.assertEqual(
                [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()],
                [{"row": 1}, {"row": 2}],
            )
            self.assertEqual(list(root.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()

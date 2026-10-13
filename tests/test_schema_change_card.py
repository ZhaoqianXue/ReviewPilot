import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from reviewpilot_core.state_projection import _stored_chat_messages

ROOT = Path(__file__).resolve().parents[1]

RENDER = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const { formatCount } = require('./frontend/app.js');
const source = fs.readFileSync('./frontend/app.js', 'utf8');
const slice = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));
const context = vm.createContext({ formatCount, logo: () => '', assistantMessageHtml: (text) => text, activityMessage: () => '' });
vm.runInContext(slice('  function chatMessage(m) {', '  function prototypeWelcomeHtml('), context);
process.stdout.write(context.chatMessage(JSON.parse(process.argv[1])));
"""


def render(message):
    result = subprocess.run(["node", "-e", RENDER, json.dumps(message)], cwd=ROOT, text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout


class SchemaChangeCardTests(unittest.TestCase):
    def test_projection_passes_only_field_names_to_the_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "chat").mkdir()
            rows = [{"step": 4, "role": "a", "text": "Extraction schema updated. The draft now has 6 fields.", "source": "schema_chat",
                     "schema_change": {"added": ["llm_models", "<img src=x>"], "modified": [], "removed": [f"old_{n}" for n in range(11)], "fields": 6}},
                    {"step": 4, "role": "a", "text": "Nothing was changed.", "source": "schema_chat"}]
            (project / "chat" / "messages.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            messages = _stored_chat_messages(project)
        self.assertEqual(messages[0]["schemaChange"], {"added": ["llm_models"], "modified": [], "removed": [f"old_{n}" for n in range(11)], "fields": 6})
        self.assertNotIn("schemaChange", messages[1])

    def test_card_names_added_fields_and_folds_many_removed_fields_into_a_count(self):
        change = {"added": ["llm_models", "adaptation"], "modified": ["study_method"], "removed": [f"old_{n}" for n in range(11)], "fields": 6}
        html = render({"isAssistant": True, "text": "plain fallback", "schemaChange": change})
        self.assertIn('data-ui="schema-change-card"', html)
        self.assertNotIn("plain fallback", html)
        self.assertIn("6 fields", html)
        for name in ("llm_models", "adaptation", "study_method"):
            self.assertIn(f">{name}</span>", html)
        self.assertIn("<details><summary", html)  # eleven removed fields stay folded
        self.assertIn("11 fields</summary>", html)
        self.assertIn("white-space:normal", html)
        few = render({"isAssistant": True, "text": "", "schemaChange": {"added": [], "modified": [], "removed": ["comparators"], "fields": 1}})
        self.assertNotIn("<details>", few)
        self.assertIn(">comparators</span>", few)
        self.assertNotIn("Added", few)
        plain = render({"isAssistant": True, "text": "Line one\nLine two"})
        self.assertIn("white-space:pre-line", plain)


if __name__ == "__main__":
    unittest.main()

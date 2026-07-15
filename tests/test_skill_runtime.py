import json
import tempfile
import unittest
from pathlib import Path

from reviewpilot_core.skill_runtime import (
    SKILL_ASSIGNMENTS,
    SkillRegistry,
    bind_skill_llm_query,
)


class SkillRuntimeTests(unittest.TestCase):
    def test_every_authoritative_assignment_loads_one_nonempty_versioned_skill(self):
        registry = SkillRegistry()

        activations = [registry.activate(action, agent) for action, (agent, _skill) in SKILL_ASSIGNMENTS.items()]

        self.assertEqual(len(activations), 7)
        self.assertEqual({activation.name for activation in activations}, {
            "systematic-review-search-strategy",
            "evidence-screening",
            "structured-evidence-extraction",
            "evidence-synthesis-and-categorization",
        })
        self.assertTrue(all(activation.instructions and activation.version == "1.0.0" for activation in activations))
        self.assertTrue(all(len(activation.content_hash) == 64 for activation in activations))

    def test_deterministic_agents_have_no_skill_assignment(self):
        registry = SkillRegistry()

        for action, agent in (("collect", "CollectionAgent"), ("download-pdfs", "DownloadAgent")):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "no Agent Skill assignment"):
                registry.activate(action, agent)

    def test_agent_cannot_activate_another_agents_skill(self):
        with self.assertRaisesRegex(ValueError, "assignment mismatch"):
            SkillRegistry().activate("screen", "PromptAgent")

    def test_bound_query_injects_skill_and_writes_internal_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            calls = []

            def fake_query(*, text_prompt, system_prompt, model, provider):
                calls.append((text_prompt, system_prompt, model, provider))
                return "True", {"total_tokens": 2}

            query = bind_skill_llm_query(project, "screen", "FilteringAgent", fake_query, model="test-model")
            result = query(text_prompt="record", system_prompt="Return True or False.", model="test-model", provider="openai")
            traces = [json.loads(line) for line in (project / ".reviewpilot" / "skill_activations.jsonl").read_text(encoding="utf-8").splitlines()]

        self.assertEqual(result[0], "True")
        self.assertIn('<reviewpilot-agent-skill name="evidence-screening"', calls[0][1])
        self.assertIn("Return True or False.", calls[0][1])
        self.assertEqual(traces[0]["action"], "screen")
        self.assertEqual(traces[0]["skill"], "evidence-screening")
        self.assertEqual(traces[0]["model"], "test-model")
        self.assertNotIn("instructions", traces[0])

    def test_loader_fails_loud_on_malformed_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "evidence-screening"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# missing frontmatter\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "frontmatter"):
                SkillRegistry(root).activate("screen", "FilteringAgent")


if __name__ == "__main__":
    unittest.main()

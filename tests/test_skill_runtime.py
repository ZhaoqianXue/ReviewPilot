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

        self.assertEqual(len(activations), 6)
        self.assertEqual({activation.name for activation in activations}, {
            "systematic-review-search-strategy",
            "evidence-screening",
            "structured-evidence-extraction",
            "evidence-synthesis-and-categorization",
        })
        self.assertTrue(all(activation.instructions for activation in activations))
        self.assertEqual(registry.activate("save-search-setup", "SearchConditionAgent").version, "2.1.0")
        self.assertTrue(all(activation.version in {"2.0.0", "2.1.0"} for activation in activations))
        self.assertTrue(all(len(activation.content_hash) == 64 for activation in activations))

    def test_search_skill_contains_reusable_methodology_without_product_workflow(self):
        instructions = SkillRegistry().activate("save-search-setup", "SearchConditionAgent").instructions

        self.assertIn("Frame the scope", instructions)
        self.assertIn("minimum set of distinct concepts", instructions)
        self.assertIn("every user-facing concept as one atomic concept", instructions)
        self.assertIn("alternatives within the same conceptual dimension", instructions)
        self.assertIn("Build the concept strategy", instructions)
        self.assertIn("same concept at the same level of specificity", instructions)
        self.assertIn("independently of database-specific field tags", instructions)
        for history_specific_text in ("Biomedical:", "HCI:", "Urban:", "LLM-only", "Changing interaction", "Support and use"):
            self.assertNotIn(history_specific_text, instructions)
        for product_workflow_text in ("collection code", "pagination", "download", "source_limits", "concept_blocks"):
            self.assertNotIn(product_workflow_text, instructions)
        self.assertNotIn("Do not", instructions)
        self.assertNotIn("Never", instructions)

    def test_every_skill_is_methodology_only_and_uses_positive_instruction_style(self):
        registry = SkillRegistry()
        methodology_markers = {
            "evidence-screening": ("Operationalize criteria", "Represent uncertainty as uncertainty"),
            "structured-evidence-extraction": ("Design the schema", "source provenance"),
            "evidence-synthesis-and-categorization": ("Build categories", "Check coverage"),
        }

        for action, agent in (
            ("screen", "FilteringAgent"),
            ("generate-schema", "PromptAgent"),
            ("suggest-categories", "LeadAgentCategorization"),
        ):
            with self.subTest(action=action):
                activation = registry.activate(action, agent)
                for marker in methodology_markers[activation.name]:
                    self.assertIn(marker, activation.instructions)
                for product_contract in (
                    "workflow state",
                    "collection code",
                    "approval checkpoint",
                    "API response",
                    "frontend",
                    "pagination",
                    "retry loop",
                ):
                    self.assertNotIn(product_contract, activation.instructions)
                self.assertNotIn("Do not", activation.instructions)
                self.assertNotIn("Never", activation.instructions)

    def test_project_records_reviewable_skill_and_prompt_authoring_rules(self):
        guidelines = (Path(__file__).resolve().parents[1] / "docs" / "skill-and-prompt-authoring-guidelines.md").read_text(encoding="utf-8")

        self.assertIn("Write general-use runtime instructions for general use", guidelines)
        self.assertIn("State desired behavior positively", guidelines)
        self.assertIn("Keep examples out of general-use runtime instructions", guidelines)
        self.assertIn("Responsibility by layer", guidelines)
        self.assertIn("Performance changes require", guidelines)
        self.assertIn("Align instructions with action capability and schema", guidelines)
        self.assertIn("one authoritative representation for each fact", guidelines)
        self.assertIn("Keep deterministic inputs code-owned", guidelines)
        self.assertIn("Keep workflow orchestration outside methodology", guidelines)
        self.assertIn("Classify the prompt asset before editing it", guidelines)
        self.assertIn("Make uncertainty part of the contract", guidelines)
        self.assertIn("Bind each Skill once per model call", guidelines)
        self.assertIn("adversarial data", guidelines)

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

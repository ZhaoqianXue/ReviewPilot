import json
import tempfile
import unittest
from pathlib import Path

from agents.prompt_agent import PromptAgent
from reviewpilot_core.model_policy import PROMPT_MODEL
from reviewpilot_core.project_store import read_json


class PromptAgentTests(unittest.TestCase):
    def test_generate_extraction_prompt_calls_llm_and_writes_stage_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            calls = []

            def fake_llm_query(*, text_prompt, system_prompt, model, provider):
                calls.append((text_prompt, system_prompt, model, provider))
                return (
                    json.dumps(
                        {
                            "fields": [
                                {
                                    "name": "tool_type",
                                    "type": "Select",
                                    "description": "Kind of AI tool",
                                    "required": True,
                                    "example": "segmentation model",
                                },
                                {
                                    "name": "key_findings",
                                    "type": "Long text",
                                    "description": "Main findings",
                                },
                                {
                                    "name": "limitations",
                                    "description": "Study limitations",
                                },
                            ]
                        }
                    ),
                    {"model": "fake-model"},
                )

            result = PromptAgent(project_dir, llm_query=fake_llm_query).generate_extraction_prompt(
                {
                    "project_name": "Demo",
                    "description": "Review AI tools for surgery",
                    "primary_topic": "AI tools",
                    "domain": "surgery",
                    "extraction_fields": "tool type, key findings, limitations",
                    "relevance_prompt": {"task": "Include AI tools in surgery."},
                    "included_papers": [{"title": "Paper A", "abstract": "Surgical AI system."}],
                    "auto_approve": True,
                }
            )
            architecture_prompt = read_json(project_dir / "prompts" / "extraction_prompt.json")
            schema = read_json(project_dir / "extraction" / "extraction_schema.json")
            extraction_prompt = read_json(project_dir / "extraction" / "extraction_prompt.json")

        self.assertEqual(calls[0][2], PROMPT_MODEL)
        self.assertEqual(calls[0][3], "openai")
        self.assertIn("Review AI tools for surgery", calls[0][0])
        self.assertEqual(result["schema"]["fields"][0]["name"], "tool_type")
        self.assertEqual(result["source"], "llm")
        self.assertEqual(architecture_prompt["prompt_type"], "extraction")
        self.assertEqual(schema["fields"][0]["type"], "Select")
        self.assertTrue(schema["fields"][0]["required"])
        self.assertEqual(schema["fields"][2]["type"], "Text")
        self.assertIn("Extract information", extraction_prompt["extraction_prompt"])
        self.assertEqual(extraction_prompt["schema"]["fields"][1]["name"], "key_findings")


if __name__ == "__main__":
    unittest.main()

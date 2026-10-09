import json
import tempfile
import unittest
from pathlib import Path

from agents.prompt_agent import PromptAgent
from reviewpilot_core.review_guidance import (
    EXCLUDE_LEAD, INCLUDE_LEAD, field_excerpts, record_sample, render_coding_rules, render_screening_guidance,
    validate_coding_rules, validate_screening_guidance,
)

GUIDANCE = {
    "review_focus": "This review collates transmission parameters. A record is relevant when its own work reports them.",
    "definitions": ["Count a study as field data when it samples naturally infected hosts."],
    "include_when": ["outbreak descriptions with case counts;", "transmission models;", "seroprevalence surveys;", "risk-factor studies;"],
    "exclude_when": ["in-vitro work without field data;", "vaccine development without case counts;", "opinion pieces without data."],
    "tie_breakers": ["When no abstract is available, judge from the title."],
}
SCHEMA = {"fields": [{"name": "model_type", "type": "Select", "description": "Model type", "required": True, "example": "Compartmental"},
                     {"name": "code_available", "type": "boolean", "description": "Code", "required": False, "example": "true"}]}
RULES = {"preamble": "The review team's coding conventions.",
         "fields": [{"field": "code_available", "rules": ["True only when the authors share the model code."]},
                    {"field": "model_type", "rules": ["Branching process only when the authors present it as one."]}]}


class ReviewGuidanceTests(unittest.TestCase):
    def test_screening_guidance_renders_in_the_appendix_b_shape(self):
        text = render_screening_guidance(validate_screening_guidance(GUIDANCE))
        lines = text.splitlines()
        self.assertEqual(lines[0], "Review focus (from the review protocol):")
        self.assertIn("Domain rules for this review:", lines)
        self.assertLess(lines.index(INCLUDE_LEAD), lines.index(EXCLUDE_LEAD))
        self.assertIn("- transmission models;", lines)
        self.assertEqual(lines[-1], "When no abstract is available, judge from the title.")

    def test_screening_guidance_rejects_bad_shapes(self):
        for change in ({"include_when": ["only one"]}, {"exclude_when": GUIDANCE["include_when"][:3]},
                       {"definitions": ["a", "b", "c", "d", "e"]}, {"extra": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_screening_guidance({**GUIDANCE, **change})
        self.assertEqual(validate_screening_guidance({**GUIDANCE, "include_when": ["- 1. outbreak descriptions;"] + GUIDANCE["include_when"][1:]})["include_when"][0], "outbreak descriptions;")

    def test_coding_rules_follow_schema_order_and_names(self):
        rules = validate_coding_rules(RULES, SCHEMA)
        self.assertEqual([entry["field"] for entry in rules["fields"]], ["model_type", "code_available"])
        text = render_coding_rules(rules)
        self.assertTrue(text.startswith("REVIEW CODING PROTOCOL\nThe review team's coding conventions."))
        self.assertIn("\nModel type\n- Branching process", text)
        with self.assertRaisesRegex(ValueError, "does not declare"):
            validate_coding_rules({**RULES, "fields": [{"field": "unknown", "rules": ["x"]}]}, SCHEMA)

    def test_record_sample_is_distinct_stable_and_truncated(self):
        records = [{"title": "A study", "abstract": "x" * 50}, {"title": "A  Study", "abstract": "dup"}, {"title": "B", "abstract": "y"}]
        sample = record_sample(records, limit=5, abstract_chars=10)
        self.assertEqual(len(sample), 2)
        self.assertEqual(sample, record_sample(list(reversed(records[::2])), limit=5, abstract_chars=10))
        self.assertTrue(any(item["abstract"] == "x" * 10 + "…" for item in sample))

    def test_prompt_agent_generates_guidance_with_one_corrective_retry(self):
        replies = iter(["not json", json.dumps(GUIDANCE), json.dumps(GUIDANCE)])
        prompts = []

        def fake(*, text_prompt, system_prompt, **_kwargs):
            prompts.append((text_prompt, system_prompt))
            return next(replies), {}

        with tempfile.TemporaryDirectory() as tmp:
            result = PromptAgent(Path(tmp), llm_query=fake).generate_screening_guidance({
                "description": "Collate transmission parameters.", "criteria": {"inclusion": ["About Ebola."], "exclusion": ["In vitro only."]},
                "candidates": [{"title": "Ebola outbreak in 2014", "abstract": "We report 300 cases."}]})
        self.assertEqual(len(prompts), 3)
        self.assertIn("previous response was rejected", prompts[1][0])
        self.assertIn("YOUR DRAFT DATA", prompts[2][0])
        self.assertIn("review checklist", prompts[2][0])
        self.assertIn("prompt-design skill", prompts[0][1])
        self.assertIn('"Ebola outbreak in 2014"', prompts[0][0])
        self.assertTrue(result["text"].startswith("Review focus (from the review protocol):"))
        self.assertEqual(result["candidate_count"], 1)

    def test_field_excerpts_pick_sentences_that_speak_to_each_field(self):
        schema = {"fields": [
            {"name": "model_type", "description": "Allowed categories: Compartmental (SEIR compartments); Branching process (offspring distribution)."},
            {"name": "code_available", "description": "Whether model code is available in a repository or archive."}]}
        text = ("We fitted a renewal model to case data and estimated the offspring distribution of the branching process. "
                "The weather was mild during the outbreak period in the northern districts. "
                "All model code is available in a public repository on GitHub with an archived release.\n"
                "References\n1. A branching process offspring distribution paper with code in a repository archive.")
        excerpts = field_excerpts(text * 3, schema)
        self.assertIn("offspring distribution", excerpts["model_type"][0])
        self.assertIn("repository", excerpts["code_available"][0])
        self.assertTrue(all("weather" not in s for s in sum(excerpts.values(), [])))

    def test_coding_rules_read_field_excerpts_and_take_a_review_pass(self):
        prompts = []

        def fake(*, text_prompt, system_prompt, **_kwargs):
            prompts.append(text_prompt)
            return json.dumps(RULES), {"cost_usd": 0.001}

        schema = {"fields": [{**SCHEMA["fields"][0], "description": "Allowed categories: Compartmental (SEIR compartments); Branching process (offspring distribution)."},
                             SCHEMA["fields"][1]]}
        with tempfile.TemporaryDirectory() as tmp:
            result = PromptAgent(Path(tmp), llm_query=fake).generate_coding_rules({
                "description": "Collate transmission models.", "schema": schema,
                "papers": [{"title": "A model", "abstract": "We model Ebola.",
                            "text": "We estimated the offspring distribution of the branching process from SEIR compartments data."}]})
        self.assertEqual(len(prompts), 2)
        self.assertIn("field_excerpts", prompts[0])
        self.assertIn("coding-rules review checklist", prompts[1])
        self.assertAlmostEqual(result["llm_usage"]["cost_usd"], 0.002)
        self.assertTrue(result["text"].startswith("REVIEW CODING PROTOCOL"))

    def test_analysis_is_kept_with_the_draft_but_never_rendered(self):
        guidance = validate_screening_guidance({**GUIDANCE, "analysis": {"setting": "natural populations", "outside_setting_work": ["controlled experiments"]}})
        self.assertEqual(guidance["analysis"]["outside_setting_work"], ["controlled experiments"])
        self.assertNotIn("controlled experiments", render_screening_guidance(guidance))
        rules = validate_coding_rules({**RULES, "analysis": {"central_fields": ["model_type"]}}, SCHEMA)
        self.assertEqual(rules["analysis"]["central_fields"], ["model_type"])
        self.assertNotIn("central", render_coding_rules(rules))
        with self.assertRaises(ValueError):
            validate_coding_rules({**RULES, "analysis": {"central_fields": ["unknown"]}}, SCHEMA)


if __name__ == "__main__":
    unittest.main()

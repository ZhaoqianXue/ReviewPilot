import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from agents.filtering_agent import FilteringAgent
from reviewpilot_core.project_store import read_json
from reviewpilot_core.screening_criteria import criteria_state, save_criteria
from reviewpilot_core.screening_evidence import SYSTEM_PROMPT, evidence_prompt, parse_screening_response
from reviewpilot_core.screening_guidance import confirm, guidance_state, require_confirmed_guidance, save_draft
from reviewpilot_core.state_projection import _screening_step
from reviewpilot_core.workflow_state import initialize_workflow_state

GUIDANCE = {
    "review_focus": "This review collects yields of a crop on working farms. A record is relevant when its own work reports measured yields from working farms.",
    "definitions": ["A working farm is a commercial farm, not a research plot."],
    "include_when": ["yield measurements from working farms", "surveys of farm yields", "models fitted to farm yield data",
                     "reviews of farm yields"],
    "exclude_when": ["greenhouse or plot trials, without data from working farms", "breeding studies without yield data",
                     "opinion pieces without data"],
    "tie_breakers": ["When no abstract is available, judge from the title."],
}


def _project(root: Path) -> Path:
    project = root / "review"
    (project / "collected").mkdir(parents=True)
    (project / "prompts").mkdir()
    (project / "search_conditions.json").write_text(json.dumps({"project_name": "Yields", "description": "Crop yields on farms"}))
    (project / "collected" / "openalex.jsonl").write_text(json.dumps({"title": "Yields on 40 farms", "abstract": "We measured yields."}) + "\n")
    (project / "collected" / "summary.json").write_text(json.dumps({"platform_stats": {"openalex": 1}}))
    initialize_workflow_state(project)
    state = criteria_state(project)
    save_criteria(project, {"inclusion": ["Reports crop yields."], "exclusion": ["No yield data."], "revision": state["revision"]}, finalized=True)
    return project


class ScreeningInstructionTests(unittest.TestCase):
    def test_instruction_has_the_appendix_a_shape_with_guidance_before_the_criteria(self):
        prompt = evidence_prompt({"eligibility": {"inclusion": ["Reports crop yields."], "exclusion": ["No yield data."]},
                                  "screening_guidance": {"text": "Review focus (from the review protocol):\nFocus."}})
        template = prompt["user_prompt_template"]
        self.assertEqual(prompt["system_prompt"], SYSTEM_PROMPT)
        self.assertTrue(template.startswith("Screen this record for a systematic review using the eligibility criteria below.\n\nReview focus"))
        self.assertIn("Focus.\nInclusion criteria (the record must meet all of them):\n1. Reports crop yields.", template)
        self.assertIn("Exclusion criteria (exclude the record if any of them applies):\n1. No yield data.", template)
        self.assertIn('"likelihood": one of "definitely exclude"', template)
        self.assertIn("Title: {title}\nAbstract: {abstract}", template)

    def test_likelihood_decides_and_an_exclusion_needs_a_located_excerpt_and_a_known_rule(self):
        prompt = {"eligibility": {"exclusion": ["No yield data."]}, "screening_guidance": GUIDANCE}
        paper = {"title": "Greenhouse trial", "abstract": "We grew the crop in a greenhouse for two seasons."}
        reply = {"reason": "A greenhouse trial.", "criterion": "greenhouse or plot trials, without data from working farms",
                 "quote": "We grew the crop in a greenhouse", "include": True}
        include, evidence = parse_screening_response(json.dumps({**reply, "likelihood": "definitely exclude"}), paper, prompt)
        self.assertFalse(include)
        self.assertTrue(evidence["criterion_matched"])
        include, evidence = parse_screening_response(json.dumps({**reply, "likelihood": "uncertain", "include": False}), paper, prompt)
        self.assertTrue(include)
        self.assertTrue(evidence["uncertain"])
        include, evidence = parse_screening_response(json.dumps({**reply, "likelihood": "probably exclude", "quote": "an invented sentence"}), paper, prompt)
        self.assertFalse(include)
        self.assertTrue(evidence["uncertain"])
        include, evidence = parse_screening_response(json.dumps({**reply, "likelihood": "probably exclude", "criterion": "a rule nobody confirmed"}), paper, prompt)
        self.assertFalse(include)
        self.assertTrue(evidence["uncertain"])
        self.assertFalse(evidence["exclusion_verified"])
        include, evidence = parse_screening_response(json.dumps({**reply, "likelihood": "probably exclude",
            "criterion": "greenhouse trials, without data from working farms"}), paper, prompt)
        self.assertTrue(evidence["criterion_matched"])  # a light rewording of a confirmed rule still counts
        self.assertTrue(evidence["exclusion_verified"])


class ScreeningGuidanceFlowTests(unittest.TestCase):
    def test_draft_confirm_and_invalidate_on_a_criteria_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(Path(tmp))
            self.assertEqual(_screening_step(project), ("generate-screening-guidance", "Draft Guidance"))
            save_draft(project, GUIDANCE)
            self.assertEqual(guidance_state(project)["status"], "draft")
            self.assertEqual(_screening_step(project)[0], "confirm-screening-guidance")
            with self.assertRaises(ValueError):
                require_confirmed_guidance(project)
            with self.assertRaises(ValueError):
                confirm(project, {"revision": "stale"})
            edited = confirm(project, {"revision": guidance_state(project)["revision"],
                                       "exclude_when": GUIDANCE["exclude_when"] + ["reviews limited to breeding"]})
            self.assertEqual(edited["status"], "confirmed")
            self.assertEqual(_screening_step(project)[0], "screen")
            prompt = read_json(project / "prompts/relevance_prompt.json", {})
            self.assertIn("reviews limited to breeding", prompt["screening_guidance"]["text"])
            self.assertIn("Review focus (from the review protocol):", prompt["user_prompt_template"])
            state = criteria_state(project)
            save_criteria(project, {"inclusion": ["Reports crop yields on farms."], "exclusion": ["No yield data."], "revision": state["revision"]}, finalized=True)
            self.assertEqual(guidance_state(project)["status"], "stale")
            prompt = read_json(project / "prompts/relevance_prompt.json", {})
            self.assertNotIn("screening_guidance", prompt)
            self.assertNotIn("Review focus", prompt["user_prompt_template"])
            self.assertEqual(_screening_step(project)[0], "generate-screening-guidance")

    def test_a_new_collection_makes_the_guidance_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(Path(tmp))
            save_draft(project, GUIDANCE)
            confirm(project)
            (project / "collected" / "summary.json").write_text(json.dumps({"platform_stats": {"openalex": 2}}))
            self.assertEqual(guidance_state(project)["status"], "stale")


class ParallelScreeningTests(unittest.TestCase):
    def test_parallel_screening_keeps_the_input_order(self):
        def fake(*, text_prompt, **_kwargs):
            time.sleep(random.random() / 200)
            keep = "keep" in text_prompt
            return json.dumps({"reason": "decided", "likelihood": "definitely include" if keep else "definitely exclude",
                               "criterion": "No yield data.", "quote": "drop", "include": keep}), {}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            papers = [{"id": str(i), "title": f"{'keep' if i % 3 else 'drop'} {i}", "abstract": ""} for i in range(40)]
            prompt = evidence_prompt({"eligibility": {"inclusion": ["Reports crop yields."], "exclusion": ["No yield data."]}})
            agent = FilteringAgent(out, llm_query=fake)
            kept, dropped = agent._check_relevance(papers, prompt, out)
            self.assertEqual([p["id"] for p in kept], [str(i) for i in range(40) if i % 3])
            self.assertEqual([p["id"] for p in dropped], [str(i) for i in range(40) if not i % 3])
            log = [json.loads(line)["paper_id"] for line in (out / "filtering_log.jsonl").read_text().splitlines()]
            self.assertEqual(log, [str(i) for i in range(40)])


if __name__ == "__main__":
    unittest.main()

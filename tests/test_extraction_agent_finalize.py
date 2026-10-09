import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from agents.extraction_agent import ExtractionAgent
from reviewpilot_core.coding_rules import confirm, require_confirmed_rules, rules_state, save_draft
from reviewpilot_core.extraction_schema import build_extraction_prompts, finalize_schema, save_schema_draft
from reviewpilot_core.project_store import read_json
from reviewpilot_core.state_projection import _extraction_step
from reviewpilot_core.workflow_state import initialize_workflow_state

RULES = {"preamble": "Code what the study itself reports.",
         "fields": [{"field": "design", "rules": ["Cluster-randomized only when the authors randomize whole schools."]}]}


def _project(root: Path) -> Path:
    project = root / "review"
    (project / "filtered").mkdir(parents=True)
    (project / "search_conditions.json").write_text(json.dumps({"project_name": "Tutoring", "description": "Tutoring trials"}))
    (project / "filtered" / "included_papers.jsonl").write_text(json.dumps({"id": "1", "source": "openalex", "title": "A trial"}) + "\n")
    initialize_workflow_state(project)
    save_schema_draft(project, {"fields": [{"name": "design", "type": "Text", "description": "Trial design"}]})
    finalize_schema(project)
    return project


class ExtractionInstructionTests(unittest.TestCase):
    def test_coding_rules_stand_right_before_the_paper_text(self):
        _system, _stage, template = build_extraction_prompts({}, {"fields": [{"name": "design"}]}, "REVIEW CODING PROTOCOL\nRule.")
        self.assertIn("no other fields.\n\nREVIEW CODING PROTOCOL\nRule.\n\nPAPER EVIDENCE DATA:\n{paper_text}", template)
        _system, _stage, plain = build_extraction_prompts({}, {"fields": [{"name": "design"}]})
        self.assertIn("no other fields.\n\nPAPER EVIDENCE DATA:", plain)


class CodingRulesFlowTests(unittest.TestCase):
    def test_draft_confirm_and_invalidate_on_a_schema_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(Path(tmp))
            self.assertEqual(_extraction_step(project)[0], "generate-coding-rules")
            save_draft(project, RULES)
            self.assertEqual(_extraction_step(project), ("confirm-coding-rules", "Confirm Coding Rules"))
            with self.assertRaises(ValueError):
                require_confirmed_rules(project)
            edited = confirm(project, {"revision": rules_state(project)["revision"],
                                       "fields": [{"field": "design", "rules": ["Code the design the authors name."]}]})
            self.assertEqual(edited["status"], "confirmed")
            self.assertEqual(_extraction_step(project)[0], "run-extraction")
            prompt = read_json(project / "prompts/extraction_prompt.json", {})
            self.assertIn("Code the design the authors name.\n\nPAPER EVIDENCE DATA:", prompt["user_prompt_template"])
            # Retrieval rewrites the included papers with PDF paths; the rules stay current.
            (project / "filtered" / "included_papers.jsonl").write_text(
                json.dumps({"id": "1", "source": "openalex", "title": "A trial", "pdf_path": "pdfs/a.pdf"}) + "\n")
            self.assertEqual(rules_state(project)["status"], "confirmed")
            save_schema_draft(project, {"fields": [{"name": "design", "type": "Text", "description": "Trial design as named"}]})
            finalize_schema(project)
            self.assertEqual(rules_state(project)["status"], "stale")
            self.assertNotIn("Code the design", read_json(project / "prompts/extraction_prompt.json", {})["user_prompt_template"])
            self.assertEqual(_extraction_step(project)[0], "generate-coding-rules")


class ParallelExtractionTests(unittest.TestCase):
    def test_parallel_extraction_writes_results_in_input_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "review"
            (project / "pdfs").mkdir(parents=True)
            papers = []
            for i in range(12):
                (project / "pdfs" / f"row{i + 1}_paper.pdf").write_bytes(b"%PDF-1.4")
                papers.append({"id": str(i), "title": f"Paper {i}", "pdf_path": str(project / "pdfs" / f"row{i + 1}_paper.pdf"), "pdf_downloaded": True})
            (project / "filtered").mkdir()
            (project / "filtered" / "included_papers.jsonl").write_text("".join(json.dumps(p) + "\n" for p in papers))

            def llm(*, text_prompt, **_kwargs):
                time.sleep(random.random() / 100)
                number = text_prompt.split("TEXT OF ")[1].split("\n")[0]
                return json.dumps({"design": f"design {number}", "_field_evidence": {}}), {}

            prompt = {"system_prompt": "Extract.", "user_prompt_template": "{paper_text}", "schema": {"fields": [{"name": "design", "type": "Text"}]}}
            agent = ExtractionAgent(project, llm_query=llm, pdf_reader=lambda path: f"TEXT OF {Path(path).stem.split('_')[0][3:]}\n")
            agent.run({"extraction_prompt": prompt})
            rows = [json.loads(line) for line in (project / "extraction" / "extraction_results.jsonl").read_text().splitlines()]
            self.assertEqual([row["design"] for row in rows], [f"design {i + 1}" for i in range(12)])


if __name__ == "__main__":
    unittest.main()

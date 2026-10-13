import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reviewpilot_core import sub_agent_contracts
from reviewpilot_core.review_guidance import record_sample


class CodingRulesSampleTests(unittest.TestCase):
    def test_only_the_sampled_papers_are_read_and_the_sample_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "filtered").mkdir(parents=True)
            (project / "prompts").mkdir()
            papers = [{"id": f"p{i}", "title": f"Paper  {i}", "abstract": "We studied users.", "pdf_path": f"/tmp/p{i}.pdf"} for i in range(60)]
            (project / "filtered" / "included_papers.jsonl").write_text("".join(json.dumps(p) + "\n" for p in papers), encoding="utf-8")
            (project / "search_conditions.json").write_text(json.dumps({"description": "HCI"}), encoding="utf-8")
            (project / "prompts" / "extraction_prompt.json").write_text(json.dumps({"schema": {"fields": [{"name": "n", "type": "Integer", "description": "d"}]}}), encoding="utf-8")
            read, seen = [], {}

            class FakeAgent:
                def __init__(self, *args, **kwargs): pass
                def generate_coding_rules(self, data):
                    seen["sample"] = [item["title"] for item in record_sample(data["papers"], limit=25, abstract_chars=600)]
                    return {"preamble": "p", "fields": [{"field": "n", "rules": ["r"]}]}

            def fake_text(paper):
                read.append(paper["id"])
                return "full text"

            contract = sub_agent_contracts.CodingRulesContract(agent_cls=FakeAgent)
            with patch.object(sub_agent_contracts, "_paper_text", fake_text), \
                    patch.object(sub_agent_contracts, "bind_skill_llm_query", lambda *a, **k: None), \
                    patch("reviewpilot_core.coding_rules.save_draft", lambda project, rules: rules):
                contract.run(tmp, "demo")
        self.assertEqual(len(read), 25)
        self.assertEqual(seen["sample"], [item["title"] for item in record_sample(papers, limit=25, abstract_chars=600)])


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from reviewpilot_core import fulltext_eligibility as fte
from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.screening_evidence import FOCUS_REASON, OTHER_REASON, reason_label
from tests.test_fulltext_eligibility import make_project
from tests.test_prisma_flow_panel import LLM4HCI, render, text_of

# The LLM4HCI criteria and guidance shape of the 2026-10-10 test run, shortened.
PROMPT = {
    "eligibility": {
        "inclusion": ["The study develops, adapts, applies or evaluates a large language model.",
                      "The study reports original empirical work: a system evaluation, user study, experiment or deployment."],
        "exclusion": ["Review, survey, editorial, commentary or position paper without original empirical results.",
                      "No human interaction or user aspect is studied."],
    },
    "screening_guidance": {
        "text": "Review focus (from the review protocol):\n"
                "This review collects empirical evidence to understand how large language models are changing human-computer interaction.\n\n"
                "Domain rules for this review:\n"
                "Original empirical work means findings from a system evaluation, user study, experiment, or deployment conducted by the authors.\n"
                "A human-computer interaction or user aspect is studied when the work examines an interactive system or people's use of an LLM-based tool.\n"
                "Include when the record's own work reports or will clearly produce at least one of:\n"
                "- user studies or experiments measuring experience of LLM interactions\n"
                "Exclude when the record's own work is:\n"
                "- reviews, surveys, editorials, commentaries, or position papers that synthesize or discuss ideas without original empirical results\n"
                "When no abstract is available, judge from the title and keep the record if it plausibly concerns an LLM-based interactive tool.",
        "include_when": ["user studies or experiments measuring experience of LLM interactions"],
        "exclude_when": ["reviews, surveys, editorials, commentaries, or position papers that synthesize or discuss ideas without original empirical results"],
    },
}


class ReasonLabelTests(unittest.TestCase):
    def test_cited_rules_are_traced_to_their_criterion(self):
        cases = {
            "Review, survey, editorial, commentary or position paper without original empirical results.":
                "Review, survey, editorial, commentary or position paper without original empirical results.",
            "The study reports original empirical work: a system evaluation, user study, experiment or deployment.":
                "not met: The study reports original empirical work: a system evaluation, user study, experiment or deployment.",
            # a domain definition and an "exclude when" line, traced by their wording
            "Original empirical work means findings from a system evaluation, user study, experiment, or deployment conducted by the authors.":
                "not met: The study reports original empirical work: a system evaluation, user study, experiment or deployment.",
            "A human-computer interaction or user aspect is studied when the work examines an interactive system or people's use of an LLM-based tool.":
                "No human interaction or user aspect is studied.",
            "reviews, surveys, editorials, commentaries, or position papers that synthesize or discuss ideas without original empirical results":
                "Review, survey, editorial, commentary or position paper without original empirical results.",
            # the review focus is reported apart; include rules and procedural lines are not traced
            "This review collects empirical evidence to understand how large language models are changing human-computer interaction.": FOCUS_REASON,
            "user studies or experiments measuring experience of LLM interactions": OTHER_REASON,
            "When no abstract is available, judge from the title and keep the record if it plausibly concerns an LLM-based interactive tool.": OTHER_REASON,
            "": OTHER_REASON,
        }
        for cited, expected in cases.items():
            with self.subTest(cited=cited[:40]):
                self.assertEqual(reason_label(cited, PROMPT), expected)


class PrismaExclusionSplitTests(unittest.TestCase):
    def test_exclusions_split_by_tool_and_human_with_the_tools_grouped_by_criterion(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp))
            atomic_write_json(project / "prompts/relevance_prompt.json", {"criteria_finalized": True, **PROMPT})
            excluded = [json.loads(line) for line in (project / "filtered/excluded_papers.jsonl").read_text().splitlines()]  # one human exclusion
            evidence = lambda rule: {"screening_evidence": {"criterion": rule, "reason": "r", "likelihood": "definitely exclude"}}
            excluded += [{"id": "r1", "title": "Review A", "is_relevant": False, **evidence(PROMPT["eligibility"]["exclusion"][0])},
                         {"id": "r2", "title": "Review B", "is_relevant": False, **evidence(PROMPT["screening_guidance"]["exclude_when"][0])},
                         {"id": "r3", "title": "Confirmed", "is_relevant": False, **evidence(PROMPT["eligibility"]["exclusion"][1]),
                          "human_screening": {"decision": "exclude", "original_decision": "exclude", "reason": "agree", "criterion": "x"}},
                         {"id": "r4", "title": "Old row", "is_relevant": False}]
            atomic_write_jsonl(project / "filtered/excluded_papers.jsonl", excluded)
            screening = fte.prisma_counts(project)["screening"]
        self.assertEqual(screening["records_excluded"], 5)
        self.assertEqual((screening["records_excluded_by_reviewpilot"], screening["records_excluded_by_human"]), (4, 1))
        self.assertEqual(screening["records_excluded_by_reason"], {
            "Review, survey, editorial, commentary or position paper without original empirical results.": 2,
            "No human interaction or user aspect is studied.": 1,  # a reviewer confirmed it: still ReviewPilot's exclusion
            OTHER_REASON: 1})

    def test_panel_shows_the_split_and_the_tools_reasons(self):
        flow = json.loads(json.dumps(LLM4HCI))
        flow["screening"].update({"records_excluded_by_reviewpilot": 30, "records_excluded_by_human": 2,
                                  "records_excluded_by_reason": {"Review, survey or commentary.": 18, "not met: Original empirical work.": 12}})
        words = text_of(render(flow)["svg"])
        expected = ["Records excluded (n = 32)", "By ReviewPilot (n = 30)", "Review, survey or commentary. (n = 18)",
                    "Not met: Original empirical work. (n = 12)", "By a human (n = 2)"]
        positions = [words.find(phrase) for phrase in expected]
        self.assertNotIn(-1, positions, [p for p, at in zip(expected, positions) if at < 0])
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()

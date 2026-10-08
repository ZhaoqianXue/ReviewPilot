"""Regressions found while walking the web app from a new project up to Paper Screening."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import web_app
from agents.search_condition_agent import SearchConditionAgent
from reviewpilot_core.atomic_files import atomic_write_json
from reviewpilot_core.configuration_reuse import configuration_options
from reviewpilot_core.project_decisions import remember_confirmed
from reviewpilot_core.publication_dates import resolve_range
from reviewpilot_core.screening_criteria import concept_rules, criteria_state, save_criteria
from reviewpilot_core.task_runner import TaskConflictError
from searchers.http_retry import shorten_message


ROOT = Path(__file__).resolve().parents[1]
BLOCKS = [
    {"label": "Large language models", "role": "phenomenon", "eligibility_group": "llm", "required_for_eligibility": True, "query_terms": ["large language model", "LLM"]},
    {"label": "Sepsis", "role": "condition", "eligibility_group": "sepsis", "required_for_eligibility": True, "query_terms": ["sepsis"]},
]


def derive_reply(**overrides):
    payload = {"reply": "Required concepts: large language models and sepsis.", "title": "LLMs for Sepsis Care",
               "research_description": "Review LLM-based decision support for sepsis.", "concept_blocks": BLOCKS,
               "search_settings": {}}
    payload.update(overrides)
    return json.dumps(payload)


class ChatCreatedProjectTests(unittest.TestCase):
    def derive(self, response, **config):
        prompts = []

        def llm(**kwargs):
            prompts.append(kwargs["text_prompt"])
            return response, {}

        with tempfile.TemporaryDirectory() as tmp:
            result = SearchConditionAgent(output_dir=tmp, llm_query=llm).run({
                "project_name": "Review Llm Based Decision Support", "project_path": str(Path(tmp) / "p"),
                "description": "Review LLM-based decision support for sepsis, PubMed only, 5 results, 2022 to 2024",
                "platforms": ["pubmed", "arxiv", "openalex"], "source_limits": {"pubmed": 10, "arxiv": 10, "openalex": 10},
                "derive_search_terms": True, "interpret_chat_settings": True, **config})
        return result, prompts[0]

    def test_proposed_title_and_settings_free_question_replace_the_raw_request(self):
        result, prompt = self.derive(derive_reply(search_settings={"platforms": ["PubMed"], "max_results": {"pubmed": 5},
                                                                   "date_start": "2022-01-01", "date_end": "2024-12-31"}))
        self.assertEqual(result["project_name"], "LLMs for Sepsis Care")
        self.assertEqual(result["description"], "Review LLM-based decision support for sepsis.")
        self.assertEqual(result["platforms"], ["pubmed"])
        self.assertEqual(result["source_limits"], {"pubmed": 5})
        self.assertNotIn("Review Llm Based Decision Support", prompt)  # a placeholder name the model could echo
        self.assertTrue(result["lead_agent_reply"].endswith(
            "Search settings: PubMed; up to 5 records; published 2022-01-01 to 2024-12-31."))

    def test_unavailable_databases_are_named_and_the_rest_are_kept(self):
        result, _prompt = self.derive(derive_reply(search_settings={"platforms": ["scopus", "arxiv"], "max_results": {"arxiv": 20}}))
        self.assertEqual(result["platforms"], ["arxiv"])
        self.assertIn("Scopus is not available in ReviewPilot", result["lead_agent_reply"])
        only_unavailable, _prompt = self.derive(derive_reply(search_settings={"platforms": ["web of science"]}))
        self.assertEqual(only_unavailable["platforms"], ["pubmed", "arxiv", "openalex"])
        self.assertIn("Web of Science is not available", only_unavailable["lead_agent_reply"])

    def test_derive_prompt_keeps_language_instructions_off_the_reply_fields(self):
        # gpt-5.4-mini answered English requests in Spanish when the reply and description asked for "the user's language".
        prompt = SearchConditionAgent()._derive_prompt({}, "Review X", interpret_settings=True)
        self.assertEqual(prompt.count("language"), 1)
        self.assertIn('"title": "concise project title of 3 to 8 words in the user\'s language"', prompt)


class SearchChatChangeTests(unittest.TestCase):
    def refine(self, payload, current=None):
        current = current or {"project_name": "Demo", "description": "LLMs for sepsis", "concept_blocks": BLOCKS,
                               "platforms": ["pubmed", "openalex"], "source_limits": {"pubmed": 10, "openalex": 10},
                               "date_range": {"start": "", "end": "2026-10-08"}}
        with tempfile.TemporaryDirectory() as tmp:
            return SearchConditionAgent(output_dir=tmp, llm_query=lambda **_kw: (json.dumps(payload), {})).refine(
                {"current": {**current, "project_path": tmp}, "message": "change it"})

    def test_changes_are_described_from_the_actual_difference(self):
        added = BLOCKS + [{"label": "Chatbots", "role": "phenomenon", "eligibility_group": "llm", "required_for_eligibility": True, "query_terms": ["chatbot"]}]
        result = self.refine({"reply": "Done.", "concept_blocks": added, "search_settings": {"max_results": {"arxiv": 5}}})
        self.assertEqual(result["config"]["platforms"], ["pubmed", "openalex", "arxiv"])
        self.assertEqual(result["config"]["source_limits"], {"pubmed": 10, "openalex": 10, "arxiv": 5})
        self.assertEqual(result["changes"], ['added required concept "Chatbots"', "added source arXiv", "set arXiv to 5 results"])

    def test_a_reordered_source_list_is_not_a_change(self):
        result = self.refine({"reply": "Kept.", "concept_blocks": None, "search_settings": {"platforms": ["openalex", "pubmed"]}})
        self.assertFalse(result["changed"])
        self.assertEqual(result["changes"], [])


class ScreeningCriteriaDraftTests(unittest.TestCase):
    def test_rules_follow_concept_rows_and_skip_analytical_dimensions(self):
        blocks = BLOCKS + [
            {"label": "Chatbots", "role": "phenomenon", "eligibility_group": "llm", "required_for_eligibility": True, "query_terms": ["chatbot"]},
            {"label": "Older adults", "role": "population", "eligibility_group": "age", "required_for_eligibility": False, "query_terms": ["older adults"]},
            {"label": "Study type", "role": "analytical_dimension", "eligibility_group": "type", "required_for_eligibility": False, "query_terms": []},
        ]
        self.assertEqual(concept_rules(blocks), ["Study addresses Large language models or Chatbots.", "Study addresses Sepsis.",
                                                 "Study addresses Older adults."])

    def test_unsaved_draft_tracks_the_current_setup_and_hides_the_stale_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            atomic_write_json(project / "prompts/relevance_prompt.json", {"criteria": {"topic": {"label": "Old topic"}},
                                                                         "user_prompt_template": "OLD TEMPLATE"})
            atomic_write_json(project / "search_conditions.json", {"concept_blocks": BLOCKS})
            state = criteria_state(project)
            self.assertEqual(state["inclusion"], ["Study addresses Large language models.", "Study addresses Sepsis."])
            self.assertEqual(state["prompt"], "")
            saved = save_criteria(project, {**state, "revision": state["revision"]})
            self.assertIn("REVIEWER ELIGIBILITY CRITERIA DATA", saved["prompt"])


class DateRangeTests(unittest.TestCase):
    def test_months_cover_the_whole_month_and_impossible_dates_say_so(self):
        self.assertEqual(resolve_range({"start": "2024-02", "end": "2024-02"}), {"start": "2024-02-01", "end": "2024-02-29"})
        with self.assertRaisesRegex(ValueError, "2024-13-45 is not a calendar date"):
            resolve_range({"start": "2024-13-45"})
        with self.assertRaisesRegex(ValueError, "Dates must use YYYY-MM-DD"):
            resolve_range({"start": "yesterday"})


class MessageTests(unittest.TestCase):
    def test_source_messages_are_shortened_at_a_sentence_or_word(self):
        message = "Insufficient budget. " + "This request has no API key, so it counts against the shared budget. " * 5
        short = shorten_message(message)
        self.assertLessEqual(len(short), 300)
        self.assertTrue(short.endswith("shared budget."))
        self.assertTrue(shorten_message("word " * 100).endswith("word…"))

    def test_task_conflicts_name_the_running_work(self):
        self.assertEqual(str(TaskConflictError({"project_id": "p", "action": "collect"})),
                         "Paper collection is already running in this project. Try again when it finishes.")
        self.assertIn("still being saved", str(TaskConflictError({"project_id": "p", "action": "update-setup"})))


class WebRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "demo"
        atomic_write_json(self.project / "search_conditions.json", {
            "project_name": "Demo", "description": "LLMs for sepsis", "concept_blocks": BLOCKS, "search_terms": "x",
            "platforms": ["pubmed"], "source_limits": {"pubmed": 10}, "date_range": {"start": "", "end": "2026-10-08"}})
        patcher = patch.object(web_app, "OUTPUT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(web_app.create_app())

    def test_a_search_draft_can_be_discarded(self):
        atomic_write_json(self.project / "memory/search_setup_draft.json", {"concept_blocks": BLOCKS[:1]})
        self.assertIsNotNone(self.client.get("/projects/demo/state").json()["searchReuseDraft"])
        response = self.client.delete("/projects/demo/setup/draft")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["searchReuseDraft"])
        self.assertFalse((self.project / "memory/search_setup_draft.json").exists())

    def test_model_failures_carry_a_reason_instead_of_a_bare_500(self):
        class Unreachable:
            def __init__(self, *_args, **_kwargs):
                pass

            def handle_message(self, **_kwargs):
                raise ConnectionError("provider down")

        with patch.object(web_app, "LeadAgent", Unreachable):
            response = self.client.post("/projects/demo/chat", json={"message": "Add Scopus", "step": "search"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("could not get an answer from the language model (ConnectionError)", response.json()["detail"])

    def test_strategy_contract_failures_read_as_a_next_step(self):
        class Unusable:
            def __init__(self, *_args, **_kwargs):
                pass

            def save_search_setup(self, *_args):
                raise ValueError("SearchConditionAgent LLM did not return valid JSON")

        with patch.object(web_app, "LeadAgent", Unusable):
            response = self.client.post("/projects", json={"project_name": "X", "description": "LLM agents"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("could not turn the research question into a usable search strategy", response.json()["detail"])
        self.assertFalse((self.root / "x").exists())


class ConfigurationReuseTitleTests(unittest.TestCase):
    def test_options_use_the_name_shown_in_the_sidebar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, title in (("target", None), ("source", "Renamed conversation")):
                atomic_write_json(root / name / "search_conditions.json", {"project_name": name.title(), "concept_blocks": BLOCKS,
                                                                           "search_terms": "x", "platforms": ["pubmed"]})
                if title:
                    atomic_write_json(root / name / "session.json", {"title": title})
            remember_confirmed(root / "source", "search_setup")
            titles = {option["source_title"] for option in configuration_options(root, "target")}
        self.assertEqual(titles, {"Renamed conversation"})


class FrontendChatFailureTests(unittest.TestCase):
    def test_a_failed_chat_message_returns_to_the_input_with_the_reason(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('./frontend/app.js', 'utf8');
const context = vm.createContext({
  D: {project: {id: 'p'}, isNewProject: false, readOnlyExample: false, messages: [{step: 1, role: 'a', text: 'hi'}], steps: []},
  state: {chatDrafts: {}, chatPending: false, navigationPending: '', actionPending: '', preservedChatMessages: [], step: 'search'},
  chatSubmission: 0,
  esc: (value) => String(value),
  appendMessage(role, text) { context.D.messages = [...context.D.messages, {step: 1, role, text}]; },
  sendProjectChat: async () => { throw new Error('Paper collection is already running in this project. Try again when it finishes.'); },
});
vm.runInContext(source.slice(source.indexOf('  async function handleChatSubmit('), source.indexOf('  function projectNameFromTopic(')), context);
(async () => {
  await context.handleChatSubmit('What sources are selected?');
  assert.deepEqual(context.D.messages, [{step: 1, role: 'a', text: 'hi'}]);
  assert.equal(context.state.chatDrafts.p, 'What sources are selected?');
  assert.equal(context.state.chatFailure.text, 'What sources are selected?');
  assert.match(context.state.chatFailure.error, /already running/);
  assert.equal(context.state.chatPending, false);
  context.sendProjectChat = async () => { throw new TypeError('Failed to fetch'); };
  await context.handleChatSubmit('Retry');
  assert.match(context.state.chatFailure.error, /could not reach its server/);
  context.state.actionPending = 'collect';
  await context.handleChatSubmit('Blocked while running');
  assert.equal(context.state.chatFailure.text, 'Retry');
})().catch((error) => { console.error(error); process.exit(1); });
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_contract_for_keyboard_focus_and_dialog_semantics(self):
        source = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
        self.assertIn('data-act="step" data-step="${s.key}" role="button" tabindex="0"', source)
        self.assertIn("if (state.quickStartOpen) { state.quickStartOpen = false; state.quickStartDismissed = true;", source)
        self.assertIn('aria-label="Send message"', source)
        self.assertIn('role="dialog" aria-modal="true" aria-labelledby="rp-setup-dialog-title"', source)
        self.assertIn(">${esc(d.description)}</textarea>", source)
        self.assertIn("actionTicker = setInterval(updateElapsedLabels, 1000);", source)
        self.assertIn('data-act="discard-setup-draft"', source)


if __name__ == "__main__":
    unittest.main()

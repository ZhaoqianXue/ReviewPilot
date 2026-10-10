import json
import tempfile
import unittest
from pathlib import Path

from agents.lead_agent import LeadAgent
from agents.search_condition_agent import SearchConditionAgent
from reviewpilot_core.chat_language import reply_language, reply_language_instruction
from reviewpilot_core.workflow_state import initialize_workflow_state


class ReplyLanguageTests(unittest.TestCase):
    def test_language_follows_the_script_of_the_message(self):
        self.assertEqual(reply_language("筛选这一步会做什么？"), "Chinese")
        self.assertEqual(reply_language("What happens in screening?"), "English")
        self.assertEqual(reply_language("スクリーニングとは"), "Japanese")
        self.assertEqual(reply_language("선별 단계"), "Korean")
        self.assertIn("Write the reply in Chinese", reply_language_instruction("请把分诊加入筛选概念"))

    def test_project_chat_names_the_language_and_sees_guidance_and_rules(self):
        prompts = []

        def llm(**kwargs):
            prompts.append(kwargs["text_prompt"])
            return json.dumps({"reply": "好的。"}), {}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "demo"
            project.mkdir()
            (project / "search_conditions.json").write_text(json.dumps({"project_name": "Demo", "description": "Tutoring trials"}))
            (project / "prompts").mkdir()
            (project / "prompts" / "screening_guidance.json").write_text(json.dumps(
                {"status": "draft", "text": "Review focus (from the review protocol):\nTutoring trials.", "basis": "x"}))
            initialize_workflow_state(project)
            LeadAgent(root, llm_query=llm).reply_to_project_message("demo", "指导里写了什么？")
        self.assertIn("Write the reply in Chinese", prompts[0])
        self.assertIn("screening_guidance", prompts[0])
        self.assertIn("coding_rules", prompts[0])
        self.assertIn("prisma_counts", prompts[0])

    def test_search_prompts_name_the_reply_language_only(self):
        agent = SearchConditionAgent(Path(tempfile.mkdtemp()))
        derive = agent._derive_prompt({}, "我想做一个急诊大语言模型的综述", False)
        self.assertIn("Write the reply in Chinese.", derive)
        self.assertNotIn("research question in clear prose in the user's language", derive)
        self.assertIn("Write the reply in English.", agent._derive_prompt({}, "A review of LLMs in emergency care", False))


if __name__ == "__main__":
    unittest.main()

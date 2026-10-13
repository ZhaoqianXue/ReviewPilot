import unittest
from pathlib import Path

from agents.lead_agent import LeadAgent


class GeneralChatPromptTests(unittest.TestCase):
    def test_general_chat_cannot_claim_unsaved_changes(self):
        prompt = LeadAgent(Path("."))._project_chat_prompt({"project_name": "Review"}, "Also exclude conference abstracts.")
        self.assertIn("This reply cannot change saved settings, screening criteria, schemas, or categories.", prompt)
        self.assertIn("never describe the change as added, proposed, or pending", prompt)


if __name__ == "__main__":
    unittest.main()

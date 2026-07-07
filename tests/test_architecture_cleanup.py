from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureCleanupTests(unittest.TestCase):
    def test_workflow_actions_monolith_has_been_removed(self):
        self.assertFalse((ROOT / "reviewpilot_core" / "workflow_actions.py").exists())

    def test_lead_agent_uses_explicit_contract_adapter(self):
        source = (ROOT / "agents" / "lead_agent.py").read_text(encoding="utf-8")

        self.assertIn("WorkflowActionAdapter", source)
        self.assertIn("default_sub_agent_contracts", source)
        self.assertIn("workflow_actions function overrides are no longer supported", source)
        self.assertNotIn("from reviewpilot_core import workflow_actions", source)
        self.assertNotIn("import reviewpilot_core.workflow_actions", source)


if __name__ == "__main__":
    unittest.main()

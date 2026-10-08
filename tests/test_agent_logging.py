import tempfile
import unittest
from pathlib import Path

from agents.base_agent import BaseAgent


class _Agent(BaseAgent):
    def run(self, input_data):
        return {}


class AgentLoggingTests(unittest.TestCase):
    def test_each_project_logs_to_its_own_pipeline_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            _Agent(first, "logging_probe").logger.info("first project event")
            _Agent(second, "logging_probe").logger.info("second project event")
            for handler in _Agent(first, "logging_probe").logger.handlers + _Agent(second, "logging_probe").logger.handlers:
                handler.flush()
            first_log = (first / "logs/pipeline.log").read_text(encoding="utf-8")
            second_log = (second / "logs/pipeline.log").read_text(encoding="utf-8")
        self.assertIn("first project event", first_log)
        self.assertNotIn("second project event", first_log)
        self.assertIn("second project event", second_log)


if __name__ == "__main__":
    unittest.main()

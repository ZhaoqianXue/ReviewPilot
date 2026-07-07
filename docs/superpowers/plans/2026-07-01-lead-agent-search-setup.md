# Lead Agent Search Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route ReviewPilot web app Search Setup creation and update through a Lead Agent that delegates to `SearchConditionAgent`, without changing the visible frontend experience.

**Architecture:** Add a small `LeadAgent` orchestration layer for the first vertical slice only. `web_app.py` will normalize setup payloads as it does today, then call `LeadAgent.save_search_setup(...)`; the Lead Agent delegates to `SearchConditionAgent`, verifies the expected artifact, and returns the search conditions. Existing artifact projection and frontend layout remain intact.

**Tech Stack:** Python 3, Starlette web app, unittest, existing `agents/` package, existing JSON artifact folders.

---

### Task 1: Add LeadAgent Search Setup Orchestrator

**Files:**
- Create: `agents/lead_agent.py`
- Test: `tests/test_lead_agent.py`

- [ ] **Step 1: Write the failing test**

```python
import json
import tempfile
import unittest
from pathlib import Path

from agents.lead_agent import LeadAgent


class LeadAgentTests(unittest.TestCase):
    def test_save_search_setup_delegates_to_search_condition_agent_and_verifies_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            calls = []

            class FakeSearchConditionAgent:
                def __init__(self, output_dir):
                    self.output_dir = output_dir

                def run(self, config):
                    calls.append((self.output_dir, dict(config)))
                    project_path = Path(config["project_path"])
                    project_path.mkdir(parents=True, exist_ok=True)
                    search_conditions = {
                        **config,
                        "project_name": "Lead Agent Review",
                        "search_terms": "AI AND medicine",
                        "platforms": ["pubmed"],
                    }
                    (project_path / "search_conditions.json").write_text(
                        json.dumps(search_conditions),
                        encoding="utf-8",
                    )
                    return search_conditions

            lead_agent = LeadAgent(output_root, search_condition_agent_cls=FakeSearchConditionAgent)
            result = lead_agent.save_search_setup(
                "lead-agent-review",
                {
                    "project_name": "Lead Agent Review",
                    "description": "Review AI in medicine",
                    "search_terms": "AI AND medicine",
                    "platforms": ["pubmed"],
                    "primary_topic": "AI",
                },
            )

            written = json.loads((output_root / "lead-agent-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stage, "search_conditions")
        self.assertEqual(result.reply, "Search Setup is ready. Review it in the canvas, then run collection when you are ready.")
        self.assertEqual(result.search_conditions["project_name"], "Lead Agent Review")
        self.assertEqual(result.artifacts, [str(output_root / "lead-agent-review" / "search_conditions.json")])
        self.assertEqual(calls[0][0], str(output_root))
        self.assertEqual(calls[0][1]["project_path"], str(output_root / "lead-agent-review"))
        self.assertEqual(written["search_terms"], "AI AND medicine")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_lead_agent.py::LeadAgentTests::test_save_search_setup_delegates_to_search_condition_agent_and_verifies_artifact -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'agents.lead_agent'`.

- [ ] **Step 3: Write minimal implementation**

Create `agents/lead_agent.py`:

```python
"""Lead Agent orchestration layer for ReviewPilot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.search_condition_agent import SearchConditionAgent


@dataclass(frozen=True)
class LeadAgentResult:
    stage: str
    status: str
    reply: str
    search_conditions: dict[str, Any]
    artifacts: list[str]


class LeadAgent:
    """Bounded orchestration agent for the web app."""

    def __init__(self, output_root: Path | str, search_condition_agent_cls=SearchConditionAgent):
        self.output_root = Path(output_root)
        self.search_condition_agent_cls = search_condition_agent_cls

    def save_search_setup(self, project_id: str, config: dict[str, Any]) -> LeadAgentResult:
        project_path = self.output_root / project_id
        agent = self.search_condition_agent_cls(output_dir=str(self.output_root))
        search_conditions = agent.run({**config, "project_path": str(project_path)})
        artifact = project_path / "search_conditions.json"
        self._verify_search_setup_artifact(artifact, search_conditions)
        return LeadAgentResult(
            stage="search_conditions",
            status="completed",
            reply="Search Setup is ready. Review it in the canvas, then run collection when you are ready.",
            search_conditions=search_conditions,
            artifacts=[str(artifact)],
        )

    def _verify_search_setup_artifact(self, artifact: Path, search_conditions: dict[str, Any]) -> None:
        if not artifact.exists():
            raise ValueError(f"Search setup artifact was not written: {artifact}")
        required = ("project_name", "search_terms", "platforms")
        missing = [key for key in required if not search_conditions.get(key)]
        if missing:
            raise ValueError(f"Search setup missing required fields: {', '.join(missing)}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_lead_agent.py -q`

Expected: PASS.

### Task 2: Route Web Search Setup Through LeadAgent

**Files:**
- Modify: `web_app.py`
- Modify: `tests/test_web_app.py`

- [ ] **Step 1: Write the failing web app tests**

Update the existing create/update Search Setup routing tests in `tests/test_web_app.py` so they patch `web_app.LeadAgent` instead of `web_app.SearchConditionAgent`. The fake Lead Agent should expose `save_search_setup(project_id, config)` and return an object with a `search_conditions` dict.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_app.py::WebAppTests::test_create_project_routes_search_setup_through_lead_agent tests/test_web_app.py::WebAppTests::test_update_project_setup_routes_existing_project_through_lead_agent -q`

Expected: FAIL because `web_app.py` does not yet import or call `LeadAgent`.

- [ ] **Step 3: Write minimal implementation**

Modify `web_app.py` to import `LeadAgent`, remove the direct `SearchConditionAgent` import, and replace `_run_search_condition_agent(...)` with a helper that calls `LeadAgent(...).save_search_setup(...)`.

- [ ] **Step 4: Run focused web tests**

Run: `python -m pytest tests/test_web_app.py -q`

Expected: PASS.

### Task 3: Align User-Facing Search Setup Message

**Files:**
- Modify: `reviewpilot_core/state_projection.py`
- Test: `tests/test_state_projection.py`

- [ ] **Step 1: Write or update the failing test**

Add or update a state projection assertion so existing project chat text says `Lead Agent generated this Search Setup` instead of `SearchConditionAgent generated this Search Setup`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_state_projection.py -q`

Expected: FAIL until the projection message is updated.

- [ ] **Step 3: Write minimal implementation**

Change the Search Setup assistant message in `reviewpilot_core/state_projection.py` from `SearchConditionAgent generated this Search Setup` to `Lead Agent generated this Search Setup`.

- [ ] **Step 4: Run state projection tests**

Run: `python -m pytest tests/test_state_projection.py -q`

Expected: PASS.

### Task 4: Verify First Slice

**Files:**
- Verify only.

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_lead_agent.py tests/test_web_app.py tests/test_state_projection.py -q`

Expected: PASS.

- [ ] **Step 2: Run broader non-PDF web/core tests**

Run: `python -m pytest tests/test_web_app.py tests/test_state_projection.py tests/test_ui_state.py tests/test_task_runner.py tests/test_architecture_cleanup.py tests/test_collection_agent.py -q`

Expected: PASS.

- [ ] **Step 3: Inspect changed files**

Run: `git diff -- agents/lead_agent.py web_app.py reviewpilot_core/state_projection.py tests/test_lead_agent.py tests/test_web_app.py tests/test_state_projection.py docs/superpowers/plans/2026-07-01-lead-agent-search-setup.md`

Expected: Diff only includes the first Lead Agent Search Setup slice and plan.

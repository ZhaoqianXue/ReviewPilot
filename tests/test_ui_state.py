import unittest

from ui_state import (
    WORKFLOW_STEPS,
    build_step_states,
    clamp_resume_step,
    project_stage_label,
    schema_workbench_state,
)


class UiStateTests(unittest.TestCase):
    def test_start_state_marks_steps_pending_without_fake_locks(self):
        states = build_step_states(current_step=0, finalized=[False, False, False, False, False])

        self.assertEqual([step["status"] for step in states], ["pending"] * len(WORKFLOW_STEPS))
        self.assertTrue(all(not step["reachable"] for step in states))
        self.assertEqual(states[0]["number"], 1)
        self.assertEqual(states[0]["name"], "Search Setup")

    def test_active_workflow_marks_complete_current_and_locked_steps(self):
        states = build_step_states(current_step=4, finalized=[True, True, True, False, False])

        self.assertEqual(
            [step["status"] for step in states],
            ["complete", "complete", "complete", "current", "locked"],
        )
        self.assertEqual([step["reachable"] for step in states], [True, True, True, True, False])

    def test_completed_project_is_labeled_without_exposing_step_six(self):
        self.assertEqual(project_stage_label(6), "Complete")
        self.assertEqual(clamp_resume_step(6), 5)

    def test_unknown_or_empty_project_state_is_named_not_started(self):
        self.assertEqual(project_stage_label(0), "Not started")
        self.assertEqual(clamp_resume_step(0), 1)

    def test_schema_workbench_requires_explicit_generation_before_finalize(self):
        state = schema_workbench_state(has_schema=False, schema_finalized=False)

        self.assertEqual(state["status"], "missing")
        self.assertEqual(state["primary_action"], "Generate Schema")
        self.assertFalse(state["can_finalize"])

    def test_schema_workbench_can_finalize_existing_draft_schema(self):
        state = schema_workbench_state(has_schema=True, schema_finalized=False)

        self.assertEqual(state["status"], "draft")
        self.assertEqual(state["primary_action"], "Finalize Schema")
        self.assertTrue(state["can_finalize"])


if __name__ == "__main__":
    unittest.main()

"""Pure UI state helpers for ReviewPilot's Streamlit workflow."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowStep:
    number: int
    name: str
    description: str
    primary_action: str


WORKFLOW_STEPS = [
    WorkflowStep(1, "Search Setup", "Define topic, platforms, Boolean queries, and date limits.", "Start collection"),
    WorkflowStep(2, "Paper Screening", "Filter by date, remove duplicates, and apply relevance criteria.", "Run screening"),
    WorkflowStep(3, "Full-Text Retrieval", "Download open-access PDFs and prepare manual follow-up lists.", "Download PDFs"),
    WorkflowStep(4, "Information Extraction", "Review schema fields and extract structured evidence from PDFs.", "Finalize schema"),
    WorkflowStep(5, "Categorization", "Group extracted values into analysis-ready categories.", "Apply categorization"),
]


def clamp_resume_step(step: int | None) -> int:
    """Return a user-facing workflow step in the supported 1-5 range."""
    try:
        value = int(step or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, len(WORKFLOW_STEPS)))


def project_stage_label(step: int | None) -> str:
    """Describe project progress without exposing internal sentinel values."""
    try:
        value = int(step or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return "Not started"
    if value > len(WORKFLOW_STEPS):
        return "Complete"
    return f"Step {value} of {len(WORKFLOW_STEPS)}"


def build_step_states(current_step: int | None, finalized: list[bool] | tuple[bool, ...]) -> list[dict]:
    """Build normalized step state for sidebars, ribbons, and resume cards."""
    current = int(current_step or 0)
    finalized_values = list(finalized[: len(WORKFLOW_STEPS)])
    finalized_values.extend([False] * (len(WORKFLOW_STEPS) - len(finalized_values)))

    states = []
    for index, step in enumerate(WORKFLOW_STEPS):
        number = step.number
        done = bool(finalized_values[index])
        if current <= 0:
            status = "pending"
            reachable = False
        elif done:
            status = "complete"
            reachable = True
        elif number == current:
            status = "current"
            reachable = True
        elif number < current:
            status = "available"
            reachable = True
        else:
            status = "locked"
            reachable = False

        states.append(
            {
                "number": number,
                "name": step.name,
                "description": step.description,
                "primary_action": step.primary_action,
                "status": status,
                "reachable": reachable,
            }
        )
    return states


def schema_workbench_state(has_schema: bool, schema_finalized: bool) -> dict:
    """Return the Step 4 schema action state without triggering side effects."""
    if not has_schema:
        return {
            "status": "missing",
            "primary_action": "Generate Schema",
            "can_finalize": False,
        }
    if schema_finalized:
        return {
            "status": "finalized",
            "primary_action": "Run Extraction",
            "can_finalize": False,
        }
    return {
        "status": "draft",
        "primary_action": "Finalize Schema",
        "can_finalize": True,
    }

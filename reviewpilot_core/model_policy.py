"""Model defaults for ReviewPilot agent roles."""

LEAD_AGENT_DEV_MODEL = "gpt-6-luna"
LEAD_AGENT_PRODUCTION_MODEL = "gpt-5.4"
DEFAULT_MAX_RESULTS_PER_PLATFORM = 10
SEARCH_CONDITION_MODEL = "gpt-6-luna"
PROMPT_MODEL = "gpt-6-luna"
COLLECTION_MODEL = "gpt-6-luna"
FILTERING_MODEL = "gpt-6-luna"
DOWNLOAD_MODEL = "gpt-6-luna"
EXTRACTION_MODEL = "gpt-6-luna"
CATEGORIZATION_MODEL = "gpt-6-luna"
SUBSCRIBED_PAPER_FALLBACK_MODEL = "gpt-6-luna"
ESCALATION_MODEL = "gpt-5.4"
HARD_REASONING_ESCALATION_MODEL = "gpt-5.5"


def accepts_custom_temperature(model) -> bool:
    """o-series and GPT-6 models accept only the default temperature at their default reasoning effort."""
    return not any(marker in str(model or "") for marker in ("o3", "o4-mini", "gpt-6"))


def project_model(config, default: str) -> str:
    """Return the model saved in a project's search setup, or the role default when unset."""
    value = config.get("model") if isinstance(config, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else default

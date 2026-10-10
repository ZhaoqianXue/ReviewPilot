"""Model defaults for ReviewPilot agent roles."""

LEAD_AGENT_DEV_MODEL = "gpt-6-luna"
LEAD_AGENT_PRODUCTION_MODEL = "gpt-6-luna"
DEFAULT_MAX_RESULTS_PER_PLATFORM = 10
SEARCH_CONDITION_MODEL = "gpt-6-luna"
PROMPT_MODEL = "gpt-6-luna"
COLLECTION_MODEL = "gpt-6-luna"
FILTERING_MODEL = "gpt-6-luna"
DOWNLOAD_MODEL = "gpt-6-luna"
EXTRACTION_MODEL = "gpt-6-luna"
CATEGORIZATION_MODEL = "gpt-6-luna"
SUBSCRIBED_PAPER_FALLBACK_MODEL = "gpt-6-luna"
ESCALATION_MODEL = "gpt-6-luna"
HARD_REASONING_ESCALATION_MODEL = "gpt-6-luna"


def accepts_custom_temperature(model) -> bool:
    """o-series and GPT-6 models accept only the default temperature at their default reasoning effort."""
    return not any(marker in str(model or "") for marker in ("o3", "o4-mini", "gpt-6"))


def project_model(config, default: str) -> str:
    """Return the model saved in a project's search setup, or the role default when unset."""
    value = config.get("model") if isinstance(config, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else default


def supports_web_search(model) -> bool:
    """Whether ReviewPilot can give this model a provider web-search tool.

    Extraction calls OpenAI's Responses API, whose ``web_search`` tool the GPT-4o and
    later GPT and o-series models accept. Models served elsewhere (for example the
    open models hosted on Together) have no such tool here.
    """
    name = str(model or "").strip().lower()
    return name.startswith(("gpt-4o", "gpt-4.1", "gpt-5", "gpt-6", "o3", "o4")) and name != "gpt-4.1-nano"


WEB_SEARCH_CALL_USD = 0.01  # OpenAI web_search tool, per call, on top of tokens
FALLBACK_RATES_PER_MILLION = (0.25, 2.0)  # input, output; used for models missing from the price table


def token_cost(model, input_tokens: int, output_tokens: int) -> float:
    """USD for one call, from the shared price table in utils.llm."""
    try:
        from utils.llm import MODEL_COSTS
    except ImportError:
        MODEL_COSTS = {}
    input_rate, output_rate = MODEL_COSTS.get(str(model or ""), FALLBACK_RATES_PER_MILLION)
    return input_tokens / 1_000_000 * input_rate + output_tokens / 1_000_000 * output_rate

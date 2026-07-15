# ReviewPilot Lead Agent Architecture Setting

## Position
ReviewPilot's target agent architecture is **one Lead Agent plus six specialized evidence Sub Agents**. The Lead Agent owns user conversation, pipeline orchestration, and final result presentation. The six Sub Agents retain focused responsibilities from the prototype agent system. The execution model is a bounded evidence pipeline plus a user-facing result stage, not an unconstrained autonomous agent loop.

There are two layers that must not be collapsed:

1. **Internal evidence pipeline:** seven artifact-producing stages that search, screen, retrieve, and extract evidence.
2. **User-facing product workflow:** five ReviewPilot steps ending with **Categorization & Analysis**, where extracted evidence is organized into semantic groups and presented as the final review workspace.

The earlier idea that categorization is merely optional is not consistent with the prototype Streamlit app or the writing. Categorization should not be mixed into information extraction, but it must remain the default final user-facing stage.

This setting replaces the current implicit web assistant behavior with a real orchestration layer. The frontend should render conversation, project state, artifacts, and available actions, but it should not decide pipeline logic or pretend to be the assistant.

## UI/UX Boundary
This architecture is a kernel/orchestration setting, not a redesign of the ReviewPilot web interface. The UI/UX interaction rules live in [LEAD_AGENT_UI_UX_BOUNDARY.md](LEAD_AGENT_UI_UX_BOUNDARY.md). Read that document together with this one before changing the frontend, especially the rule that all click interactions belong in the canvas while the chat box remains text-only.

## Agent Skill Boundary

The implemented Agent Skill layer is defined in [AGENT_SKILL_ARCHITECTURE.md](AGENT_SKILL_ARCHITECTURE.md). Read that document together with this one before changing system prompts, adding reusable review methods, or assigning Skills to Agents. This document remains authoritative for the Agent roster, workflow, orchestration, and artifact contracts; the Agent Skill document remains authoritative for the four-Skill catalog, system-prompt boundary, assignment, activation, loading, provenance, and engineering verification policy.

## Agent Memory Boundary

The production Agent Memory design is defined in [AGENT_MEMORY_ARCHITECTURE.md](AGENT_MEMORY_ARCHITECTURE.md). It is authoritative for invisible Lead-owned session context, cross-project memory, context assembly, storage, promotion, retrieval, failure behavior, and minimal cross-project user controls. Project artifacts and the workflow ledger remain authoritative state and must never be replaced by remembered context.

## Anthropic Basis
This architecture follows Anthropic's orchestrator-worker guidance: a central agent decomposes work, delegates to specialized workers, and synthesizes results. Anthropic's research system uses a lead agent with subagents because subagents provide parallelism, isolated context windows, and separation of concerns. Anthropic also warns that agentic systems should add complexity only when the task value and complexity justify it; ReviewPilot qualifies because systematic reviews involve multi-stage research, search, screening, extraction, long context, and many external tools.

## Agent Roster
| Role | Count | Responsibility |
|---|---:|---|
| Lead Agent | 1 | Owns user dialogue, determines current stage from artifacts, routes work, calls Sub Agents, verifies outputs, asks for user confirmation when needed |
| `SearchConditionAgent` | 1 | Produces search setup and `search_conditions.json` |
| `PromptAgent` | 1 | Produces both relevance and extraction prompts |
| `CollectionAgent` | 1 | Searches configured academic platforms and writes collection outputs |
| `FilteringAgent` | 1 | Deduplicates and screens collected records using relevance criteria |
| `DownloadAgent` | 1 | Retrieves full-text PDFs for included papers |
| `ExtractionAgent` | 1 | Extracts structured information from PDFs or fallback metadata |
| Lead-owned Categorization & Analysis capability | 0 Sub Agents | Groups extracted field values into semantic categories, presents final result summaries, and writes final categorized artifacts |

There are **six evidence Sub Agents**, not seven. The internal evidence pipeline has seven stages because `PromptAgent` is used twice. Categorization & Analysis is a Lead Agent-owned product/result capability, not a seventh evidence Sub Agent. If ReviewPilot later needs independent scaling, evals, or long-running categorization recovery, this capability can be promoted into a dedicated `CategorizationAgent` through a separate architecture decision.

## Model Assignment Setting
The model ID must use OpenAI's canonical hyphenated spelling: `gpt-5.4-mini`, not `gpt5.4mini`.

`gpt-5.4-mini` is a reasonable development-time default for ReviewPilot's Lead Agent because the Lead Agent is bounded by a fixed stage machine, artifact checks, and explicit sub-agent contracts. OpenAI lists `gpt-5.4-mini` with a 400,000-token context window and 128,000 max output tokens, which is enough for ReviewPilot's intended Lead Agent pattern as long as the Lead Agent passes artifact summaries and file paths rather than full paper corpora.

The production target is different: during development, use `gpt-5.4-mini`; after the architecture is complete and stable, use `gpt-5.4` for the Lead Agent by default. The Lead Agent should not be treated as an unconstrained supreme planner. If it must perform hard recovery, complex scientific judgment, or final quality arbitration, it should escalate to `gpt-5.4` or `gpt-5.5` depending on the task.

OpenAI's model documentation describes `gpt-5.4-mini` as a fast, efficient model for high-volume workloads, with strong support for coding, computer use, subagents, tool use, function calling, structured outputs, web search, file search, MCP, and a 400k context window. This makes it a good fit for ReviewPilot's lead/sub-agent control plane, provided the system keeps stage order and artifact validation in code.

### Prototype Model Usage
The prototype does **not** assign one LLM model per agent. Its agent pipeline uses models only where LLM calls are actually made.

| Prototype component | Actual model behavior |
|---|---|
| `SearchConditionAgent` | No LLM model; uses deterministic concept extraction, query generation, human prompts, and provided config |
| `PromptAgent` | No LLM model in the agent pipeline; builds prompt templates from search conditions and user/provided fields |
| `CollectionAgent` | No LLM model; uses `AcademicSearcher` and platform APIs/searchers |
| `FilteringAgent` | Uses an LLM for relevance checks; default model is `gpt-5-mini` |
| `DownloadAgent` | No LLM model in the pipeline agent; uses `utils/downloader.py` for URL/DOI/arXiv downloading |
| `ExtractionAgent` | Uses an LLM for PDF/text extraction; default model is `gpt-5-mini` |
| `PipelineCoordinator` | Accepts one `model` argument, default `gpt-5-mini`, and passes it only to `FilteringAgent` and `ExtractionAgent` |
| Prototype `config.py` | Default `MODEL = "gpt-5-mini"` for utility LLM calls outside the explicit coordinator model parameter |

The prototype also contains a more advanced `utils/pdf_downloader.py` with optional LLM publisher detection and web-search fallback, but the prototype `DownloadAgent` calls `utils/downloader.py`, not that advanced downloader. Therefore the prototype pipeline should be described as **LLM-backed for filtering and extraction only**.

### Target Model Assignment
| Agent | Default target model | Escalation model | Reason |
|---|---|---|---|
| Lead Agent | Development: `gpt-5.4-mini`; production: `gpt-5.4` | `gpt-5.5` | Owns dialogue, routing, artifact checks, and bounded planning; mini is acceptable during development, but production should use `gpt-5.4` once the architecture is stable |
| `SearchConditionAgent` | `gpt-5.4-mini` | `gpt-5.4` for difficult multidisciplinary query design | Search setup benefits from semantic query expansion and domain-sensitive platform choice |
| `PromptAgent` | `gpt-5.4-mini` | `gpt-5.4` for complex extraction schemas or difficult inclusion criteria | Prompt generation is structured, but quality affects downstream screening and extraction |
| `CollectionAgent` | `gpt-5.4-mini` assigned, but no LLM call in the default collection path | Route query repair back to Lead/SearchCondition | Core collection should remain deterministic API/tool execution; the assigned model is only for optional LLM-assisted query repair, metadata normalization, or source-specific recovery |
| `FilteringAgent` | `gpt-5.4-mini` | `gpt-5.4` for borderline screening audits | False exclusions are expensive; avoid `nano` for final include/exclude decisions unless evals prove it is safe |
| `DownloadAgent` | `gpt-5.4-mini` assigned, but no LLM call in the default download path | `gpt-5.4` for difficult subscribed-paper recovery | Downloading is mostly deterministic; the assigned model is for optional PDF URL discovery and subscribed-paper fallback extraction with strict source validation |
| `ExtractionAgent` | `gpt-5.4-mini` | `gpt-5.4` for noisy PDFs, long papers, or high-value extraction | Extraction is the most information-dense stage; use mini for throughput and escalate when confidence is low |

All six Sub Agents must have an explicit model assignment of `gpt-5.4-mini`. For deterministic agents such as `CollectionAgent` and `DownloadAgent`, this does not mean every run must call an LLM. It means any LLM-assisted branch inside that Sub Agent uses `gpt-5.4-mini` unless an escalation rule says otherwise.

### CollectionAgent LLM Policy
It is reasonable for `CollectionAgent` to avoid LLM calls in its default path. Academic collection should be reproducible: given search terms, platforms, date range, and limits, it should call deterministic search APIs/scrapers and write raw records. LLMs should not silently rewrite queries or alter platform results inside collection.

LLM use belongs either before collection or in explicitly named recovery branches:

| Situation | Correct owner |
|---|---|
| Generate or revise Boolean query | `SearchConditionAgent` |
| Explain why collection returned too few/too many records | Lead Agent with `CollectionAgent` summary |
| Retry with a revised search strategy | Lead Agent routes back to `SearchConditionAgent`, then re-runs `CollectionAgent` |
| Normalize odd metadata from collected records | Optional `CollectionAgent` LLM-assisted branch using `gpt-5.4-mini`, with original raw metadata preserved |

### Implementation Notes
The active codebase should not hard-code model strings in scattered locations. Add a central model policy, for example `reviewpilot_core/model_policy.py`, with named roles:

```python
LEAD_AGENT_DEV_MODEL = "gpt-5.4-mini"
LEAD_AGENT_PRODUCTION_MODEL = "gpt-5.4"
SEARCH_CONDITION_MODEL = "gpt-5.4-mini"
PROMPT_MODEL = "gpt-5.4-mini"
COLLECTION_MODEL = "gpt-5.4-mini"
FILTERING_MODEL = "gpt-5.4-mini"
DOWNLOAD_MODEL = "gpt-5.4-mini"
EXTRACTION_MODEL = "gpt-5.4-mini"
CATEGORIZATION_MODEL = "gpt-5.4-mini"
SUBSCRIBED_PAPER_FALLBACK_MODEL = "gpt-5.4-mini"
ESCALATION_MODEL = "gpt-5.4"
HARD_REASONING_ESCALATION_MODEL = "gpt-5.5"
```

The active repo should keep `config.MODEL`, web project defaults, and `reviewpilot_core/model_policy.py` aligned on `gpt-5.4-mini` during development. Add pricing entries for `gpt-5.4-mini` and `gpt-5.4` in `utils/llm.py` before relying on production cost estimates.

## Subscribed-Paper Fallback Extraction
When `DownloadAgent` cannot download a PDF because the paper is subscribed, paywalled, or otherwise unavailable, ReviewPilot should not stop extraction for that paper. It should record the failed full-text retrieval, then invoke an extraction fallback using web-grounded sources.

This fallback belongs primarily to `ExtractionAgent`, not `DownloadAgent`. `DownloadAgent` should determine and persist retrieval status. `ExtractionAgent` owns schema-based information extraction, so it should decide how to extract from available public metadata, abstracts, publisher pages, preprints, repository mirrors, and other web-visible sources. The Lead Agent coordinates this transition.

The subscribed-paper fallback flow is:

```text
DownloadAgent
  -> marks paper as unavailable/subscribed in download_report.json and included_papers.jsonl
LeadAgent
  -> detects unavailable papers before extraction
ExtractionAgent
  -> uses the existing extraction schema
  -> calls OpenAI Responses API with the hosted web_search tool
  -> returns structured extraction with source URLs and confidence fields
LeadAgent
  -> verifies citations/sources exist
  -> marks extraction_source = "web_search_fallback"
```

Use OpenAI's Responses API hosted web search tool for this path. For new API integrations, use `{"type": "web_search"}` rather than legacy `web_search_preview`. When a search must happen, set `tool_choice` to `"required"` or to a specific web-search tool choice; with `"auto"`, the model may choose not to search.

The API shape should follow this pattern:

```python
from openai import OpenAI
from pydantic import BaseModel

client = OpenAI()

class ExtractedPaperFields(BaseModel):
    extraction_source: str
    source_urls: list[str]
    confidence: str
    # Dynamically generated fields from ExtractionAgent schema go here.

response = client.responses.parse(
    model="gpt-5.4-mini",
    tools=[
        {
            "type": "web_search",
            "filters": {
                "allowed_domains": [
                    "pubmed.ncbi.nlm.nih.gov",
                    "pmc.ncbi.nlm.nih.gov",
                    "arxiv.org",
                    "openalex.org",
                    "semanticscholar.org",
                    "acm.org",
                    "ieee.org",
                    "springer.com",
                    "nature.com",
                    "sciencedirect.com",
                "wiley.com",
                ]
            },
        }
    ],
    tool_choice="required",
    include=["web_search_call.action.sources"],
    input=[
        {
            "role": "system",
            "content": "Extract only information supported by cited web sources. If a field is not supported, return an empty value and low confidence.",
        },
        {
            "role": "user",
            "content": "Use the provided extraction schema to extract fields for this subscribed paper: <paper metadata + schema here>",
        },
    ],
    text_format=ExtractedPaperFields,
)

extracted = response.output_parsed
```

This fallback must never pretend to be full-text extraction. It must set `extraction_source` to `web_search_fallback`, store searched/cited URLs, and mark unsupported fields as empty or low confidence. The web-search context window is limited to 128k even when the underlying model context window is larger, so this path should be used for targeted evidence gathering, not full-paper reconstruction.

## Internal Seven-Stage Evidence Pipeline
| Stage | Owner | Primary Input | Primary Artifact |
|---|---|---|---|
| 1. `search_conditions` | `SearchConditionAgent` | User research topic and setup preferences | `search_conditions.json` |
| 2. `prompt_relevance` | `PromptAgent.generate_relevance_prompt()` | `search_conditions.json` | `prompts/relevance_prompt.json` |
| 3. `collection` | `CollectionAgent` | `search_conditions.json` | `collected/*.jsonl`, `collected/summary.json` |
| 4. `filtering` | `FilteringAgent` | collected records, relevance prompt, date range | `filtered/included_papers.jsonl`, `filtered/screening_stats.json` |
| 5. `prompt_extraction` | `PromptAgent.generate_extraction_prompt()` | search conditions, included papers, relevance criteria | `prompts/extraction_prompt.json` |
| 6. `download` | `DownloadAgent` | included papers | `pdfs/`, `pdfs/download_report.json` |
| 7. `extraction` | `ExtractionAgent` | PDFs, included papers, extraction prompt/schema | `extraction/extraction_results.jsonl` |

This pipeline produces the evidence base. It is not the full user-facing product workflow.

## User-Facing Five-Step Workflow
| Step | User-visible name | Internal stage coverage | User-facing outcome |
|---:|---|---|---|
| 1 | Search Setup | `search_conditions`, `prompt_relevance`, `collection` entry point | Search strategy, sources, date range, keywords, and collection readiness |
| 2 | Paper Screening | `filtering` | Included/excluded paper set with PRISMA-style counts and relevance rationale |
| 3 | Full-Text Retrieval | `download` | PDF retrieval status, subscribed/paywalled status, and fallback eligibility |
| 4 | Information Extraction | `prompt_extraction`, `extraction` | Evidence matrix from included papers using the approved extraction schema |
| 5 | Categorization & Analysis | Lead-owned result capability after `extraction` | Semantic category mapping, categorized results, group counts, and final review workspace |

The default path after extraction is **not complete**. The Lead Agent should move the user into Step 5 and recommend a field to categorize. The user may explicitly skip categorization, but skipping is a visible product decision rather than the hidden default.

## Final Result Presentation
ReviewPilot's final output should be a result workspace, not a raw extraction file. The canvas should present:

1. **Review Overview:** identified records, screened records, included papers, retrieved PDFs, unavailable/subscribed papers, extracted papers, categorized papers, sources, date range, and search strategy.
2. **Evidence Matrix:** one row per paper with metadata, screening decision/rationale, retrieval status, extraction source, extracted fields, missing values, and source URLs for web-search fallback extraction.
3. **Categorization & Analysis:** selected field, category mode, editable category names/descriptions, category counts, papers per category, and representative extracted evidence.
4. **Export Package:** `search_conditions.json`, `prompts/relevance_prompt.json`, `filtered/included_papers.jsonl`, `pdfs/download_report.json`, `extraction/extraction_results.jsonl`, `categorization/categorization_mapping.json`, and `categorization/categorized_results.jsonl`.

The final completion state is reached only after categorization artifacts exist or the user explicitly records a skip decision.

## Lead Agent Contract
The Lead Agent must expose a small, stable interface for the web app:

```python
LeadAgent.handle_message(project_id: str | None, message: str, action: str | None = None) -> LeadAgentResult
```

The result should be structured, not just text:

```json
{
  "reply": "Human-readable assistant response",
  "stage": "collection",
  "status": "waiting_user | running | completed | failed",
  "next_actions": ["run_collection", "edit_search"],
  "task_id": "optional async task id",
  "artifacts": ["output/project/collected/summary.json"]
}
```

The frontend consumes this result and re-renders from persisted project state. It must not infer hidden pipeline state from chat text.

## Lead Agent Responsibilities
1. **Conversation ownership:** Interpret user intent, ask concise follow-up questions, explain results, and keep the user oriented.
2. **Stage routing:** Determine the true current stage from project artifacts, not from frontend state alone.
3. **Delegation:** Call the correct Sub Agent with explicit inputs, output paths, task boundaries, and success criteria.
4. **Verification:** After every Sub Agent run, verify that required artifacts exist and have minimally valid structure before advancing.
5. **Checkpointing:** Persist lead-level decisions, current stage, user approvals, and failure summaries.
6. **Recovery:** If a stage fails or an artifact is missing, stop at that stage, report the concrete blocker, and offer the narrowest recovery action.

## Bounded Autonomy Rules
The Lead Agent may choose how to answer the user and which allowed action to take next, but it may not freely reorder the pipeline. The valid forward order for the internal evidence pipeline is:

```text
search_conditions -> prompt_relevance -> collection -> filtering -> prompt_extraction -> download -> extraction
```

The valid forward order for the user-facing workflow continues one step further:

```text
Search Setup -> Paper Screening -> Full-Text Retrieval -> Information Extraction -> Categorization & Analysis
```

Allowed regressions are explicit and artifact-driven:

| User or system event | Regression target |
|---|---|
| User changes research topic, platforms, query, date range, or source limits | `search_conditions` |
| User changes inclusion or exclusion criteria | `prompt_relevance` |
| User requests recollection after changing search setup | `collection` after regenerated upstream artifacts |
| User changes extraction fields or schema expectations | `prompt_extraction` |
| Required artifact is absent, invalid, or stale | Earliest stage that can regenerate it |

## Artifact-First Context Design
Sub Agents should write durable artifacts directly to the project folder. The Lead Agent should pass file paths, compact summaries, and validation results rather than copying full paper sets through chat context. This keeps context small and avoids lossy lead-agent paraphrasing.

Each stage should have a minimal artifact contract:

| Stage | Required validation |
|---|---|
| `search_conditions` | JSON exists; includes project name, query/search terms, platforms, date range, result limits |
| `prompt_relevance` | JSON exists; includes task/instruction or equivalent relevance criteria |
| `collection` | summary exists; platform counts are present; collected JSONL files are readable |
| `filtering` | included/excluded outputs exist; screening stats match readable row counts |
| `prompt_extraction` | prompt/schema exists; extraction fields are non-empty |
| `download` | download report exists; included papers are updated with PDF status when available |
| `extraction` | extraction results JSONL exists; rows are readable and tied back to included papers |
| `categorization` | categorized results JSONL and category mapping JSON exist; categories have readable counts |

## Web App Integration Setting
The new web app should call the Lead Agent for all user-facing assistant interactions and stage actions:

```text
frontend chat/action
  -> web_app LeadAgent endpoint
    -> LeadAgent
      -> selected Sub Agent
      -> artifact verification
    -> structured LeadAgentResult
  -> frontend state refresh
```

`reviewpilot_core.workflow_actions` has been removed from the active web app architecture. The web app routes user-facing actions through `LeadAgent -> WorkflowActionAdapter -> explicit SubAgentContract`. Shared behavior that remains useful should live inside the owning Sub Agent or a narrow artifact utility, not in a monolithic workflow helper.

## Migration Order
1. Add `LeadAgent` with artifact-based stage detection and structured responses.
2. Route Search Setup through `LeadAgent -> SearchConditionAgent`.
3. Route relevance prompt generation through `LeadAgent -> PromptAgent`.
4. Route collection through `LeadAgent -> CollectionAgent`.
5. Route screening through `LeadAgent -> FilteringAgent`.
6. Route extraction prompt generation through `LeadAgent -> PromptAgent`.
7. Route PDF retrieval through `LeadAgent -> DownloadAgent`.
8. Route extraction through `LeadAgent -> ExtractionAgent`.
9. Replace frontend assistant message generation with Lead Agent responses.
10. Restore Categorization & Analysis as Step 5 in the user-facing workflow and route the canvas intent through the Lead Agent.
11. Move categorization implementation behind an explicit Lead-owned result contract so `LeadAgent` does not call monolithic workflow helpers directly.
12. Present final results through the canvas as overview, evidence matrix, categorization analysis, and export package.

## Non-Goals
- Do not introduce a seventh Sub Agent merely because the pipeline has seven stages.
- Do not let the Lead Agent bypass artifact validation.
- Do not let frontend UI state become the source of truth for pipeline stage.
- Do not merge categorization into `ExtractionAgent`; categorization is semantic analysis over extracted results, not field extraction.
- Do not hide categorization as an optional side feature when the user has completed extraction.
- Do not replace specialized Sub Agent internals with a monolithic Lead Agent prompt.

## References
- Anthropic, ["Building effective agents"](https://www.anthropic.com/engineering/building-effective-agents): workflows vs agents, orchestrator-workers, and the warning to keep agentic systems as simple as possible until complexity is justified.
- Anthropic, ["How we built our multi-agent research system"](https://www.anthropic.com/engineering/multi-agent-research-system): lead agent plus subagents, parallel research, isolated contexts, task delegation, and artifact-oriented subagent outputs.
- Anthropic, ["Effective context engineering for AI agents"](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents): sub-agent architectures as a way to separate concerns and keep the lead agent focused on synthesis.
- Anthropic, ["Building agents with the Claude Agent SDK"](https://claude.com/blog/building-agents-with-the-claude-agent-sdk): agent loop, subagent support, isolated context windows, and gather-context/take-action/verify cycles.
- OpenAI, ["GPT-5.4 mini model documentation"](https://developers.openai.com/api/docs/models/gpt-5.4-mini): canonical model ID, context window, tool support, structured outputs, and pricing.
- OpenAI, ["Models"](https://developers.openai.com/api/docs/models): model-selection guidance for using GPT-5.5 on complex reasoning/coding and GPT-5.4 mini/nano for lower-latency, lower-cost workloads.
- OpenAI, ["Introducing GPT-5.4 mini and nano"](https://openai.com/index/introducing-gpt-5-4-mini-and-nano/): mini/nano positioning for coding workflows, subagents, tool use, and cost/performance tradeoffs.
- OpenAI, ["Web search"](https://developers.openai.com/api/docs/guides/tools-web-search): Responses API hosted `web_search` tool, domain filters, source inclusion, required tool choice, and the 128k search-context limit.
- OpenAI, ["Structured model outputs"](https://developers.openai.com/api/docs/guides/structured-outputs): Responses API `responses.parse`, Pydantic parsing, and schema-constrained JSON extraction.

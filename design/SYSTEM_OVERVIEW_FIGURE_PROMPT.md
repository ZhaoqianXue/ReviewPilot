# Prompt for Claude Design — ReviewPilot System Overview Figure (Nature Communications)

## Task
Create ONE publication-quality vector figure (editable SVG; also export a PNG preview) titled **"ReviewPilot system overview"** for a Nature Communications manuscript. It must be a **single integrated diagram — no separate panels** — that simultaneously shows:

1. The multi-agent structure: **1 Lead Agent + 6 specialized Sub Agents** (plus one analysis capability owned by the Lead Agent itself).
2. The **complete workflow**: the full sequence of steps every agent performs, from the researcher's question to the final categorized evidence report.

## Audience and language — CRITICAL
Nature Communications is a multidisciplinary journal: reviewers may come from biology, medicine, or any natural-science field, and most are not software engineers. All figure text must read like the methodology of a systematic literature review, not like a software diagram.

- **Never use these words in the figure:** artifact, JSON/JSONL, API, UI/frontend/backend/web app, pipeline, adapter, contract, schema, orchestration layer, deterministic, cache, endpoint, file paths or file names of any kind.
- **Use plain review-methodology language instead:** "shared project memory" (not artifact store), "chat assistant / conversation" (not Web UI), "search strategy" (not search_conditions.json), "screening criteria" (not relevance prompt), "extraction form" (not extraction schema), "database search" (not API call), "saved to memory" (not writes artifacts).
- Spell out "large language model (LLM)" once; after that "LLM" is fine.
- Frame the workflow as mirroring a human-conducted systematic review (identification → screening → full-text retrieval → data extraction → synthesis). Reviewers who know PRISMA should recognize the logic instantly.

Ground the facts in this repository (`design/LEAD_AGENT_ARCHITECTURE.md`, `design/AGENT_SKILL_ARCHITECTURE.md`, `design/AGENT_MEMORY_ARCHITECTURE.md`, `agents/`, `reviewpilot_core/`), but **translate everything into the plain language above** — code names and file names must never appear in the figure. The current figure remains an Agent-and-workflow overview: do not turn the four shared Skills or either Agent Memory layer into additional Agent cards or workflow stages.

## Canvas
Nature double-column figure: 183 mm wide, ~120–140 mm tall, white background, single integrated composition.

## Composition (one integrated diagram)

**Researcher (far left):** a person icon labeled "Researcher". Two-way arrow to the Lead Agent labeled "conversation (chat)". Small markers along the workflow show where the researcher approves or edits (see checkpoints below).

**Lead Agent (top center):** one prominent box labeled "Lead Agent". Short caption: "talks with the researcher, assigns each task to a specialist agent, checks every result before moving to the next step". Downward supervision arrows from the Lead Agent to each of the six Sub Agents (thin, uniform — these show hierarchy). Annotate near the box: "follows the fixed review sequence; returns to an earlier step only when the researcher changes a decision".

**Six Sub Agents (a horizontal row below the Lead Agent, arranged left → right in workflow order).** Each card: plain-English name, one-line role, and a small tag "LLM reasoning" or "database retrieval". The workflow itself is drawn as a bold numbered flow line running left → right through the row: numbered circles ①–⑦ mark the order in which tasks happen. The Prompt Agent carries two numbers (② and ⑤) because it is used twice — this is how one row of 6 agents shows a 7-step workflow. Route the flow line accordingly: ①→②→③→④, then a short, clean loop back to the Prompt Agent card for ⑤, then forward to ⑥→⑦. This loop-back is intentional — do NOT split the Prompt Agent into two cards to straighten the line.

| Order | Agent card | Role text on card | What it produces (label on the flow line) | Tag |
|---|---|---|---|---|
| ① | Search Strategy Agent | turns the research question into search terms, databases, and time range | search strategy | LLM reasoning |
| ② | Prompt Agent (1st use) | writes the inclusion/exclusion screening criteria | screening criteria | LLM reasoning |
| ③ | Literature Collection Agent | searches academic databases (PubMed, OpenAlex, arXiv, and others) | candidate papers | database retrieval |
| ④ | Screening Agent | removes duplicates, then screens each paper against the criteria | included papers, with reasons | LLM reasoning |
| ⑤ | Prompt Agent (2nd use) | designs the data-extraction form (which fields to extract) | extraction form | LLM reasoning |
| ⑥ | Full-Text Retrieval Agent | fetches full-text PDFs; records which papers are paywalled or unavailable | full texts + availability record | database retrieval |
| ⑦ | Information Extraction Agent | fills the extraction form from each full text; for unavailable papers, gathers evidence from trusted web sources instead (marked with source links and confidence) | extracted evidence | LLM reasoning |

**Web-evidence fallback:** a short labeled branch arrow from ⑥ ("paywalled / unavailable papers") into ⑦, labeled "evidence gathered from trusted web sources". Draw it as a branch inside step ⑦ — it is NOT an extra workflow step.

**Categorization & Analysis (right end, after ⑦):** a block visually attached to the Lead Agent (same color, dashed border), labeled "Categorization & Analysis — performed by the Lead Agent". Caption: "groups the extracted evidence into themes; the researcher reviews and edits the categories". Flow line ends in a final output box: "Interactive review report — study counts at each step, evidence table, thematic categories" with an arrow back to the Researcher.

**Shared project memory (bottom strip, full width):** a single horizontal band labeled "Shared project memory". Short dashed connectors from every Sub Agent card and from the Lead Agent down to this band. Caption: "every agent saves its results here; the Lead Agent verifies each saved result before the workflow advances".

In this figure, "Shared project memory" is the plain-language label for the current project's authoritative saved review record. It is not the Lead Agent's session memory or the cross-project memory defined in `AGENT_MEMORY_ARCHITECTURE.md`. Do not merge those concepts. A later figure revision may add cross-project memory as a separate Lead-only context source after the runtime is implemented.

**Researcher checkpoints:** small person-icon markers on the flow line at: ① approve search strategy · ④ review screening result · Categorization: review/edit themes. Legend entry: "researcher approval point".

**Revision arrows (thin, distinct color, curving back above the flow line, max 2 to avoid clutter):** "researcher revises the question → back to ①"; "researcher revises criteria → back to ②". Label the pair "researcher-driven revision".

## Style (Nature conventions)
- Sans-serif (Helvetica/Arial); minimum ~5–6 pt at print size; sentence case; all text in English.
- Colorblind-safe palette (Okabe–Ito). Suggested roles: Lead Agent (and its Categorization block) = one saturated hue; the six Sub Agents = one shared second hue; memory band = neutral gray; researcher/approval markers = a third hue; revision + fallback arrows = vermillion. Flat fills only — no gradients, shadows, 3D, or clipart.
- Legend (compact, bottom corner): supervision arrow · workflow flow line ①–⑦ · save-to-memory connector · researcher approval point · revision arrow · "LLM reasoning" vs "database retrieval" tag.
- Deliverables: (1) editable SVG, (2) PNG preview, (3) a draft figure legend (~120 words) in the same plain, non-engineering language.

## Accuracy checklist (verify before finishing)
- Exactly **6** Sub-Agent cards; the Prompt Agent appears **once** as a card but carries **two** sequence numbers (② and ⑤).
- No Skill is drawn as an additional Agent or workflow stage; any future Skill annotation must preserve the four shared capabilities and assignments defined in `AGENT_SKILL_ARCHITECTURE.md`.
- Workflow order is exactly ①→⑦ as in the table; Categorization & Analysis comes after ⑦ and belongs to the Lead Agent — never drawn as a 7th Sub Agent.
- The web-evidence fallback is a branch within step ⑦, not a separate step.
- Not a single banned engineering term or file name appears anywhere in the figure.
- Structure (hierarchy: Lead → Sub Agents → shared memory) and workflow (numbered flow line) are both legible in the same single diagram.

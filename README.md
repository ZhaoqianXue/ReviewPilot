# ReviewPilot

ReviewPilot is a multi-agent research assistant for systematic reviews. It helps researchers search for papers, screen them, retrieve full text, extract evidence, and organize the results for analysis.

The work is divided among six specialized Sub Agents. A Lead Agent coordinates their work, checks the result of each stage, and keeps the researcher in control of the review.

## How a Review Works

ReviewPilot guides the researcher through five steps:

1. **Search Setup** — Describe the research question and confirm the keywords, academic sources, date range, and number of results.
2. **Paper Screening** — Confirm the inclusion and exclusion criteria, then review which papers are included or excluded.
3. **Full-Text Retrieval** — Retrieve PDFs for the included papers and record which papers are unavailable or require another source.
4. **Information Extraction** — Decide which information should be collected from each paper, then produce a structured evidence table.
5. **Categorization & Analysis** — Group the extracted evidence into meaningful categories and review the summarized results.

The final project contains the search strategy, collected papers, screening decisions, full-text retrieval report, extracted evidence, and categorized results.

## Lead Agent and Six Sub Agents

### Overall Structure

```text
                         Lead Agent
              Coordinates, assigns, checks, summarizes
                              │
       ┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐
       │          │          │          │          │          │
     Search      Task       Paper      Paper     Full-Text  Information
     Setup    Preparation  Collection  Screening  Retrieval  Extraction
     Agent       Agent       Agent       Agent       Agent       Agent
```

### Lead Agent

The Lead Agent is responsible for the review as a whole. It:

- Understands the researcher's request.
- Determines the current stage of the review.
- Assigns work to the appropriate Sub Agent.
- Checks each result before the review moves forward.
- Explains what has been completed and what the researcher can do next.
- Organizes and presents the final results.

### Six Sub Agents

| Agent | Responsibility |
|---|---|
| `SearchConditionAgent` | Turns the research question into keywords, academic sources, date ranges, and other search settings. |
| `PromptAgent` | Prepares the instructions used for paper screening and information extraction. |
| `CollectionAgent` | Collects papers from the selected academic sources. |
| `FilteringAgent` | Removes duplicates and decides whether each paper matches the review criteria. |
| `DownloadAgent` | Retrieves PDFs and records papers whose full text could not be obtained. |
| `ExtractionAgent` | Extracts the required information from each paper using the approved fields. |

### How the Agents Work Together

```text
SearchConditionAgent
        ↓
PromptAgent prepares the screening criteria
        ↓
CollectionAgent
        ↓
FilteringAgent
        ↓
PromptAgent prepares the extraction requirements
        ↓
DownloadAgent
        ↓
ExtractionAgent
        ↓
Lead Agent categorizes, summarizes, and presents the results
```

`PromptAgent` works at two different stages but remains one Sub Agent. Categorization and analysis are handled by the Lead Agent. ReviewPilot therefore has one Lead Agent and six Sub Agents.

## Agent Skills

An Agent Skill is a reusable professional method that helps an Agent complete a particular type of review task consistently.

| Skill | Purpose | Used by |
|---|---|---|
| `systematic-review-search-strategy` | Designs and revises systematic-review search strategies. | `SearchConditionAgent` |
| `evidence-screening` | Defines screening criteria and supports decisions about paper relevance. | `PromptAgent`, `FilteringAgent` |
| `structured-evidence-extraction` | Designs extraction fields and guides evidence extraction. | `PromptAgent`, `ExtractionAgent` |
| `evidence-synthesis-and-categorization` | Organizes extracted evidence into meaningful categories. | Lead Agent |

ReviewPilot automatically selects the Skill required for the current task. It also records the Skill name and version so collaborators can trace which method was used.

## Agent Memory

ReviewPilot uses two forms of memory:

- **Current-project memory** allows the Lead Agent to remember earlier conversations, decisions, and corrections within the same review project.
- **Cross-project memory** allows confirmed search strategies, screening settings, extraction fields, and categorization settings from earlier projects to be reused as references.

The researcher's current request and the confirmed results of the current project always take priority over previous memory. The Lead Agent manages memory and only shares information that is relevant to a Sub Agent's current task.

Current-project memory uses the files already saved under `output/<project>/`: conversation history in `chat/messages.jsonl`, eligibility rules and their draft/finalized status in `prompts/relevance_prompt.json`, and the saved extraction schema, category settings, and workflow state. The Lead Agent reads these files again on each conversation, including after reopening the app. No embedding service or separate project-memory database is required. Optional cross-project presets remain in the local `output/.agent_memory/memory.sqlite3` database; turning them off or clearing them preserves project history and settings.

In Step 2, edit Inclusion Criteria and Exclusion Criteria directly (one rule per line), or refine them in chat. **Save Draft** persists changes; **Finalize Criteria** confirms them and enables **Run screening**. Editing a saved rule requires confirmation again and marks existing screening and downstream outputs as stale. Existing projects without a recorded criteria confirmation must review and finalize their criteria before screening again.

## Installation and Startup

Run the following commands from the ReviewPilot project folder.

### Apple Silicon

Create the development environment:

```bash
PYTHON_BIN=python3 scripts/bootstrap_native_env.sh
file .venv-native/bin/python
.venv-native/bin/python -c 'import platform; print(platform.machine())'
```

Both checks should report `arm64`.

Start ReviewPilot:

```bash
.venv-native/bin/uvicorn web_app:app --host 127.0.0.1 --port 5602 --reload
```

### Other Platforms

Create a virtual environment and install the required packages:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

Start ReviewPilot:

```bash
.venv/bin/uvicorn web_app:app --host 127.0.0.1 --port 5602 --reload
```

### API Configuration

Create a local `config.py` file if one does not already exist:

```python
EMAIL = "your-email@example.com"
PUBMED_API_KEY = None
SCOPUS_API_KEY = None
MODEL = "gpt-5.4-mini"
```

Create a local `secrets.txt` file and add the model providers you use:

```text
openai_key, sk-your-openai-key
claude_key, sk-ant-your-anthropic-key
```

`PUBMED_API_KEY` is optional. `SCOPUS_API_KEY` is required only when Scopus is selected. At least one supported model-provider key is required for tasks that use a language model.

After starting ReviewPilot, open:

```text
http://127.0.0.1:5602
```

## Usage

1. Open ReviewPilot in a browser.
2. Create a new review project.
3. Enter the research question and confirm the Search Setup.
4. Collect candidate papers and run Paper Screening.
5. Run Full-Text Retrieval for the included papers.
6. Review and confirm the fields for Information Extraction.
7. Run Information Extraction.
8. Categorize and summarize the extracted evidence.
9. Review and export the final results.

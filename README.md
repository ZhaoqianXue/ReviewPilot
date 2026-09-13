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

ReviewPilot keeps project memory on this computer and offers explicit reuse of confirmed configurations from other local projects.

- **Conversation history:** The sidebar keeps three protected Examples separate from ordinary Chats, ordered by recent activity. New Review opens a clean draft; the first submitted topic creates a saved project. Chats can be searched, renamed, and deleted from their `…` menu. Names are stored in `output/<project>/session.json` without changing research configuration. Deleting a chat moves its full directory to `output/.trash/<project>-<token>`; to recover it with the app stopped, move that directory back to `output/<project>` only if the destination is absent. Running workflow tasks block deletion/rename. Per-conversation unsent text is kept in browser session storage, and saved chat/configuration reloads from local files.
- **Current-project memory:** Full conversation history remains in `output/<project>/chat/messages.jsonl`. Saved project artifacts are authoritative; `memory/confirmed_decisions.json` preserves the last confirmed screening criteria and extraction fields while a new draft is being edited. Current configurations take precedence over earlier chat suggestions or superseded instructions. Chat edits remain drafts; screening criteria and extraction fields are confirmed through their canvas actions. Extraction confirmation is bound to the exact schema content. Reopening the app reads the saved state again without summarizing or trimming chat history.
- **Explicit configuration reuse:** Open **Settings → Reuse project configuration**, choose a source project and configuration, inspect the complete preview, then select **Import as draft**. Search setup, inclusion/exclusion criteria, extraction fields, and categories are supported. Imported settings must pass their normal review/confirmation steps before execution; they never silently replace confirmed decisions. Changes to either project after preview require a new preview. Importing a schema or categories requires the prerequisite stage and compatible fields in the target project.

Automatic cross-project retrieval and promotion are disabled, including for older installations whose Memory toggle was enabled. The legacy SQLite store is retained on disk for compatibility but is not queried by the Lead Agent. Configuration reuse reads confirmed local project artifacts only after the user opens the picker. No embedding service or background memory generation is required.

In Step 2, confirmed criteria are locked. Click **Edit Criteria** to open a draft, edit directly or refine in chat, then **Finalize Criteria** before running screening. Draft edits preserve the previous confirmed decision and mark affected outputs as stale. Imported search setup is saved locally as a draft and must be reviewed and saved in Search Setup before collection.

### Review decisions and evidence

- **Step 2 → Review screened papers:** Search or filter included/excluded records, inspect the full abstract, and review the saved rationale and decisive criterion. Save an inclusion decision with an approved criterion and a reason. Changing inclusion updates the paper lists and counts, and marks retrieval, extraction, and categorization as needing a rerun. Older runs explicitly show when no rationale was saved.
- **Step 4 → Evidence & corrections:** Select a field to inspect its value, source, PDF text, and verified quotation/page when available. Correct the value and record a reason; the original value remains in local review history. A quoted passage is located by matching the actual PDF text, not by trusting a model-supplied page number. Web fallback links and unavailable evidence are labeled separately. Corrections invalidate categorization.
- **Examples:** The three sidebar examples are read-only snapshots. **Try this example — create my copy** creates an independent local project with its own PDFs, configuration, results, and review history. Browsing a snapshot requires no model API calls.
- **Live samples:** In your own project, select one to five papers in Step 2 or Step 4 and choose **Run live sample**. Only those papers are computed, using the configured model API; the result is displayed separately with its run time. It does not replace saved full results or automatically adopt suggested changes.

Reviews are stored in `review/changes.json`, also available as **Human review history** in the export package. A revision check rejects stale edits, and a local write journal recovers interrupted saves. A full stage rerun replaces that stage's working results; the review history remains available. Missing quotations in older outputs are not retroactively invented.

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

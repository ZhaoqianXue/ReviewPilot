# ReviewPilot

A local monolith web app for searching, filtering, downloading, extracting, and categorizing academic papers across multiple platforms.

## Features

- **Multi-platform search**: PubMed, OpenAlex, arXiv, Scopus, Web of Science, Google Scholar, CS Conferences
- **New frontend web app**: Starlette serves the optimized ReviewPilot frontend and calls the Python pipeline directly
- **Human-in-the-loop**: Project configuration and workflow actions are exposed through the web app
- **LLM-powered filtering**: Relevance checking using GPT-5/Claude/Gemini
- **Automated extraction**: Extract structured information from PDFs
- **Resume capability**: Continue from any pipeline stage
- **Real-time output**: JSONL streaming for large datasets

## Project Structure

```
ReviewPilot/
├── web_app.py                  # Starlette monolith entrypoint
├── reviewpilot_core/           # Project state projection and workflow actions
├── frontend/                   # Optimized zero-build frontend
├── main.py                     # Academic search adapter used by workflow actions
├── requirements.txt
├── README.md
│
├── agents/                     # Pipeline agents retained for backend workflows
│   ├── base_agent.py
│   ├── coordinator.py          # Pipeline orchestrator
│   ├── search_condition_agent.py   # Asks user for search params
│   ├── prompt_agent.py         # Generates LLM prompts
│   ├── collection_agent.py     # Searches platforms
│   ├── filtering_agent.py      # Dedup + relevance check
│   ├── download_agent.py       # Downloads PDFs
│   └── extraction_agent.py     # Extracts info from PDFs
│
├── searchers/                  # Platform modules
│   ├── pubmed.py
│   ├── openalex.py
│   ├── arxiv_search.py
│   ├── scopus.py
│   ├── wos.py
│   ├── google_scholar.py
│   └── dblp.py
│
└── utils/
    ├── llm.py                  # LLM API interface
    ├── pdf_downloader.py       # PDF downloader
    ├── jsonl_handler.py
    └── human_interaction.py
```

> **Local-only note:** `agent_skill/` is a private scratch directory for a minor collaborator task. It is not part of the `agents/` system, not a project skill, and is intentionally ignored by Git.

## Quick Start

### 1. Install dependencies

On Apple Silicon, bootstrap a native ARM64 development environment:

```bash
PYTHON_BIN=python3 scripts/bootstrap_native_env.sh
file .venv-native/bin/python
.venv-native/bin/python -c 'import platform; print(platform.machine())'
```

Both architecture checks should report `arm64`. The bootstrap refuses to create the environment when the selected Python interpreter is not ARM64.

On other platforms, use a conventional virtual environment and install the development requirements:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

### 2. Configure API keys

```bash
# Create local config.py and secrets.txt if they are not already present.
# These files are ignored by Git.
```

### 3. Run the web app

On Apple Silicon:

```bash
.venv-native/bin/uvicorn web_app:app --host 127.0.0.1 --port 5602 --reload
```

On other platforms:

```bash
.venv/bin/uvicorn web_app:app --host 127.0.0.1 --port 5602 --reload
```

Open http://127.0.0.1:5602, create a review project, then run collection, screening, PDF download, schema generation, extraction, and categorization from the optimized frontend.
With `--reload`, Python backend edits restart the local server automatically.
Frontend static edits under `frontend/` are picked up by a browser refresh.

The old Streamlit and CLI entrypoints are kept only in `ReviewPilot_prototype/`
for historical comparison.

## Configuration

### config.py (API credentials only)

```python
EMAIL = "your-email@example.com"      # Required for PubMed/OpenAlex
PUBMED_API_KEY = None                 # Optional
SCOPUS_API_KEY = None                 # Optional
MODEL = "gpt-5.4-mini"                # Default development LLM
```

### secrets.txt (LLM API keys)

```
openai_key, sk-your-openai-key
claude_key, sk-ant-your-anthropic-key
```

## Usage

```bash
# Main web interface
.venv-native/bin/uvicorn web_app:app --host 127.0.0.1 --port 5602 --reload
```

## Pipeline Workflow

1. **Project Setup** → New frontend writes `output/{project}/search_conditions.json`
2. **Collection** → Searches configured platforms and saves JSONL outputs
3. **Screening** → Deduplicates and checks relevance with LLM criteria
4. **Full-Text Retrieval** → Downloads PDFs for included papers
5. **Information Extraction** → Generates a schema and extracts structured JSONL from PDFs or metadata fallback
6. **Categorization** → Groups extracted results with LLM-generated semantic categories for final analysis

## Output Structure

```
output/{project_name}/
├── search_conditions.json
├── prompts/
│   ├── relevance_prompt.json
│   └── extraction_prompt.json
├── collected/
│   ├── pubmed.jsonl
│   ├── arxiv.jsonl
│   └── summary.json
├── filtered/
│   └── filtered_papers.jsonl
├── pdfs/
│   └── download_report.json
├── extraction/
│   ├── extraction_schema.json
│   ├── extraction_prompt.json
│   └── extraction_results.jsonl
└── categorization/
    ├── categorization_mapping.json
    └── categorized_results.jsonl
```

## Local Prototype Snapshot

`ReviewPilot_prototype/` may exist in local workspaces as the original handoff snapshot from the project owner. It is for historical comparison, regression investigation, and recovery of pre-migration behavior only. It is intentionally ignored by Git, marked read-only locally, and must not be edited as the active codebase or pushed with product changes.

## Supported LLM Models

| Provider | Models |
|----------|--------|
| OpenAI | gpt-5.4-mini, gpt-5.4, gpt-5.5, gpt-5-mini, gpt-5.1, gpt-5.2, gpt-4.1, o3, o4-mini |
| Anthropic | claude-sonnet-4-5, claude-opus-4-5, claude-haiku-4-5 |
| Google | gemini-2.5-pro, gemini-2.5-flash, gemini-3-pro |
| Together | Llama-4, Qwen-2.5, QwQ-32B |

## License

MIT License

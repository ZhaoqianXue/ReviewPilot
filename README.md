# Data Scholar

A multi-agent system for searching, filtering, downloading, and extracting information from academic papers across multiple platforms.

## Features

- **Multi-platform search**: PubMed, OpenAlex, arXiv, Scopus, Web of Science, Google Scholar, CS Conferences
- **Human-in-the-loop**: Interactive prompts guide you through the entire workflow
- **LLM-powered filtering**: Relevance checking using GPT-5/Claude/Gemini
- **Automated extraction**: Extract structured information from PDFs
- **Resume capability**: Continue from any pipeline stage
- **Real-time output**: JSONL streaming for large datasets

## Project Structure

```
data-scholar/
├── cli.py                      # Main entry point
├── main.py                     # Legacy search interface
├── config.example.py           # Configuration template
├── secrets.example.txt         # API keys template
├── requirements.txt
├── README.md
│
├── agents/                     # Multi-agent system
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
    ├── downloader.py           # PDF downloader
    ├── jsonl_handler.py
    └── human_interaction.py
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp config.example.py config.py
cp secrets.example.txt secrets.txt

# Edit with your API keys
nano secrets.txt
nano config.py
```

### 3. Run

```bash
python3 chat.py
```

Describe your research → AI generates config → Give feedback → Run!

**Example:**
```
What are you researching? I am doing a survey of using LLM for rare disease

🤖 Generating configuration...

======================================================================
  Generated Search Configuration
======================================================================
📁 Project: llm-rare-disease
🔍 Search Query: (LLM OR "large language model") AND "rare disease"
📌 Topic: Large Language Models
🌍 Domain: rare disease
📚 Platforms: pubmed, arxiv, openalex
📅 Date: 2020-01-01 to present
======================================================================

💬 Feedback: add diagnosis to the query
🤖 Processing... [Updates configuration]

💬 Feedback: yes
🚀 Starting pipeline...
```

## Configuration

### config.py (API credentials only)

```python
EMAIL = "your-email@example.com"      # Required for PubMed/OpenAlex
PUBMED_API_KEY = None                 # Optional
SCOPUS_API_KEY = None                 # Optional
MODEL = "gpt-5-mini"                  # Default LLM
```

### secrets.txt (LLM API keys)

```
openai_key, sk-your-openai-key
claude_key, sk-ant-your-anthropic-key
```

## Usage

```bash
# Main interface - AI-assisted with feedback
python3 chat.py

# Resume interrupted search
python3 cli.py run --resume filtering --project your-project

# Check project status
python3 cli.py status --project my_project

# Run individual stages
python cli.py search
python cli.py filter
python cli.py download
python cli.py extract
```

## Pipeline Workflow

1. **Search Conditions** → Agent asks for search terms, platforms, dates
2. **Relevance Prompt** → Agent generates prompt, asks for approval
3. **Collection** → Searches all platforms, saves to JSONL
4. **Filtering** → Deduplicates, checks relevance with LLM
5. **Extraction Prompt** → Agent asks what info to extract
6. **Download** → Downloads PDFs from URLs
7. **Extraction** → Extracts info from PDFs using LLM

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
├── papers/
│   └── row{N}_{source}_{year}_{title}.pdf
├── extracted/
│   └── extracted_data.jsonl
└── logs/
    └── pipeline_state.json
```

## Supported LLM Models

| Provider | Models |
|----------|--------|
| OpenAI | gpt-5-mini, gpt-5.1, gpt-5.2, gpt-4.1, o3, o4-mini |
| Anthropic | claude-sonnet-4-5, claude-opus-4-5, claude-haiku-4-5 |
| Google | gemini-2.5-pro, gemini-2.5-flash, gemini-3-pro |
| Together | Llama-4, Qwen-2.5, QwQ-32B |

## License

MIT License

# Quick Start Guide - Data Scholar

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API Keys

```bash
cp config.example.py config.py
cp secrets.example.txt secrets.txt
```

Edit `secrets.txt`:
```
openai_key, sk-your-openai-key-here
```

Edit `config.py`:
```python
EMAIL = "your-email@example.com"  # Required for PubMed/OpenAlex
```

### 3. Run

```bash
python3 chat.py
```

## Usage

```
What are you researching? Survey of using LLM for rare disease diagnosis

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

💬 Feedback Loop
──────────────────────────────────────────────────────────────────────
Tell me what to change, or type 'yes' to proceed.
Examples:
  • 'add diagnosis to the query'
  • 'only papers from 2023'
  • 'include arxiv'
──────────────────────────────────────────────────────────────────────

💬 Feedback: add diagnosis and treatment
🤖 Processing... [Updates query]

💬 Feedback: yes
🚀 Starting pipeline...

✅ Complete!
📁 Results: output/llm-rare-disease/
```

## Output

```
output/{project_name}/
├── collected/          # Raw papers from all platforms
├── filtered/           # Relevant papers after filtering
├── papers/             # Downloaded PDFs
└── extracted/          # Extracted information
```

## Resume Interrupted Search

```bash
python3 cli.py run --resume filtering --project your-project
```

## Tips

- Describe your research clearly in the initial prompt
- Use feedback to refine: "add X to query", "only papers from Y", etc.
- Check filtered papers before downloading all PDFs

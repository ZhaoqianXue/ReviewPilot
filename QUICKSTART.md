# Quick Start Guide - ReviewPilot

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API Keys

```bash
# Create local config.py and secrets.txt if they are not already present.
# Both files are ignored by Git.
```

Edit `secrets.txt`:
```
openai_key, sk-your-openai-key-here
```

Edit `config.py`:
```python
EMAIL = "your-email@example.com"  # Required for PubMed/OpenAlex
```

### 3. Run the optimized frontend

```bash
.venv/bin/uvicorn web_app:app --host 127.0.0.1 --port 5602 --reload
```

Open http://127.0.0.1:5602.
Keep this process running while developing. Refresh the browser after frontend
changes; Python backend changes restart automatically because of `--reload`.

## Usage

1. Click **New conversation**.
2. Enter the project name, research question, search terms, platforms, and optional date range.
3. Open the project and run each workflow action from the right-hand ReviewPilot panel.
4. Results are written under `output/{project_name}/`.

## Output

```
output/{project_name}/
├── collected/          # Raw papers from all platforms
├── filtered/           # Relevant papers after filtering
├── pdfs/               # Downloaded PDFs and download report
├── extraction/         # Schema, prompt, and extracted information
└── categorization/     # Category mapping and categorized rows
```

## Prototype Reference

The old Streamlit and CLI interfaces live only in local `ReviewPilot_prototype/`
for historical comparison. The active migrated UI is served by `web_app.py`.

## Tips

- Describe your research clearly in the initial prompt
- Use feedback to refine: "add X to query", "only papers from Y", etc.
- Check filtered papers before downloading all PDFs

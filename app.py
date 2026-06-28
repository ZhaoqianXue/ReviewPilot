#!/usr/bin/env python3
"""
Data Scholar - Streamlit Web Interface with Chat
Step-by-step systematic review workflow with conversational interaction
"""

import streamlit as st
import json
import pandas as pd
import os
import re
import shutil
import time
import inspect
from pathlib import Path
from datetime import datetime
import sys

sys.path.insert(0, str(Path(__file__).parent))

from utils.llm import query_llm, load_api_key
from utils.jsonl_handler import read_jsonl, write_jsonl, save_json, load_json, write_xlsx, save_papers_with_xlsx
from utils.pdf_downloader import create_pdf_downloader
from utils.memory import MemoryManager, ExtractionSchema, ExtractionField, ScreeningCriteria
from utils.agent_memory import AgentMemory
from main import AcademicSearcher
from pydantic import BaseModel, Field, create_model
from ui_state import (
    WORKFLOW_STEPS,
    build_step_states,
    clamp_resume_step,
    project_stage_label,
    schema_workbench_state,
)


def app_llm_model() -> str:
    """Single model used by the Streamlit app for LLM-assisted steps."""
    return os.getenv("REVIEWPILOT_LLM_MODEL", "gpt-5.4-nano")


def is_valid_api_email(email: str) -> bool:
    email = (email or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return False
    return not email.lower().endswith("@example.com")


def default_api_email(config: dict = None) -> str:
    config = config or {}
    candidates = [
        config.get("api_email"),
        config.get("email"),
        os.getenv("UNPAYWALL_EMAIL"),
        os.getenv("REVIEWPILOT_API_EMAIL"),
        os.getenv("REVIEWPILOT_EMAIL"),
    ]
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if is_valid_api_email(candidate):
            return candidate
    return ""


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def archive_path_if_exists(path: Path, label: str = "stale") -> Path:
    """Move old run artifacts aside so a new user action starts from scratch."""
    if not path.exists():
        return path
    archive = path.with_name(f"{path.name}.{label}-{new_run_id()}")
    counter = 1
    while archive.exists():
        archive = path.with_name(f"{path.name}.{label}-{new_run_id()}-{counter}")
        counter += 1
    shutil.move(str(path), str(archive))
    return archive

# Page config
st.set_page_config(
    page_title="ReviewPilot",
    page_icon="logo.png",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ============================================================================
# DESIGN SYSTEM
# Color/typography/focus are owned by .streamlit/config.toml (the single source
# of truth). This small stylesheet only adds what native theming can't express:
# design tokens, quiet elevation on cards, and a visible keyboard-focus ring.
# (Replaces the former ~1040 lines of !important overrides + 30ms JS loop.)
# ============================================================================
st.markdown("""
<style>
    /* ---------- Design tokens ---------- */
    :root {
        --brand: #1a365d;
        --brand-hover: #2c5282;
        --brand-soft: #eaf0f7;
        --ink: #1f2937;
        --ink-soft: #5b6573;
        --border: #e3e8ef;
        --surface: #ffffff;
        --surface-2: #f6f8fb;
        --radius: 10px;
        --radius-sm: 7px;
        --shadow-sm: 0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.04);
    }

    /* ---------- Layout: honor wide mode, comfortable measure ---------- */
    .main .block-container { padding-top: 2rem; padding-bottom: 3rem; max-width: 1360px; }

    /* ---------- Typography: real scale (no Arial lock, no 0.85rem clamps) ---------- */
    h1, h2, h3, h4, h5, h6 { color: var(--ink); font-weight: 650; letter-spacing: -0.01em; }
    h1 { font-size: 1.75rem; }
    h2 { font-size: 1.4rem; }
    h3 { font-size: 1.15rem; }

    /* ---------- Visible, brand-colored keyboard focus (keyboard-only) ---------- */
    *:focus-visible { outline: 2px solid var(--brand) !important; outline-offset: 2px !important; }

    /* ---------- Buttons: calm, consistent radius ---------- */
    .stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
        border-radius: var(--radius-sm); font-weight: 550;
    }

    /* ---------- Metric cards: quiet elevation ---------- */
    [data-testid="stMetric"] {
        background: var(--surface); border: 1px solid var(--border);
        border-radius: var(--radius); padding: 0.85rem 1rem; box-shadow: var(--shadow-sm);
    }
    [data-testid="stMetricValue"] { color: var(--brand); font-weight: 650; }

    /* ---------- Chat messages: subtle card ---------- */
    [data-testid="stChatMessage"] {
        background: var(--surface); border: 1px solid var(--border);
        border-radius: var(--radius); box-shadow: var(--shadow-sm);
    }

    /* ---------- Sidebar: calm light surface (from secondaryBackground) ---------- */
    [data-testid="stSidebar"] { border-right: 1px solid var(--border); }
    [data-testid="stSidebar"] .stButton > button { text-align: left; justify-content: flex-start; }
    .rp-brand { display: flex; align-items: center; gap: .6rem; padding: .25rem 0 .75rem; }
    .rp-brand h1 { font-size: 1.15rem; margin: 0; color: var(--brand); }
    .rp-eyebrow { font-size: .72rem; font-weight: 650; letter-spacing: .08em;
        text-transform: uppercase; color: var(--ink-soft); margin: .25rem 0 .35rem; }
    .rp-nav-step {
        border: 1px solid var(--border); border-radius: var(--radius-sm);
        padding: .65rem .75rem; margin: .45rem 0; background: var(--surface);
        color: var(--ink-soft); font-size: .86rem;
    }
    .rp-nav-step strong { color: var(--ink); font-weight: 650; }
    .rp-nav-step.complete { border-color: #c8d8e8; background: #fbfdff; }
    .rp-nav-step.current { border-color: var(--brand); background: var(--brand-soft); color: var(--brand); }
    .rp-nav-step.pending, .rp-nav-step.locked { opacity: .72; }

    /* ---------- Expanders & dataframes: consistent border/radius ---------- */
    [data-testid="stExpander"] { border: 1px solid var(--border); border-radius: var(--radius); }
    [data-testid="stDataFrame"] { border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }

    /* ---------- Misc ---------- */
    hr { margin: 0.9rem 0; border-color: var(--border); }
    .stDeployButton, [data-testid="stDeployButton"],
    [data-testid="stAppDeployButton"] { display: none; }

    /* ---------- App masthead ---------- */
    .rp-header { display: flex; align-items: center; gap: 1rem; padding: 0.25rem 0 1.25rem; }
    .rp-header img { width: 48px; height: 48px; border-radius: 10px; }
    .rp-header h1 { margin: 0; font-size: 1.6rem; color: var(--brand); }
    .rp-header p { margin: 0; color: var(--ink-soft); font-size: 0.9rem; }

    /* ---------- Workbench structure ---------- */
    .rp-workbench-head {
        border: 1px solid var(--border); border-radius: var(--radius);
        background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
        padding: 1rem 1.15rem; margin: .25rem 0 1rem; box-shadow: var(--shadow-sm);
    }
    .rp-workbench-title { font-size: 1.05rem; font-weight: 700; color: var(--ink); margin: 0; }
    .rp-workbench-meta { color: var(--ink-soft); font-size: .86rem; margin-top: .25rem; }
    .rp-step-ribbon { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .55rem; margin: .75rem 0 1rem; }
    .rp-step-chip {
        min-height: 74px; border: 1px solid var(--border); border-radius: var(--radius-sm);
        background: var(--surface); padding: .55rem .65rem; box-shadow: var(--shadow-sm);
    }
    .rp-step-chip .num { font-size: .72rem; color: var(--ink-soft); font-weight: 650; }
    .rp-step-chip .name { font-size: .84rem; line-height: 1.25; color: var(--ink); font-weight: 650; margin-top: .15rem; }
    .rp-step-chip.complete { border-color: #bcd4d2; background: #f6fbfa; }
    .rp-step-chip.current { border-color: var(--brand); background: var(--brand-soft); }
    .rp-step-chip.locked, .rp-step-chip.pending { opacity: .62; }
    .rp-task-card {
        border: 1px solid var(--border); border-radius: var(--radius);
        background: var(--surface); padding: 1rem; margin-bottom: .85rem; box-shadow: var(--shadow-sm);
    }
    .rp-task-card h3 { margin: 0 0 .35rem; }
    .rp-task-card p { color: var(--ink-soft); margin: .25rem 0 0; }
    .rp-callout {
        border: 1px solid #cfe0f5; border-radius: var(--radius);
        background: #f3f7fd; color: var(--ink); padding: .85rem .95rem;
        font-size: .9rem; margin: .75rem 0;
    }
    .rp-subtle-panel {
        border: 1px solid var(--border); border-radius: var(--radius);
        background: var(--surface); padding: .9rem; margin: .65rem 0;
    }
    .rp-action-panel {
        border: 1px solid var(--border); border-radius: var(--radius);
        background: var(--surface); padding: 1rem; box-shadow: var(--shadow-sm);
    }
    .rp-action-panel h3 { margin-top: 0; }
    .rp-action-note { color: var(--ink-soft); font-size: .86rem; line-height: 1.45; }
    @media (max-width: 760px) {
        .main .block-container { padding-top: 1rem; }
        .rp-header { align-items: flex-start; }
        .rp-step-ribbon { grid-template-columns: 1fr; }
        .rp-step-chip { min-height: auto; }
    }
</style>
""", unsafe_allow_html=True)

# Available platforms
PLATFORMS = {
    "pubmed": "PubMed - Biomedical literature",
    "arxiv": "arXiv - CS, physics, math preprints (rate-limited)",
    "openalex": "OpenAlex - Open scholarly database",
    "scopus": "Scopus - Elsevier (requires API key)",
    "wos": "Web of Science (requires API key)",
    "google_scholar": "Google Scholar (rate-limited)",
    "dblp": "DBLP - CS Conferences & Journals"
}

# Initialize session state
def init_session_state():
    defaults = {
        'step': 0,  # 0 = project selection, 1-5 = workflow steps
        'messages': [],
        'project_config': {},
        'collected_papers': [],
        'filtered_papers': [],
        'relevant_papers': [],
        # Step completion flags (5 steps now)
        'step1_finalized': False,  # Search Setup
        'step2_finalized': False,  # Paper Screening
        'step3_finalized': False,  # Paper Collection
        'step4_finalized': False,  # Information Extraction
        'step5_finalized': False,  # Categorization & Analysis
        'awaiting_input': False,
        'current_question': None,
        # Project selection
        'project_selected': False,
        'existing_projects': [],
        # Step 1: Multiple search queries support
        'search_queries': [],  # List of {"name": str, "query": str}
        # Step 2: Paper Screening
        'relevance_prompt': None,
        'relevance_prompt_finalized': False,
        'step2_intro_shown': False,
        'collection_done': False,  # Papers collected
        'filtering_done': False,   # Basic filtering done
        # Step 3: Paper Collection
        'pdfs_downloaded': False,
        'pdf_download_stats': None,
        'step3_intro_shown': False,
        # Step 4: Extraction workflow
        'extraction_prompt': None,
        'extraction_prompt_finalized': False,
        'extraction_schema': None,  # Pydantic-like schema for structured output
        'step4_intro_shown': False,
        'extraction_results': [],
        'extraction_in_progress': False,
        # Step 5: Categorization & Analysis
        'step5_intro_shown': False,
        'categorization_field': None,
        'categorization_mapping': {},  # {original_value: category}
        'suggested_categories': [],  # List of category names
        'category_descriptions': {},  # {category: description}
        'chat_suggested_categories': '',  # Categories from chat conversation
        'categories_confirmed': False,  # Whether user confirmed categories
        'confirmed_categories': [],  # Confirmed category list
        'cat_mode': 'single',  # 'single' or 'multiple' categories per paper
        'categorization_done': False,
        # Memory system
        'memory_manager': None,  # Initialized when project folder is created
        'agent_memory': None  # Agent-level memory shared across projects
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()


def add_message(role: str, content: str):
    """Add a message to the chat history."""
    st.session_state.messages.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat()
    })


def extract_json(response: str) -> dict:
    """Extract JSON from LLM response."""
    text = response.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())


def list_existing_projects() -> list:
    """List all existing projects in the output folder."""
    output_dir = Path("output")
    if not output_dir.exists():
        return []

    projects = []
    for project_dir in sorted(output_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if project_dir.is_dir():
            config_file = project_dir / "search_conditions.json"
            if config_file.exists():
                try:
                    config = load_json(str(config_file))
                    # Determine project progress
                    step = 1
                    if (project_dir / "collected").exists():
                        step = 2
                    if (project_dir / "filtered" / "included_papers.jsonl").exists():
                        step = 3
                    if (project_dir / "pdfs").exists() and any((project_dir / "pdfs").glob("*.pdf")):
                        step = 4
                    if (project_dir / "extraction" / "extraction_results.jsonl").exists():
                        step = 5
                    if (project_dir / "categorization").exists():
                        step = 6  # Completed

                    projects.append({
                        "name": project_dir.name,
                        "path": str(project_dir),
                        "config": config,
                        "current_step": step,
                        "modified": datetime.fromtimestamp(project_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    })
                except:
                    pass
    return projects


def load_existing_project(project_path: str, resume_step: int = None) -> dict:
    """Load an existing project and restore session state.

    Args:
        project_path: Path to the project directory
        resume_step: Which step to resume from (1-5), or None to continue from last
    """
    project_dir = Path(project_path)
    config_file = project_dir / "search_conditions.json"

    if not config_file.exists():
        return None

    config = load_json(str(config_file))

    # Reset session state for the project
    st.session_state.project_config = config
    st.session_state.project_selected = True

    # Load collected papers
    collected_dir = project_dir / "collected"
    if collected_dir.exists():
        all_papers = []
        for jsonl_file in collected_dir.glob("*.jsonl"):
            papers = read_jsonl(str(jsonl_file))
            all_papers.extend(papers)
        st.session_state.collected_papers = all_papers
        st.session_state.step1_finalized = True

    # Load filtered papers
    filtered_file = project_dir / "filtered" / "filtered_papers.jsonl"
    if filtered_file.exists():
        st.session_state.filtered_papers = read_jsonl(str(filtered_file))
        st.session_state.collection_done = True
        st.session_state.filtering_done = True

    # Load included papers (after screening)
    included_file = project_dir / "filtered" / "included_papers.jsonl"
    if included_file.exists():
        st.session_state.relevant_papers = read_jsonl(str(included_file))
        st.session_state.step2_finalized = True

    # Check for PDFs
    pdf_dir = project_dir / "pdfs"
    if pdf_dir.exists():
        pdf_count = len(list(pdf_dir.glob("*.pdf")))
        if pdf_count > 0:
            st.session_state.pdfs_downloaded = True
            st.session_state.pdf_download_stats = {"success": pdf_count}
            st.session_state.step3_finalized = True

    # Load extraction results and schema
    extraction_file = project_dir / "extraction" / "extraction_results.jsonl"
    extraction_schema_file = project_dir / "extraction" / "extraction_schema.json"

    if extraction_file.exists():
        st.session_state.extraction_results = read_jsonl(str(extraction_file))
        st.session_state.step4_finalized = True
        st.session_state.extraction_prompt_finalized = True

        # Try to load saved schema first
        if extraction_schema_file.exists():
            st.session_state.extraction_schema = load_json(str(extraction_schema_file))
        elif st.session_state.extraction_results:
            # Infer schema from extraction results columns
            first_result = st.session_state.extraction_results[0]
            metadata_fields = {'paper_id', 'title', 'authors', 'year', 'doi', 'source', 'pdf_path',
                             'title_match', 'title_similarity', 'extraction_source', 'url', 'abstract'}
            inferred_fields = []
            for key in first_result.keys():
                if key not in metadata_fields and not key.endswith('_category'):
                    inferred_fields.append({
                        "name": key,
                        "description": f"Extracted field: {key.replace('_', ' ')}",
                        "example": str(first_result.get(key, ""))[:50]
                    })
            st.session_state.extraction_schema = {"fields": inferred_fields}

    # Load categorization if exists
    categorization_file = project_dir / "categorization" / "categorization_mapping.json"
    if categorization_file.exists():
        mapping = load_json(str(categorization_file))
        st.session_state.categorization_mapping = mapping.get("mapping", {})
        st.session_state.categorization_field = mapping.get("field")
        st.session_state.step5_finalized = True

    # Determine which step to go to
    if resume_step:
        st.session_state.step = resume_step
    else:
        # Go to the next incomplete step
        if not st.session_state.step1_finalized:
            st.session_state.step = 1
        elif not st.session_state.step2_finalized:
            st.session_state.step = 2
        elif not st.session_state.step3_finalized:
            st.session_state.step = 3
        elif not st.session_state.step4_finalized:
            st.session_state.step = 4
        elif not st.session_state.step5_finalized:
            st.session_state.step = 5
        else:
            st.session_state.step = 5  # Review completed project

    # Initialize memory manager
    st.session_state.memory_manager = MemoryManager(project_path=project_dir)

    return config


# ========== Tool-Use Intent Detection ==========

def detect_intent_with_tools(user_message: str, system_prompt: str, tools: list, context: str = "") -> dict:
    """Use OpenAI function calling to detect user intent reliably."""
    from openai import OpenAI

    messages = [{"role": "system", "content": system_prompt}]
    if context:
        messages.append({"role": "user", "content": f"Context:\n{context}"})
        messages.append({"role": "assistant", "content": "Understood. How can I help?"})
    messages.append({"role": "user", "content": user_message})

    client = OpenAI(api_key=load_api_key('openai'))

    try:
        response = client.chat.completions.create(
            model=app_llm_model(),
            messages=messages,
            tools=tools,
            tool_choice="required",
            temperature=0.1
        )
        message = response.choices[0].message
        if message.tool_calls:
            tool_call = message.tool_calls[0]
            return {
                "action": tool_call.function.name,
                "args": json.loads(tool_call.function.arguments)
            }
    except Exception as e:
        st.toast(f"Intent detection error: {str(e)[:50]}")

    return {"action": "unknown", "args": {"instruction": user_message}}


# Step 1: Search Query Configuration tools
STEP1_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "modify_query",
            "description": "User wants to modify/change/update the current search query. Examples: 'remove the third group', 'add X', 'make it two groups', 'change to...'",
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "description": "The user's modification instruction"}
                },
                "required": ["instruction"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_query",
            "description": "User is satisfied with current query. Examples: 'done', 'ok', 'good', 'looks good', 'perfect', 'that works'",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_query",
            "description": "User wants to add another/new search query. Examples: 'yes', 'add another', 'I also want to search for...', or describing a new topic",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "The new query topic/description if provided"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_collection",
            "description": "User wants to start paper collection now. Examples: 'no' (to adding more queries), 'start', 'search', 'begin', 'collect'",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# Step 2: Screening Prompt tools
STEP2_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "show_prompt",
            "description": "Show/display the current screening prompt. Examples: 'show', 'display', 'let me see', 'what is the prompt'",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "modify_prompt",
            "description": "Modify/update/change the screening prompt. Examples: 'add exclusion for reviews', 'remove criterion X', 'change the focus to...'",
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "description": "What to change in the prompt"},
                    "updated_prompt": {"type": "string", "description": "The full updated screening prompt with changes applied"}
                },
                "required": ["instruction", "updated_prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "answer_question",
            "description": "Answer a question about the screening process or prompt. Examples: 'how does this work?', 'what criteria are used?'",
            "parameters": {
                "type": "object",
                "properties": {
                    "response": {"type": "string", "description": "Your helpful answer to the question"}
                },
                "required": ["response"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_screening",
            "description": "User wants to finalize/confirm the screening prompt. Examples: 'done', 'finalize', 'confirm', 'looks good', 'proceed'",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# Step 4: Extraction Schema tools
STEP4_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "show_schema",
            "description": "Show/display the current extraction schema fields. Examples: 'show schema', 'show fields', 'what fields do we have'",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "show_prompt",
            "description": "Show the full extraction prompt. Examples: 'show prompt', 'show extraction prompt'",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_field",
            "description": "Add a new field to the extraction schema. Examples: 'add a field for sample size', 'I want to extract the methodology'",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Field name (snake_case)"},
                    "description": {"type": "string", "description": "What this field captures"},
                    "example": {"type": "string", "description": "Example value for this field"}
                },
                "required": ["name", "description", "example"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_field",
            "description": "Remove a field from the extraction schema. Examples: 'remove the X field', 'delete field Y'",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string", "description": "Name of the field to remove"}
                },
                "required": ["field_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "modify_field",
            "description": "Modify an existing field. Examples: 'change the description of X', 'rename field Y to Z'",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string", "description": "Current field name to modify"},
                    "new_name": {"type": "string", "description": "New field name (if renaming)"},
                    "new_description": {"type": "string", "description": "New description (if changing)"},
                    "new_example": {"type": "string", "description": "New example (if changing)"}
                },
                "required": ["field_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "answer_question",
            "description": "Answer a question about extraction. Examples: 'how does extraction work?', 'what format?'",
            "parameters": {
                "type": "object",
                "properties": {
                    "response": {"type": "string", "description": "Your helpful answer"}
                },
                "required": ["response"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_extraction",
            "description": "User wants to finalize schema and start extraction. Examples: 'done', 'finalize', 'start extraction', 'confirm'",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]


def generate_search_query(description: str) -> dict:
    """Generate search query from research description - simple fallback if LLM fails."""
    import re
    slug = re.sub(r'[^a-z0-9]+', '-', description.lower())[:40].strip('-')

    # Simple keyword extraction as fallback
    words = description.lower().split()
    keywords = [w for w in words if len(w) > 3 and w not in ['using', 'with', 'about', 'survey', 'review', 'study', 'for', 'the']]

    # Build search terms
    search_terms = '("' + '" OR "'.join(keywords[:3]) + '")' if keywords else "research"

    fallback = {
        "project_name": slug or "research-project",
        "search_terms": search_terms,
        "primary_topic": keywords[0] if keywords else "research",
        "domain": keywords[-1] if len(keywords) > 1 else "general"
    }

    # Get agent-level memory context for search suggestions
    agent_mem = get_agent_memory()
    memory_context = agent_mem.get_context_for_step("search", description)

    # Check for user preferences about query format
    agent_mem_pref = get_agent_memory()
    preferred_groups = agent_mem_pref.get_preference('preferred_groups')
    preference_note = ""
    if preferred_groups:
        preference_note = f"\n\nIMPORTANT USER PREFERENCE: The user prefers queries with exactly {preferred_groups} AND groups. Do NOT add extra groups beyond {preferred_groups}."

    prompt = f"""Generate academic paper search configuration for: "{description}"

IMPORTANT: Create a proper Boolean search query with concept groups connected by AND.
Each concept group should have synonyms/variants connected by OR.
Only include groups that are essential to the research topic. Do NOT add unnecessary groups like "study" or "research" or "analysis".

Example for "LLM for rare disease diagnosis":
("rare disease" OR "orphan disease" OR "rare genetic disorder") AND ("LLM" OR "large language model" OR "GPT" OR "ChatGPT" OR "Claude")

{memory_context}{preference_note}

Return ONLY valid JSON (no markdown):
{{
  "project_name": "slug-name",
  "search_terms": "Boolean query with (group1) AND (group2) structure",
  "primary_topic": "main topic",
  "domain": "research field"
}}"""

    for model in [app_llm_model()]:
        try:
            response, _ = query_llm(
                text_prompt=prompt,
                system_prompt="You are an expert at creating academic search queries. Use any learned patterns or user preferences provided. Return only valid JSON.",
                model=model
            )
            result = extract_json(response)
            for key in ["project_name", "search_terms", "primary_topic", "domain"]:
                if key not in result:
                    result[key] = fallback[key]
            return result
        except Exception as e:
            st.toast(f"Model {model} failed: {str(e)[:50]}")
            continue

    return fallback


def get_conversation_history() -> str:
    """Get recent conversation history for context."""
    if not st.session_state.messages:
        return ""

    # Get last 10 messages for context
    recent = st.session_state.messages[-10:]
    history = []
    for msg in recent:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"][:500]  # Truncate long messages
        history.append(f"{role}: {content}")

    return "\n".join(history)


def generate_extraction_fields(description: str, topic: str, domain: str) -> list:
    """Generate suggested extraction fields."""
    prompt = f"""For a research survey on: "{description}"
Topic: {topic}
Domain: {domain}

Suggest 5-7 specific fields to extract from each paper. Return ONLY a JSON array of field names.
Example: ["datasets used", "model architecture", "evaluation metrics", "key findings"]"""

    for model in [app_llm_model()]:
        try:
            response, _ = query_llm(
                text_prompt=prompt,
                system_prompt="Return only a JSON array of strings.",
                model=model
            )
            text = response.strip()
            if text.startswith('['):
                return json.loads(text)
        except:
            continue

    return ["methods", "datasets", "key findings", "evaluation metrics", "limitations"]


def generate_extraction_prompt_and_schema(description: str, topic: str, domain: str, relevance_prompt: str) -> tuple:
    """
    Generate extraction prompt and schema based on research topic and relevance criteria.

    NOTE: Metadata fields (title, authors, year, doi, source) are NOT included in the schema
    because they come directly from the paper metadata, not from PDF extraction.

    Returns:
        Tuple of (system_prompt, extraction_prompt, schema_dict)
    """
    # Get memory context for extraction suggestions
    memory = get_memory_manager(st.session_state.project_config)
    memory_context = memory.get_prompt_context("extraction", topic, domain)

    # Generate domain-specific fields using LLM
    fields_prompt = f"""You are an expert researcher designing a data extraction form for a systematic review.

Research Topic: "{description}"
Primary Topic: {topic}
Domain: {domain}

Based on the relevance criteria used for screening:
{relevance_prompt[:1000]}

Generate a comprehensive extraction schema with 8-12 fields to extract FROM THE PDF CONTENT.

IMPORTANT:
- Do NOT include metadata fields like: title, authors, year, doi, publication_year, study_title
  (these come from paper metadata, not PDF extraction)
- Focus on CONTENT that must be extracted by reading the paper

Each field should have:
- name: snake_case field name
- description: Clear description of what to extract (1 sentence)
- example: A concrete example of expected value

Include fields like:
1. "included" - Whether paper meets inclusion criteria after full-text review
2. Fields specific to {topic} methodology/approach (e.g., model_used, techniques)
3. Fields for {domain} application details (e.g., dataset, clinical_task)
4. Evaluation/results fields (e.g., evaluation_metrics, performance)
5. Limitations and future directions

{memory_context}

Return ONLY valid JSON in this format:
{{
    "fields": [
        {{"name": "included", "description": "Yes if included, No (reason) if excluded", "example": "Yes or No (not clinical)"}},
        {{"name": "field_name", "description": "What to extract", "example": "Example value"}}
    ]
}}"""

    # Check memory for suggested extraction schema (using memory from above)
    memory_schema = memory.suggest_extraction_schema(topic, domain)

    if memory_schema:
        # Use memory-suggested schema as default
        default_schema = {
            "fields": [
                {"name": f.name, "description": f.description, "example": f.examples[0] if f.examples else ""}
                for f in memory_schema.fields
            ]
        }
    else:
        # Get suggested fields from memory's common fields
        suggested_fields = memory.suggest_fields(domain)

        if suggested_fields:
            default_schema = {
                "fields": [{"name": "included", "description": "Yes if paper meets criteria after full-text review, No (reason) if excluded", "example": "Yes"}] + [
                    {"name": f.name, "description": f.description, "example": f.examples[0] if f.examples else ""}
                    for f in suggested_fields[:6]
                ]
            }
        else:
            default_schema = {
                "fields": [
                    {"name": "included", "description": "Yes if paper meets criteria after full-text review, No (reason) if excluded", "example": "Yes"},
                    {"name": "dataset", "description": "Dataset name(s) used in the study", "example": "MIMIC-IV, PubMedQA"},
                    {"name": "methods", "description": "Main methods or techniques used", "example": "Fine-tuned GPT-4 with RAG"},
                    {"name": "key_findings", "description": "Main findings or contributions", "example": "Achieved 85% accuracy on diagnosis task"},
                    {"name": "evaluation_metrics", "description": "Metrics used to evaluate performance", "example": "Accuracy, F1-score, AUC-ROC"},
                    {"name": "limitations", "description": "Limitations discussed in the paper", "example": "Small sample size, single institution"},
                    {"name": "future_directions", "description": "Future research directions mentioned", "example": "Multi-center validation needed"}
                ]
            }

    schema = default_schema
    for model in [app_llm_model()]:
        try:
            response, _ = query_llm(
                text_prompt=fields_prompt,
                system_prompt="You are an expert at designing systematic review extraction schemas. Return only valid JSON.",
                model=model
            )
            result = extract_json(response)
            if "fields" in result and len(result["fields"]) >= 3:
                schema = result
                break
        except Exception as e:
            continue

    # Build system prompt with explicit screening criteria for inclusion
    system_prompt = f"""You are an expert researcher extracting structured information from academic papers.
Your task is to analyze papers about "{topic}" in the domain of "{domain}".

Guidelines:
1. Be PRECISE - extract exact names, numbers, and terms from the paper
2. Be CONCISE - keep each field under 50 words unless more detail is needed
3. If information is NOT explicitly stated, write "None"
4. For metrics, include actual numbers when available (e.g., "0.85 accuracy, 0.82 F1")
5. Look carefully in Abstract, Methods, Results, and Discussion sections
6. For the "included" field, you MUST apply the SAME criteria used in Paper Screening (Step 2)

**Inclusion Criteria (from Paper Screening):**
{relevance_prompt[:1500] if relevance_prompt else "Standard PRISMA criteria for topic and domain relevance."}

When deciding "included", use the exact same criteria above. If the full-text reveals the paper doesn't meet these criteria, mark as "No (reason)"."""

    # Build extraction prompt with field definitions
    extraction_prompt = f"""Extract information from this research paper about {topic} in {domain}.

**Field Definitions and Examples:**

"""
    for i, field in enumerate(schema["fields"], 1):
        extraction_prompt += f"""{i}. **{field['name']}**: {field['description']}
   Example: "{field['example']}"

"""

    extraction_prompt += """**Paper Content:**

"""

    return system_prompt, extraction_prompt, schema


def get_extraction_intro_message(topic: str, domain: str, schema: dict) -> str:
    """Generate intro message for extraction step."""
    # Build detailed field list with examples
    fields_detailed = []
    for f in schema.get("fields", []):
        field_entry = f"**{f['name']}**\n  - Description: {f['description']}\n  - Example: _{f.get('example', 'N/A')}_"
        fields_detailed.append(field_entry)

    fields_display = "\n\n".join(fields_detailed)

    return f"""**Step 4: Information Extraction**

I've generated an extraction schema for **{topic}** in **{domain}**.

**Metadata (auto-populated):** `paper_id` • `title` • `authors` • `year` • `doi` • `source` • `url`

**Extraction Fields (from PDF):**

{fields_display}

**Revise the Schema:** Tell me what to change:
- "Add a field for [X]"
- "Remove the [field_name] field"
- "Show schema" / "Show prompt"

Click **"Finalize Schema"** when ready. Extraction takes ~15-30s per paper."""


def read_pdf(pdf_path: str) -> str:
    """Extract text from PDF file using PyMuPDF (fitz).

    Args:
        pdf_path: Path to the PDF file

    Returns:
        Extracted text from all pages
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return "Error: PyMuPDF not installed"

    try:
        doc = fitz.open(pdf_path)
        text = []
        for page in doc:
            text.append(page.get_text())
        doc.close()
        return "\n".join(text)
    except Exception as e:
        return f"Error reading PDF: {str(e)}"


def jaccard_similarity(str1: str, str2: str) -> float:
    """Calculate Jaccard similarity between two strings.

    Tokenizes strings into words and computes intersection/union ratio.
    Returns value between 0 (no similarity) and 1 (identical).
    """
    if not str1 or not str2:
        return 0.0

    # Normalize: lowercase, remove punctuation, split into words
    import re
    def tokenize(s):
        s = s.lower()
        s = re.sub(r'[^\w\s]', ' ', s)
        # Remove common stop words that don't help matching
        stop_words = {'a', 'an', 'the', 'of', 'and', 'or', 'in', 'on', 'for', 'to', 'with', 'by', 'is', 'are', 'was', 'were'}
        words = set(s.split())
        return words - stop_words

    set1 = tokenize(str1)
    set2 = tokenize(str2)

    if not set1 or not set2:
        return 0.0

    intersection = len(set1 & set2)
    union = len(set1 | set2)

    return intersection / union if union > 0 else 0.0


def check_title_match(expected_title: str, pdf_title: str) -> tuple:
    """Check if PDF title matches expected title using multiple methods.

    Returns:
        tuple: (match_status: str, similarity_score: float)
        - match_status: "Yes", "Likely", or "No (possible wrong PDF)"
        - similarity_score: best similarity score found
    """
    import re

    if not expected_title or not pdf_title:
        return ("N/A", 0.0)

    # Normalize both titles
    def normalize(s):
        s = s.lower().strip()
        s = re.sub(r'[^\w\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s)
        return s

    exp_norm = normalize(expected_title)
    pdf_norm = normalize(pdf_title)

    # Method 1: Jaccard similarity
    jaccard = jaccard_similarity(expected_title, pdf_title)

    # Method 2: Check if key words from expected title are in PDF title
    exp_words = set(exp_norm.split())
    pdf_words = set(pdf_norm.split())
    # Remove very short words
    exp_words = {w for w in exp_words if len(w) > 2}
    pdf_words = {w for w in pdf_words if len(w) > 2}

    if exp_words:
        word_overlap = len(exp_words & pdf_words) / len(exp_words)
    else:
        word_overlap = 0.0

    # Method 3: Check if first N significant words match (titles often get truncated)
    exp_sig_words = [w for w in exp_norm.split() if len(w) > 3][:6]
    pdf_sig_words = [w for w in pdf_norm.split() if len(w) > 3][:6]
    if exp_sig_words and pdf_sig_words:
        prefix_match = sum(1 for a, b in zip(exp_sig_words, pdf_sig_words) if a == b) / len(exp_sig_words)
    else:
        prefix_match = 0.0

    # Take the best score from all methods
    best_score = max(jaccard, word_overlap, prefix_match)

    # Determine match status with more lenient thresholds
    if best_score >= 0.7:
        return ("Yes", best_score)
    elif best_score >= 0.5:
        return ("Likely", best_score)
    else:
        return ("No (possible wrong PDF)", best_score)


def extract_title_from_pdf_text(pdf_text: str) -> str:
    """Extract the likely title from the beginning of PDF text.

    Usually the title appears in the first few lines before abstract.
    Uses multiple heuristics to find the best title candidate.
    """
    import re

    if not pdf_text or len(pdf_text) < 50:
        return ""

    # Take first 3000 chars and look for title patterns
    header = pdf_text[:3000]
    lines = [line.strip() for line in header.split('\n') if line.strip()]

    # Title is usually one of the first non-empty lines, often the longest
    # before "Abstract" appears
    title_candidates = []
    abstract_found = False

    for i, line in enumerate(lines[:20]):  # Check first 20 lines
        line_lower = line.lower()

        # Stop at abstract
        if line_lower.startswith('abstract') or line_lower == 'abstract':
            abstract_found = True
            break

        # Skip common header patterns
        skip_patterns = [
            r'^\d+$',  # Just numbers
            r'^page\s*\d+',  # Page numbers
            r'^vol\.\s*\d+',  # Volume numbers
            r'^\d{4}\s*(ieee|acm|springer)',  # Conference/journal headers
            r'^arxiv:',  # arXiv IDs
            r'^preprint',  # Preprint markers
            r'^accepted|submitted|published',  # Status markers
            r'^author|correspondence|email|@',  # Author info
        ]
        if any(re.match(p, line_lower) for p in skip_patterns):
            continue

        # Good title candidates: 20-400 chars, contains letters
        if 20 <= len(line) <= 400 and re.search(r'[a-zA-Z]', line):
            # Higher score for lines with title-like characteristics
            score = len(line)
            # Boost if contains colon (common in titles)
            if ':' in line:
                score += 50
            # Boost if starts with capital
            if line[0].isupper():
                score += 30
            title_candidates.append((score, line))

    if title_candidates:
        # Return the highest scored candidate
        title_candidates.sort(key=lambda x: x[0], reverse=True)
        return title_candidates[0][1]

    # Fallback: concatenate first few meaningful lines
    meaningful_lines = [l for l in lines[:5] if len(l) > 10 and re.search(r'[a-zA-Z]', l)]
    if meaningful_lines:
        return ' '.join(meaningful_lines[:2])

    return lines[0] if lines else ""


def extract_via_web_search(paper: dict, extraction_fields: list, topic: str, domain: str) -> dict:
    """Extract paper information using web search when PDF is unavailable.

    Uses the paper's title, DOI, and abstract to search for information.
    """
    title = paper.get("title", "")
    doi = paper.get("doi", "")
    abstract = paper.get("abstract", "")

    # Build search context
    search_context = f"Paper: {title}\n"
    if doi:
        search_context += f"DOI: {doi}\n"
    if abstract:
        search_context += f"Abstract: {abstract[:1000]}\n"

    field_descriptions = "\n".join([
        f"- {f['name']}: {f['description']}"
        for f in extraction_fields
    ])

    prompt = f"""Extract the following information about this research paper on {topic} in {domain}.

{search_context}

Fields to extract:
{field_descriptions}

Based on the title and abstract provided, extract as much information as possible.
For fields that cannot be determined from the available information, return "Not available (no PDF)".

Return ONLY a valid JSON object with the field names as keys."""

    result = {f["name"]: "Not available (no PDF)" for f in extraction_fields}

    try:
        response, _ = query_llm(
            text_prompt=prompt,
            system_prompt="You are an expert researcher. Extract information from the paper details provided. Return only valid JSON.",
            model=app_llm_model()
        )

        # Parse JSON response
        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        extracted = json.loads(text.strip())
        for field in extraction_fields:
            if field["name"] in extracted:
                result[field["name"]] = extracted[field["name"]]

    except Exception as e:
        # If extraction fails, keep default "Not available" values
        pass

    return result


def run_collection(config: dict, output_dir: Path = None, progress_callback=None) -> dict:
    """Run paper collection from platforms with real-time saving.

    Args:
        config: Search configuration with search_queries or search_terms
        output_dir: Directory to save results (enables real-time saving)
        progress_callback: Optional callback(platform, query_name, count) for progress updates

    Returns:
        Dictionary with papers, platform_stats, query_stats, and total
    """
    searcher = AcademicSearcher()

    # Get search queries (support both old single query and new multiple queries)
    search_queries = config.get("search_queries", [])
    if not search_queries and config.get("search_terms"):
        search_queries = [{"name": "main", "query": config["search_terms"]}]

    # Setup output directory for real-time saving
    collected_dir = None
    if output_dir:
        collected_dir = output_dir / "collected"
        archive_path_if_exists(collected_dir, "previous-run")
        collected_dir.mkdir(parents=True, exist_ok=True)

    all_papers = []
    platform_stats = {}
    query_stats = {}
    seen_ids = set()  # For deduplication across queries

    # Run each search query
    for sq in search_queries:
        query_name = sq.get("name", "unnamed")
        query = sq.get("query", "")
        if not query:
            continue

        query_stats[query_name] = {}

        for platform in config["platforms"]:
            try:
                # Search this platform with this query
                # Pass output_folder for real-time saving
                results = searcher.search(
                    query=query,
                    platforms=[platform],
                    max_results=config.get("max_results", 100),
                    output_folder=None
                )

                papers = results.get(platform, [])
                new_count = 0

                # Process and save papers in real-time
                for paper in papers:
                    paper_id = paper.get('id') or paper.get('doi') or paper.get('title', '')
                    if paper_id and paper_id in seen_ids:
                        continue  # Skip duplicates

                    seen_ids.add(paper_id)
                    paper["collected_at"] = datetime.now().isoformat()
                    paper["search_query"] = query_name
                    all_papers.append(paper)
                    new_count += 1

                    # Real-time save to JSONL
                    if collected_dir:
                        output_file = collected_dir / f"{platform}.jsonl"
                        with open(output_file, 'a', encoding='utf-8') as f:
                            f.write(json.dumps(paper, ensure_ascii=False) + '\n')

                # Update stats
                platform_stats[platform] = platform_stats.get(platform, 0) + new_count
                query_stats[query_name][platform] = new_count

                if progress_callback:
                    progress_callback(platform, query_name, new_count)

                print(f"  {platform} ({query_name}): {new_count} new papers")

            except Exception as e:
                print(f"  Error searching {platform} with query '{query_name}': {e}")
                query_stats[query_name][platform] = 0

    return {
        "papers": all_papers,
        "platform_stats": platform_stats,
        "query_stats": query_stats,
        "total": len(all_papers)
    }


def run_basic_filtering(papers: list, date_range: dict) -> dict:
    """Run basic filtering: date filter and deduplication."""
    import re
    from datetime import datetime

    # Parse date range (supports YYYY-MM-DD or YYYY format)
    start_date_str = date_range.get("start", "")
    end_date_str = date_range.get("end", "")

    start_date = None
    end_date = None

    if start_date_str:
        try:
            if len(start_date_str) == 10:  # YYYY-MM-DD
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            else:  # YYYY only
                start_date = datetime.strptime(start_date_str[:4] + "-01-01", "%Y-%m-%d")
        except:
            pass

    if end_date_str:
        try:
            if len(end_date_str) == 10:  # YYYY-MM-DD
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
            else:  # YYYY only
                end_date = datetime.strptime(end_date_str[:4] + "-12-31", "%Y-%m-%d")
        except:
            pass

    date_filtered = []
    for paper in papers:
        paper_date = None
        # Try to get full date first, then fall back to year
        pub_date = paper.get("publication_date") or paper.get("date") or paper.get("year")

        if pub_date:
            try:
                pub_str = str(pub_date)
                if len(pub_str) >= 10:
                    paper_date = datetime.strptime(pub_str[:10], "%Y-%m-%d")
                elif len(pub_str) >= 4:
                    paper_date = datetime.strptime(pub_str[:4] + "-06-15", "%Y-%m-%d")  # Mid-year estimate
            except:
                pass

        if paper_date:
            if start_date and paper_date < start_date:
                continue
            if end_date and paper_date > end_date:
                continue

        date_filtered.append(paper)

    seen_titles = set()
    unique_papers = []
    for paper in date_filtered:
        title = paper.get("title", "")
        normalized = re.sub(r'[^\w\s]', '', title.lower())
        normalized = ' '.join(normalized.split())
        if normalized and normalized not in seen_titles:
            seen_titles.add(normalized)
            unique_papers.append(paper)

    return {
        "papers": unique_papers,
        "stats": {
            "initial": len(papers),
            "after_date_filter": len(date_filtered),
            "after_dedup": len(unique_papers),
            "removed_by_date": len(papers) - len(date_filtered),
            "removed_by_dedup": len(date_filtered) - len(unique_papers)
        }
    }


def run_relevance_check(papers: list, topic: str, domain: str) -> dict:
    """Run LLM-based relevance checking."""
    system_prompt = f"""You are an academic paper relevance checker.
Determine if a paper is relevant to research on "{topic}" in the domain of "{domain}".
Answer only with "true" or "false"."""

    relevant = []
    irrelevant = []

    for paper in papers:
        title = paper.get("title", "")
        abstract = paper.get("abstract", "")[:1000]

        user_prompt = f"""Title: {title}
Abstract: {abstract if abstract else "No abstract available"}

Is this paper relevant? Answer true or false."""

        is_relevant = None
        for model in [app_llm_model()]:
            try:
                response, _ = query_llm(
                    text_prompt=user_prompt,
                    system_prompt=system_prompt,
                    model=model
                )
                is_relevant = response.strip().lower() == "true"
                break
            except:
                continue

        paper["is_relevant"] = is_relevant
        if is_relevant or is_relevant is None:
            relevant.append(paper)
        else:
            irrelevant.append(paper)

    return {
        "relevant": relevant,
        "irrelevant": irrelevant,
        "stats": {
            "total_checked": len(papers),
            "relevant_count": len(relevant),
            "irrelevant_count": len(irrelevant)
        }
    }


def get_agent_memory() -> AgentMemory:
    """Get or create the agent-level memory."""
    if not st.session_state.agent_memory:
        st.session_state.agent_memory = AgentMemory()
    return st.session_state.agent_memory


def get_memory_manager(config: dict = None) -> MemoryManager:
    """Get or create the memory manager for the current project."""
    if st.session_state.memory_manager:
        return st.session_state.memory_manager

    if config and config.get("project_name"):
        project_path = Path("output") / config["project_name"]
        project_path.mkdir(parents=True, exist_ok=True)
        st.session_state.memory_manager = MemoryManager(project_path=project_path)
        return st.session_state.memory_manager

    # Return a memory manager without short-term memory if no project yet
    return MemoryManager()


def save_project_output(config: dict, stage: str, data: dict):
    """Save output to project folder."""
    output_dir = Path("output") / config["project_name"]
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize memory manager for this project if not already done
    if not st.session_state.memory_manager:
        st.session_state.memory_manager = MemoryManager(project_path=output_dir)

    if stage == "collection":
        collected_dir = output_dir / "collected"
        collected_dir.mkdir(exist_ok=True)

        by_platform = {}
        for paper in data["papers"]:
            platform = paper.get("source", "unknown")
            if platform not in by_platform:
                by_platform[platform] = []
            by_platform[platform].append(paper)

        for platform, papers in by_platform.items():
            write_jsonl(str(collected_dir / f"{platform}.jsonl"), papers)

        save_json(str(collected_dir / "summary.json"), {
            "collected_at": datetime.now().isoformat(),
            "total_papers": data["total"],
            "platform_stats": data["platform_stats"]
        })

    elif stage == "filtering":
        filtered_dir = output_dir / "filtered"
        filtered_dir.mkdir(exist_ok=True)
        write_jsonl(str(filtered_dir / "filtered_papers.jsonl"), data["papers"])
        save_json(str(filtered_dir / "filtering_stats.json"), data["stats"])

    elif stage == "relevance":
        filtered_dir = output_dir / "filtered"
        filtered_dir.mkdir(exist_ok=True)

        # Handle both old format (relevant/irrelevant) and new format (included/excluded)
        included = data.get("included", data.get("relevant", []))
        excluded = data.get("excluded", data.get("irrelevant", []))

        # Define columns for xlsx export (in preferred order)
        xlsx_columns = [
            'paper_id', 'title', 'authors', 'year', 'source', 'doi',
            'abstract', 'url', 'screening_decision', 'exclusion_reasons',
            'pdf_downloaded', 'pdf_path', 'pdf_method'
        ]

        # Save included papers (jsonl + xlsx)
        write_jsonl(str(filtered_dir / "included_papers.jsonl"), included)
        write_xlsx(str(filtered_dir / "included_papers.xlsx"), included, xlsx_columns)

        # Save excluded papers (jsonl + xlsx)
        write_jsonl(str(filtered_dir / "excluded_papers.jsonl"), excluded)
        write_xlsx(str(filtered_dir / "excluded_papers.xlsx"), excluded, xlsx_columns)

        save_json(str(filtered_dir / "screening_stats.json"), data["stats"])

        # PRISMA log with full details
        logs_dir = output_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        prisma_log = {
            "generated_at": datetime.now().isoformat(),
            "project": config["project_name"],
            "prisma_flow": {
                "identification": {
                    "records_from_databases": st.session_state.get("collection_total", 0)
                },
                "screening": {
                    "records_before_screening": st.session_state.get("collection_total", 0),
                    "records_removed_by_date": st.session_state.get("removed_by_date", 0),
                    "duplicate_records_removed": st.session_state.get("removed_by_dedup", 0),
                    "records_after_screening": st.session_state.get("after_dedup", 0)
                },
                "eligibility": {
                    "records_assessed": data["stats"].get("total_screened", data["stats"].get("total_checked", 0)),
                    "records_excluded": data["stats"].get("excluded_count", data["stats"].get("irrelevant_count", 0)),
                    "exclusion_breakdown": data["stats"].get("exclusion_breakdown", {}),
                    "records_included": data["stats"].get("included_count", data["stats"].get("relevant_count", 0))
                }
            }
        }
        save_json(str(logs_dir / "prisma_log.json"), prisma_log)

    save_json(str(output_dir / "search_conditions.json"), config)
    return output_dir


# ============================================================================
# CHAT DISPLAY
# ============================================================================

# Explicit chat avatars — bypass the Material icon font entirely so the
# assistant avatar can never render as broken ligature text (e.g. "art").
CHAT_AVATARS = {"assistant": "🧭", "user": "🧑‍🔬"}


def display_chat():
    """Display chat messages."""
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar=CHAT_AVATARS.get(msg["role"])):
            st.markdown(msg["content"])


def collect_with_status(config, output_dir):
    """Run collection inside a persistent st.status panel with live per-platform
    progress (replaces an opaque spinner during a multi-minute database search)."""
    with st.status("Collecting papers from databases…", expanded=True) as status:
        def _cb(platform, query_name, count):
            status.update(label=f"Searching {platform} — {count} records so far…")
        result = run_collection(config, output_dir=output_dir, progress_callback=_cb)
        for plat, cnt in result.get("platform_stats", {}).items():
            status.write(f"• {plat}: {cnt} records")
        status.update(
            label=f"Collection complete — {result['total']} records found",
            state="complete", expanded=False,
        )
    return result


def stretch_width() -> dict:
    """Use Streamlit's current width API while keeping older installs usable."""
    if "width" in inspect.signature(st.button).parameters:
        return {"width": "stretch"}
    return {"use_container_width": True}


def dataframe_width() -> dict:
    if "width" in inspect.signature(st.dataframe).parameters:
        return {"width": "stretch"}
    return {"use_container_width": True}


def finalized_flags() -> list:
    return [
        st.session_state.step1_finalized,
        st.session_state.step2_finalized,
        st.session_state.step3_finalized,
        st.session_state.get("step4_finalized", False),
        st.session_state.get("step5_finalized", False),
    ]


def step_icon(status: str) -> str:
    return {
        "complete": ":material/check_circle:",
        "current": ":material/radio_button_checked:",
        "available": ":material/radio_button_unchecked:",
        "locked": ":material/lock:",
        "pending": ":material/radio_button_unchecked:",
    }.get(status, ":material/radio_button_unchecked:")


def render_static_step(state: dict):
    status = state["status"]
    label = f'{state["number"]}. {state["name"]}'
    st.markdown(
        f'<div class="rp-nav-step {status}"><strong>{label}</strong><br>'
        f'<span>{state["description"]}</span></div>',
        unsafe_allow_html=True,
    )


def render_sidebar_workflow():
    st.markdown('<div class="rp-eyebrow">Workflow</div>', unsafe_allow_html=True)
    current = st.session_state.step
    for state in build_step_states(current, finalized_flags()):
        is_current = state["status"] == "current"
        if state["reachable"] and not is_current:
            if st.button(
                f'{state["number"]}.  {state["name"]}',
                key=f"nav_step_{state['number']}",
                icon=step_icon(state["status"]),
                **stretch_width(),
            ):
                st.session_state.step = state["number"]
                st.rerun()
        else:
            render_static_step(state)


def render_workbench_header():
    config = st.session_state.project_config or {}
    current = st.session_state.step
    if current <= 0:
        title = "Start or resume a review"
        meta = "Describe a research topic, or resume one of your saved projects."
    else:
        step = WORKFLOW_STEPS[current - 1]
        project_name = config.get("project_name", "Untitled review")
        title = f"Step {step.number}: {step.name}"
        meta = f"{project_name} · {step.description}"

    st.markdown(
        f"""
        <div class="rp-workbench-head">
            <p class="rp-workbench-title">{title}</p>
            <div class="rp-workbench-meta">{meta}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_step_ribbon():
    states = build_step_states(st.session_state.step, finalized_flags())
    cells = []
    for state in states:
        cells.append(
            f"<div class='rp-step-chip {state['status']}'>"
            f"<div class='num'>Step {state['number']} · {state['status'].title()}</div>"
            f"<div class='name'>{state['name']}</div>"
            "</div>"
        )
    st.markdown(f"<div class='rp-step-ribbon'>{''.join(cells)}</div>", unsafe_allow_html=True)


def render_task_card(title: str, body: str):
    st.markdown(
        f"""
        <div class="rp-task-card">
            <h3>{title}</h3>
            <p>{body}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================================
# MAIN APP
# ============================================================================

# Professional header with logo
import base64
from pathlib import Path as PathLib

@st.cache_data(show_spinner=False)
def get_base64_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode()

TAGLINE = "Systematic literature reviews, accelerated by AI."

logo_path = PathLib(__file__).parent / "logo.png"
if logo_path.exists():
    logo_b64 = get_base64_image(str(logo_path))
    st.markdown(f"""
    <div class="rp-header">
        <img src="data:image/png;base64,{logo_b64}" alt="ReviewPilot">
        <div>
            <h1>ReviewPilot</h1>
            <p>{TAGLINE}</p>
        </div>
    </div>
    """, unsafe_allow_html=True)
else:
    st.markdown(f"""
    <div class="rp-header">
        <div>
            <h1>ReviewPilot</h1>
            <p>{TAGLINE}</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

# Canonical step names — referenced by the stepper, panel headers and chat copy.
STEP_NAMES = [step.name for step in WORKFLOW_STEPS]

# Sidebar: workflow navigation (the brand lives in the masthead, not here)
with st.sidebar:
    render_sidebar_workflow()
    cur_step = st.session_state.step

    if cur_step > 0:
        st.divider()
        if st.button("New Project", icon=":material/add:", **stretch_width()):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            init_session_state()
            st.rerun()

    # Current project at a glance
    if st.session_state.project_config:
        st.divider()
        st.markdown('<div class="rp-eyebrow">Project</div>', unsafe_allow_html=True)
        config = st.session_state.project_config
        st.caption(f"**{config.get('project_name', 'N/A')}**")
        st.caption(f"Topic: {config.get('primary_topic', 'N/A')}")
        st.caption(f"Domain: {config.get('domain', 'N/A')}")

render_workbench_header()
render_step_ribbon()

# Main content area with two columns
col_chat, col_control = st.columns([1.65, 0.9])

with col_chat:
    st.subheader("Assistant", anchor=False)
    if st.session_state.step == 0:
        st.caption("Describe a research topic to create a review plan, or resume an existing project from the right panel.")
        render_task_card(
            "A guided review workbench",
            "ReviewPilot keeps the workflow explicit: search setup, screening, full-text retrieval, extraction, and categorization. The assistant helps refine decisions; the action panel controls irreversible or long-running steps.",
        )
    else:
        st.caption("Use chat for refinements; use the action panel for long-running or state-changing steps.")

    # Chat container
    chat_container = st.container(height=500)

    with chat_container:
        display_chat()

    # Chat input — paused on Step 3 (full-text download has no chat role; the
    # "Proceed to Extraction" button drives advancement there).
    _chat_paused = (st.session_state.step == 3)
    user_input = st.chat_input(
        "Chat is paused during full-text download — use the buttons on the right"
        if _chat_paused else "Message the assistant…",
        disabled=_chat_paused,
    )

    if user_input:
        add_message("user", user_input)

        # Process based on current step
        # Handle Step 0: User describes topic to start new project
        if st.session_state.step == 0:
            # User is describing their research topic - start a new project
            st.session_state.step = 1
            st.session_state.project_selected = True

            add_message("assistant", f"Starting new project for: **{user_input}**\n\nGenerating search configuration...")

            with st.spinner("Generating..."):
                config = generate_search_query(user_input)

            config["description"] = user_input
            config["search_queries"] = [{"name": "query1", "query": config.get("search_terms", "")}]
            st.session_state.project_config = config

            response = f"""**Project:** {config.get('project_name', 'N/A')}
**Topic:** {config.get('primary_topic', 'N/A')} ({config.get('domain', 'N/A')})

**Query 1:**
```
{config.get('search_terms', 'N/A')}
```

Tell me how to modify this query, or type **"done"** when satisfied."""
            add_message("assistant", response)
            st.rerun()

        elif st.session_state.step == 1 and st.session_state.step1_finalized:
            # Step 1 is done, waiting for user to proceed
            user_lower = user_input.lower().strip()
            if user_lower in ["proceed", "next", "continue", "yes", "go"]:
                add_message("assistant", "Proceeding to **Step 2: Paper Screening**...")
                st.session_state.step = 2
                st.rerun()
            else:
                add_message("assistant", "Step 1 is complete. Type **\"proceed\"** to continue to Step 2: Paper Screening.")

        elif st.session_state.step == 1 and not st.session_state.step1_finalized:
            config = st.session_state.project_config

            if not config:
                # First message - generate search query
                add_message("assistant", f"Generating search configuration for: **{user_input}**...")

                with st.spinner("Generating..."):
                    config = generate_search_query(user_input)

                config["description"] = user_input
                config["search_queries"] = [{"name": "query1", "query": config.get("search_terms", "")}]
                if "platforms" not in config:
                    config["platforms"] = ["pubmed", "arxiv", "openalex"]
                if "max_results" not in config:
                    config["max_results"] = 100
                if "date_range" not in config:
                    config["date_range"] = {"start": "2020-01-01", "end": ""}
                st.session_state.project_config = config

                response = f"""**Query 1:**
```
{config.get('search_terms', 'N/A')}
```

Tell me how to modify, or type **"done"** when satisfied."""
                add_message("assistant", response)

            else:
                # Use tool_use to detect user intent
                current_queries = config.get("search_queries", [])
                current_query = current_queries[-1]["query"] if current_queries else ""
                conversation_history = get_conversation_history()

                queries_display = "\n".join([f"  Query {i+1}: {q['query']}" for i, q in enumerate(current_queries)])
                context = f"""Current queries:\n{queries_display}\n\nRecent conversation:\n{conversation_history[-1000:]}"""

                with st.spinner("Processing..."):
                    intent = detect_intent_with_tools(user_input,
                        "You are helping a researcher configure Boolean search queries for systematic literature review. Detect what the user wants to do.",
                        STEP1_TOOLS, context)

                action = intent["action"]
                args = intent.get("args", {})

                if action == "modify_query":
                    # Modify current query via LLM
                    with st.spinner("Updating query..."):
                        # Get user preferences
                        preferences = st.session_state.get('query_preferences', {})
                        pref_note = ""
                        if preferences.get('preferred_groups'):
                            pref_note = f"\nUser prefers {preferences['preferred_groups']} AND groups."

                        modify_prompt = f"""Modify this Boolean search query based on user instructions.

**Current query:**
{current_query}

**User's instruction:** {args.get('instruction', user_input)}

**Conversation context:**
{conversation_history[-1500:]}{pref_note}

Apply the changes. Return ONLY the modified query, no explanation. Keep (term1 OR term2) AND (term3 OR term4) format."""

                        try:
                            from openai import OpenAI
                            client = OpenAI(api_key=load_api_key('openai'))
                            response = client.chat.completions.create(
                                model=app_llm_model(),
                                messages=[
                                    {"role": "system", "content": "You modify Boolean search queries. Return only the modified query, no explanation or markdown."},
                                    {"role": "user", "content": modify_prompt}
                                ],
                                temperature=0.3
                            )
                            new_query = response.choices[0].message.content.strip().strip('"\'`\n')
                            if new_query.startswith("```"):
                                lines = new_query.split("\n")
                                new_query = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:]).strip()
                        except Exception as e:
                            new_query = current_query
                            add_message("assistant", f"Error modifying query: {e}")

                    # Update the last query
                    if current_queries:
                        config["search_queries"][-1]["query"] = new_query
                        config["search_terms"] = config["search_queries"][0]["query"]
                        st.session_state.project_config = config

                    # Track user preference for number of AND groups
                    import re as _re
                    and_count = len(_re.split(r'\bAND\b', new_query, flags=_re.IGNORECASE))
                    get_agent_memory().save_preference('preferred_groups', and_count)

                    query_num = len(config.get("search_queries", []))
                    response = f"""**Query {query_num} updated:**
```
{new_query}
```

Continue modifying, or type **"done"** when satisfied."""
                    add_message("assistant", response)

                elif action == "finalize_query":
                    query_count = len(config.get("search_queries", []))
                    add_message("assistant", f"""**Query {query_count} finalized!**

Would you like to add another search query?
- **Yes**: Describe what to search for
- **No** / **"start"**: Begin paper collection""")

                elif action == "add_query":
                    topic = args.get("topic", "")
                    if topic:
                        # Generate a new query for the given topic
                        with st.spinner("Generating new query..."):
                            preferences = st.session_state.get('query_preferences', {})
                            pref_note = ""
                            if preferences.get('preferred_groups'):
                                pref_note = f"\nIMPORTANT: Use exactly {preferences['preferred_groups']} AND groups."

                            new_query_prompt = f"""Generate a Boolean search query for academic papers.

Main research topic: {config.get('description')}
New aspect to search: {topic}{pref_note}

Create a query using (term1 OR term2) AND (term3 OR term4) format.
Return ONLY the query string, nothing else."""

                            try:
                                from openai import OpenAI
                                client = OpenAI(api_key=load_api_key('openai'))
                                response = client.chat.completions.create(
                                    model=app_llm_model(),
                                    messages=[
                                        {"role": "system", "content": "Generate academic Boolean search query. Return only the query."},
                                        {"role": "user", "content": new_query_prompt}
                                    ],
                                    temperature=0.3
                                )
                                new_query = response.choices[0].message.content.strip().strip('"\'`\n')
                                if new_query.startswith("```"):
                                    lines = new_query.split("\n")
                                    new_query = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:]).strip()
                            except:
                                new_query = f'("{topic}")'

                        query_num = len(current_queries) + 1
                        config["search_queries"].append({"name": f"query{query_num}", "query": new_query})
                        st.session_state.project_config = config

                        response = f"""**Query {query_num}:**
```
{new_query}
```

Tell me how to modify, or type **"done"** when satisfied."""
                        add_message("assistant", response)
                    else:
                        add_message("assistant", "What should the new query search for? Describe the keywords or topic.")

                elif action == "start_collection":
                    add_message("assistant", "Starting paper collection...")

                    output_dir = Path("output") / config["project_name"]
                    output_dir.mkdir(parents=True, exist_ok=True)

                    result = collect_with_status(config, output_dir)
                    st.session_state.collected_papers = result["papers"]
                    st.session_state.collection_total = result["total"]
                    save_project_output(config, "collection", result)

                    msg = f"""**Collection Complete**

**Records identified:** {result['total']}
"""
                    for p, c in result.get("platform_stats", {}).items():
                        msg += f"  • {p}: {c}\n"

                    msg += f"""
**Step 1 complete.** Click **Proceed to Step 2** in the panel on the right to start screening."""
                    add_message("assistant", msg)

                    st.session_state.step1_finalized = True
                    st.session_state.collection_done = True

                    # Save to agent memory
                    try:
                        agent_mem = get_agent_memory()
                        agent_mem.save_run_config(config["project_name"], config)
                        for q in config.get("search_queries", []):
                            agent_mem.save_search_query(
                                config["project_name"],
                                config.get("primary_topic", ""),
                                config.get("domain", ""),
                                q.get("query", ""),
                                config.get("platforms", []),
                                result.get("total", 0)
                            )
                    except Exception:
                        pass

                    st.rerun()

                else:
                    add_message("assistant", "I didn't understand. You can:\n- Modify the query (e.g., 'remove the third group')\n- Type **\"done\"** when satisfied\n- Type **\"start\"** to begin collection")

        elif st.session_state.step == 2 and st.session_state.step2_finalized:
            # Step 2 is done, waiting for user to proceed
            user_lower = user_input.lower().strip()
            if user_lower in ["proceed", "next", "continue", "yes", "go"]:
                add_message("assistant", "Proceeding to **Step 3: Full-Text Retrieval**...")
                st.session_state.step = 3
                st.rerun()
            else:
                add_message("assistant", "Step 2 is complete. Click **Proceed to Step 3** in the panel on the right.")

        elif st.session_state.step == 2 and not st.session_state.step2_finalized:
            # Handle Step 2 chat with tool_use intent detection
            config = st.session_state.project_config
            current_prompt = st.session_state.relevance_prompt or ""
            topic = config.get('primary_topic', 'the topic')
            domain = config.get('domain', 'the field')
            conversation_history = get_conversation_history()

            context = f"""Current screening prompt:\n{current_prompt[:1500]}\n\nTopic: {topic}\nDomain: {domain}\n\nRecent conversation:\n{conversation_history[-1000:]}"""

            with st.spinner("Processing..."):
                intent = detect_intent_with_tools(user_input,
                    "You are managing a relevance screening prompt for systematic literature review. Detect what the user wants. For modify_prompt, you must generate the full updated prompt in the updated_prompt parameter.",
                    STEP2_TOOLS, context)

            action = intent["action"]
            args = intent.get("args", {})

            if action == "show_prompt":
                response_text = f"""**Current Screening Prompt:**

---

{current_prompt}

---

**Screening Focus:** {topic} ({domain})

Would you like to modify anything?"""

            elif action == "modify_prompt":
                old_prompt = current_prompt
                updated = args.get("updated_prompt", "")
                if updated:
                    st.session_state.relevance_prompt = updated
                    try:
                        memory = get_memory_manager(config)
                        if memory.short_term:
                            memory.short_term.record_correction(
                                field="screening_prompt",
                                original=old_prompt[:500],
                                revised=updated[:500],
                                reason=user_input
                            )
                    except Exception:
                        pass

                instruction = args.get("instruction", "Updated based on your feedback.")
                response_text = f"""**Screening prompt updated!**

**What changed:** {instruction}

Say **"show me the prompt"** to see the full updated prompt, or click **Finalize Criteria** in the panel on the right when satisfied."""

            elif action == "answer_question":
                response_text = args.get("response", "I'm here to help with the screening prompt. What would you like to know?")

            elif action == "finalize_screening":
                response_text = "Great! Click **'Finalize Prompt'** in the control panel to proceed with the relevance check."

            else:
                response_text = "I didn't understand. You can:\n- Say **\"show me the prompt\"** to view it\n- Tell me what to change\n- Click **\"Finalize Prompt\"** when done"

            add_message("assistant", response_text)

        # ========== STEP 3 CHAT: Paper Collection ==========
        elif st.session_state.step == 3 and st.session_state.get('pdfs_downloaded'):
            # PDFs are downloaded, handle "proceed" commands
            user_lower = user_input.lower().strip()
            if user_lower in ["proceed", "next", "continue", "yes", "go", "extract", "extraction"]:
                add_message("assistant", "Proceeding to **Step 4: Information Extraction**...")
                st.session_state.step3_finalized = True
                st.session_state.step = 4
                st.rerun()
            else:
                papers_with_pdf = len([p for p in st.session_state.relevant_papers if p.get("pdf_downloaded")])
                add_message("assistant", f"PDF download complete ({papers_with_pdf} papers with PDFs). Click **Proceed to Extraction** in the panel on the right.")

        elif st.session_state.step == 3:
            # PDFs not yet downloaded
            add_message("assistant", "Click **Download PDFs** in the panel on the right to start downloading papers.")

        elif st.session_state.step == 4 and not st.session_state.extraction_prompt_finalized:
            # Handle Step 4 chat with tool_use intent detection
            config = st.session_state.project_config
            schema = st.session_state.extraction_schema or {}
            topic = config.get('primary_topic', 'the topic')
            domain = config.get('domain', 'the field')
            conversation_history = get_conversation_history()

            fields_str = "\n".join([f"- {f['name']}: {f['description']} (example: {f['example']})" for f in schema.get("fields", [])])
            context = f"""Extraction schema fields:\n{fields_str}\n\nTopic: {topic}\nDomain: {domain}\n\nRecent conversation:\n{conversation_history[-1000:]}"""

            with st.spinner("Processing..."):
                intent = detect_intent_with_tools(user_input,
                    "You are managing an extraction schema for systematic literature review. Detect what the user wants. For add_field, generate appropriate name/description/example. For modify_field, specify what to change.",
                    STEP4_TOOLS, context)

            action = intent["action"]
            args = intent.get("args", {})

            if action == "show_schema":
                fields_display = "\n".join([f"- **{f['name']}**: {f['description']}\n  _Example: {f['example']}_" for f in schema.get("fields", [])])
                response_text = f"""**Current Extraction Schema:**

{fields_display}

Say **"show prompt"** to see the full extraction prompt, or modify fields as needed."""

            elif action == "show_prompt":
                ext_prompt = st.session_state.get('extraction_prompt_template', '')
                sys_prompt = st.session_state.get('extraction_system_prompt', '')
                response_text = f"""**Extraction System Prompt:**

{sys_prompt[:500]}{'...' if len(sys_prompt) > 500 else ''}

**Extraction Template:**

{ext_prompt[:800]}{'...' if len(ext_prompt) > 800 else ''}

_The full prompt is applied to each paper's PDF content during extraction._"""

            elif action == "add_field":
                new_field = {"name": args.get("name", ""), "description": args.get("description", ""), "example": args.get("example", "")}
                if new_field["name"]:
                    schema["fields"].append(new_field)
                    st.session_state.extraction_schema = schema
                response_text = f"""**Extraction schema updated!**

**Added:** {new_field.get('name', 'unknown')} — {new_field.get('description', '')}

Say **"show me the schema"** to see all fields."""

            elif action == "remove_field":
                field_name = args.get("field_name", "")
                if field_name:
                    schema["fields"] = [f for f in schema.get("fields", []) if f["name"] != field_name]
                    st.session_state.extraction_schema = schema
                response_text = f"""**Extraction schema updated!**

**Removed:** {field_name}

Say **"show me the schema"** to see remaining fields."""

            elif action == "modify_field":
                field_name = args.get("field_name", "")
                for f in schema.get("fields", []):
                    if f["name"] == field_name:
                        if args.get("new_name"):
                            f["name"] = args["new_name"]
                        if args.get("new_description"):
                            f["description"] = args["new_description"]
                        if args.get("new_example"):
                            f["example"] = args["new_example"]
                        break
                st.session_state.extraction_schema = schema
                response_text = f"""**Extraction schema updated!**

**Modified:** {field_name}"""

            elif action == "answer_question":
                response_text = args.get("response", "I'm here to help with the extraction schema. What would you like to know?")

            elif action == "finalize_extraction":
                response_text = "Great! Click **'Finalize Extraction Schema'** in the control panel to proceed."

            else:
                response_text = "I didn't understand. You can:\n- Say **\"show schema\"** to see fields\n- **\"add/remove/modify\"** fields\n- **\"show prompt\"** to see the extraction prompt"

            add_message("assistant", response_text)

        # ========== STEP 5 CHAT: Categorization Assistance ==========
        elif st.session_state.step == 5:
            config = st.session_state.project_config
            schema = st.session_state.extraction_schema or {}
            extraction_results = st.session_state.extraction_results or []
            field_names = [f["name"] for f in schema.get("fields", [])]
            topic = config.get('primary_topic', 'research')

            # Check if user wants to generate categories for a field
            user_lower = user_input.lower()
            generate_for_field = None

            # Detect category generation requests
            if any(phrase in user_lower for phrase in ['generate categories', 'create categories', 'suggest categories', 'categorize']):
                # Try to extract field name
                for field in field_names:
                    if field.lower() in user_lower or field.replace('_', ' ').lower() in user_lower:
                        generate_for_field = field
                        break

            # If generating categories, do it in chat
            if generate_for_field:
                # Gather sample values for this field
                samples = []
                for result in extraction_results[:15]:
                    val = result.get(generate_for_field, "")
                    if val and val != "None" and str(val).strip():
                        samples.append(str(val).strip()[:200])

                if samples:
                    sample_text = "\n".join([f"- {s}" for s in samples[:10]])

                    prompt = f"""Analyze these sample values from the field "{generate_for_field}" and create meaningful categories.

**Sample Values:**
{sample_text}

**Task:** Create 4-8 categories that meaningfully group these values. Consider:
- Semantic similarity
- Research methodology groupings
- Domain-specific taxonomies

Return as a numbered list with brief descriptions:
1. Category Name - Brief description
2. ...

Be specific and use terminology from the samples."""

                    try:
                        from openai import OpenAI
                        client = OpenAI(api_key=load_api_key('openai'))

                        chat_response = client.chat.completions.create(
                            model=app_llm_model(),
                            messages=[
                                {"role": "system", "content": "You are a research analyst creating semantic categories for academic paper data."},
                                {"role": "user", "content": prompt}
                            ],
                            temperature=0.7,
                            max_tokens=600
                        )
                        categories_text = chat_response.choices[0].message.content.strip()

                        # Store in session state for control panel to use
                        st.session_state.categorization_field = generate_for_field
                        st.session_state.chat_suggested_categories = categories_text

                        response_text = f"""**Categories for "{generate_for_field}"**

I've analyzed the sample values and suggest these categories:

{categories_text}

**Next steps:**
1. Review these categories in the control panel
2. Edit if needed (you can add/remove/rename)
3. Click "Confirm Categories" when ready
4. Then "Apply Categorization" to process all papers

Want me to adjust these categories? Just tell me what to change."""

                    except Exception as e:
                        response_text = f"Error generating categories: {e}. Please try again."

                else:
                    response_text = f"No values found for field '{generate_for_field}'. Please check that extraction completed for this field."

            else:
                # Regular conversational response
                # Gather field info with sample values for LLM analysis
                field_samples = {}
                for field in field_names:
                    samples = []
                    for result in extraction_results[:10]:
                        val = result.get(field, "")
                        if val and val != "None" and str(val).strip():
                            samples.append(str(val).strip()[:150])
                    field_samples[field] = samples[:5]

                # Build context
                fields_with_samples = []
                for field, samples in field_samples.items():
                    if samples:
                        sample_str = " | ".join(samples[:3])
                        fields_with_samples.append(f"- **{field}**: {sample_str}")
                    else:
                        fields_with_samples.append(f"- **{field}**: (no values)")

                context = f"""You are ReviewPilot, a research assistant helping with systematic literature review.

**Research Topic:** {topic}
**Total Papers:** {len(extraction_results)}
**Current Step:** Categorization & Analysis

**Available Fields with Sample Values:**
{chr(10).join(fields_with_samples)}

**User Message:** {user_input}

**Your Role:**
- Help the user decide which fields to categorize
- Explain why certain fields are good/bad for categorization
- If they want to generate categories, tell them to say "generate categories for [field_name]"
- Be conversational, helpful, and proactive

**Categorization Guidelines:**
- GOOD fields: model_used, techniques, clinical_task, dataset, methodology (diverse but groupable)
- BAD fields: limitations, future_directions, findings (unique narrative text)
- BAD fields: included, performance (single values or numbers)

Keep response concise and actionable."""

                response_text = ""
                try:
                    from openai import OpenAI
                    client = OpenAI(api_key=load_api_key('openai'))

                    chat_response = client.chat.completions.create(
                        model=app_llm_model(),
                        messages=[
                            {"role": "system", "content": "You are ReviewPilot, a friendly and knowledgeable research assistant. Be conversational, give specific advice, and suggest next steps."},
                            {"role": "user", "content": context}
                        ],
                        temperature=0.7,
                        max_tokens=500
                    )
                    response_text = chat_response.choices[0].message.content.strip()
                except Exception as e:
                    print(f"Step 5 chat LLM error: {e}")

                if not response_text:
                    response_text = f"""I'm here to help with categorization!

**Available Fields** ({len(extraction_results)} papers):
{chr(10).join(['- ' + f for f in field_names])}

**Recommended for categorization:** {', '.join([f for f in field_names if any(k in f.lower() for k in ['model', 'technique', 'task', 'dataset', 'method'])][:3]) or field_names[0]}

Say "generate categories for [field_name]" to get started, or ask me which field would work best for your research."""

            add_message("assistant", response_text)

        st.rerun()

with col_control:
    _cur = st.session_state.step
    st.subheader("Get started" if _cur == 0 else f"Step {_cur} · {STEP_NAMES[_cur - 1]}", anchor=False)

    # ========== STEP 0: Project Selection ==========
    if st.session_state.step == 0:
        existing_projects = st.session_state.get('existing_projects', [])

        # Show existing projects if any
        if existing_projects:
            st.markdown("**Resume existing work**")

            # Project selection dropdown
            project_options = ["-- Select Project --"] + [
                f"{p['name']} ({project_stage_label(p['current_step'])}, {p['modified']})"
                for p in existing_projects
            ]

            selected_idx = st.selectbox(
                "Select a project:",
                range(len(project_options)),
                format_func=lambda i: project_options[i],
                label_visibility="collapsed"
            )

            if selected_idx > 0:
                selected_project = existing_projects[selected_idx - 1]
                project_path = selected_project['path']
                current_step = selected_project['current_step']

                st.markdown(f"**{selected_project['name']}**")

                # Show project info
                config = selected_project.get('config', {})
                topic = config.get('primary_topic', 'Unknown topic')
                st.caption(f"Topic: {topic}")
                st.caption(f"Progress: {project_stage_label(current_step)}")

                # Resume options
                st.markdown("**Resume target**")

                col_r1, col_r2 = st.columns(2)
                with col_r1:
                    if st.button("Continue", type="primary", **stretch_width()):
                        load_existing_project(project_path)
                        add_message("assistant", f"**Resumed project:** {selected_project['name']}\n\nContinuing from Step {st.session_state.step}...")
                        st.rerun()

                with col_r2:
                    resume_step = st.selectbox(
                        "Go to step:",
                        options=[1, 2, 3, 4, 5],
                        index=clamp_resume_step(current_step) - 1,
                        key="resume_step_select"
                    )

                if st.button("Resume selected step", **stretch_width()):
                    load_existing_project(project_path, resume_step=resume_step)
                    add_message("assistant", f"**Resumed project:** {selected_project['name']}\n\nResuming from **Step {resume_step}: {STEP_NAMES[resume_step-1]}**")
                    st.rerun()

                st.divider()

                # Review mode (read-only)
                if st.button("Review results", **stretch_width()):
                    load_existing_project(project_path, resume_step=5)
                    add_message("assistant", f"**Reviewing project:** {selected_project['name']}\n\nYou can view the analysis and results.")
                    st.rerun()

            st.divider()

        # Option to start new project
        st.markdown("**Start a new review**")
        if st.button("New Project", type="primary", icon=":material/add:", **stretch_width()):
            st.session_state.project_selected = True
            st.session_state.step = 1
            st.rerun()
        st.caption("Then describe your research topic in the Assistant to configure the search.")

    # ========== STEP 1: Search Setup ==========
    elif st.session_state.step == 1:

        if not st.session_state.project_config:
            st.info("Describe your research topic in the chat to generate search configuration.")
        else:
            config = st.session_state.project_config

            st.markdown("**Search Platforms**")
            selected_platforms = st.multiselect(
                "Select platforms",
                options=list(PLATFORMS.keys()),
                default=config.get("platforms", ["pubmed", "arxiv", "openalex"]),
                format_func=lambda x: PLATFORMS[x].split(" - ")[0],
                label_visibility="collapsed"
            )
            config["platforms"] = selected_platforms

            st.markdown("**Settings**")
            current_max = config.get("max_results", 100)
            is_unlimited = current_max is None or current_max == 0
            unlimited = st.checkbox("Unlimited results", value=is_unlimited)
            if unlimited:
                config["max_results"] = None
            else:
                default_val = current_max if current_max and current_max > 0 else 100
                config["max_results"] = st.number_input("Max results/platform", 10, 10000, default_val, key="max_results_input")

            st.markdown("**Date Range**")
            col_start, col_end = st.columns(2)
            with col_start:
                config["date_range"] = config.get("date_range", {})
                config["date_range"]["start"] = st.text_input("From", config["date_range"].get("start", "2020-01-01"))
            with col_end:
                config["date_range"]["end"] = st.text_input("To (blank=now)", config["date_range"].get("end", ""))

            st.session_state.project_config = config

            st.divider()

            if st.session_state.step1_finalized:
                total = st.session_state.get('collection_total', 0)
                st.success(f"Collection complete: {total} papers")
                if st.button("Proceed to Step 2", type="primary", **stretch_width()):
                    add_message("assistant", "Proceeding to **Step 2: Paper Screening**...")
                    st.session_state.step = 2
                    st.rerun()

    # ========== STEP 2: PRISMA Screening ==========
    elif st.session_state.step == 2:

        config = st.session_state.project_config
        topic = config.get('primary_topic', 'the topic')
        domain = config.get('domain', 'the field')
        output_dir = Path("output") / config["project_name"]

        # Phase 1: Filtering (auto-run when entering Step 2)
        if st.session_state.get('collection_done') and not st.session_state.get('filtering_done'):
            papers = st.session_state.collected_papers or []

            with st.spinner("Applying date filter and removing duplicates..."):
                filter_result = run_basic_filtering(papers, config.get("date_range", {}))
                st.session_state.filtered_papers = filter_result["papers"]
                st.session_state.after_dedup = filter_result["stats"]["after_dedup"]
                st.session_state.removed_by_date = filter_result["stats"]["removed_by_date"]
                st.session_state.removed_by_dedup = filter_result["stats"]["removed_by_dedup"]
                save_project_output(config, "filtering", filter_result)

            msg = f"""**Filtering Complete**

- Records collected: {len(papers)}
- Removed by date filter: {filter_result['stats']['removed_by_date']}
- Duplicates removed: {filter_result['stats']['removed_by_dedup']}
- **Records for screening: {filter_result['stats']['after_dedup']}**

Now let's configure the relevance screening criteria."""
            add_message("assistant", msg)

            st.session_state.filtering_done = True
            st.rerun()

        # Phase 2: Collection (fallback if not done in Step 1)
        elif not st.session_state.get('collection_done'):
            st.markdown("**Phase 1: Paper Collection**")

            col_plat, col_set = st.columns(2)
            with col_plat:
                st.caption(f"Platforms: {', '.join(config.get('platforms', []))}")
            with col_set:
                st.caption(f"Max: {config.get('max_results', 'unlimited')}/platform")

            if st.button("Start Collection", type="primary", **stretch_width()):
                add_message("assistant", "Starting paper collection from databases...")

                output_dir.mkdir(parents=True, exist_ok=True)

                result = collect_with_status(config, output_dir)
                st.session_state.collected_papers = result["papers"]
                st.session_state.collection_total = result["total"]
                save_project_output(config, "collection", result)

                msg = f"""**Collection Complete**

Records identified from databases: **{result['total']}**
"""
                for p, c in result["platform_stats"].items():
                    msg += f"  • {p}: {c}\n"

                msg += "\nDate filtering and de-duplication will run automatically."
                add_message("assistant", msg)

                st.session_state.collection_done = True
                st.rerun()

        # Phase 3: Relevance Screening (after filtering)
        else:
            papers = st.session_state.filtered_papers

            # Initialize relevance prompt if not set
            if not st.session_state.relevance_prompt:
                description = config.get('description', '')

                # Get memory suggestions from agent memory
                agent_mem = get_agent_memory()
                similar_prompts = agent_mem.find_similar_prompts(topic, domain)
                criteria_suggestions = {"inclusion": [], "exclusion": []}
                if similar_prompts:
                    criteria_suggestions["source_project"] = similar_prompts[0].get("project", "")

                # Build base inclusion criteria
                inclusion_criteria = [
                    f"1. The study involves {topic} or directly related methods/techniques.",
                    f"2. The study is applied to {domain} or closely related domains.",
                    "3. The article is written in English.",
                    "4. The study provides empirical results, methodology, or theoretical framework relevant to the research question."
                ]

                # Add memory-based inclusion criteria if available
                if criteria_suggestions.get("inclusion"):
                    for i, criterion in enumerate(criteria_suggestions["inclusion"][:3], 5):
                        inclusion_criteria.append(f"{i}. {criterion}")

                inclusion_text = "\n".join(inclusion_criteria)

                # Build exclusion keys
                exclusion_keys = [
                    f"- 'not_topic' → Does not discuss {topic} or related methods.",
                    f"- 'not_domain' → Not applied to {domain} or related domains.",
                    "- 'not_english' → The paper is not written in English.",
                    "- 'no_empirical' → No empirical results, methodology, or relevant framework.",
                    "- 'insufficient_information' → Title/abstract does not clearly indicate the required criteria."
                ]

                # Add memory-based exclusion criteria if available
                if criteria_suggestions.get("exclusion"):
                    for criterion in criteria_suggestions["exclusion"][:2]:
                        key = criterion.lower().replace(" ", "_")[:20]
                        exclusion_keys.append(f"- '{key}' → {criterion}")

                exclusion_text = "\n".join(exclusion_keys)

                default_prompt = f"""Screen the paper using PRISMA-style criteria for a review on "{description}".

PRISMA Inclusion Criteria (ALL must be satisfied):
{inclusion_text}

Exclusion Keys (use these exact labels if include=False):
{exclusion_text}

Decision Rules:
- If ALL inclusion criteria are satisfied → return {{"include": true, "exclusion_reasons": []}}
- If ANY criterion fails or is unclear → return {{"include": false, "exclusion_reasons": ["reason1", "reason2"]}}
- Multiple exclusion reasons may appear.
- If the abstract is unavailable, use the title only.
- Do NOT use outside knowledge.
- Output ONLY the JSON object.

Examples:
- Paper about {topic} applied to {domain} → {{"include": true, "exclusion_reasons": []}}
- Paper about {topic} but in unrelated field → {{"include": false, "exclusion_reasons": ["not_domain"]}}
- Paper about unrelated topic in {domain} → {{"include": false, "exclusion_reasons": ["not_topic"]}}"""
                st.session_state.relevance_prompt = default_prompt

            # Show intro message once
            if not st.session_state.step2_intro_shown:
                intro_msg = f"""**Paper Screening: Eligibility Check**

I've generated relevance screening criteria based on your research topic.

**Inclusion Criteria:**
1. Study involves **{topic}** or related methods
2. Applied to **{domain}** or related domains
3. Written in English
4. Provides empirical results or methodology

**Exclusion Keys:** `not_topic`, `not_domain`, `not_english`, `no_empirical`, `insufficient_information`

---

**Refine the criteria by chatting with me.** Examples:
- "Make it more strict about empirical results"
- "Exclude review papers"
- "Add exclusion for animal studies"

Click **"Finalize Criteria"** when satisfied."""
                add_message("assistant", intro_msg)
                st.session_state.step2_intro_shown = True
                st.rerun()

            # Show metrics
            col_m1, col_m2 = st.columns(2)
            col_m1.metric("Papers Collected", st.session_state.get('collection_total', 0))
            col_m2.metric("After Filtering", len(papers))

            st.markdown("**Phase 2: Relevance Screening**")

            with st.expander("View Screening Criteria", expanded=False):
                st.text_area(
                    "Full screening prompt",
                    st.session_state.relevance_prompt,
                    height=200,
                    disabled=True,
                    label_visibility="collapsed"
                )
                st.caption("Refine via chat")

            st.divider()

            if not st.session_state.relevance_prompt_finalized:
                if st.button("Finalize Criteria", type="primary", **stretch_width()):
                    st.session_state.relevance_prompt_finalized = True
                    add_message("assistant", "Screening criteria finalized! Ready to run relevance check.")
                    st.rerun()
                st.info("Chat to refine criteria, then click Finalize")
            else:
                col1, col2 = st.columns(2)

                with col1:
                    if st.button("Run Screening", type="primary", **stretch_width()):
                        add_message("assistant", f"Screening {len(papers)} papers...")

                        progress = st.progress(0)
                        status = st.empty()

                        included = []
                        excluded = []
                        exclusion_counts = {}

                        system_prompt = st.session_state.relevance_prompt

                        for i, paper in enumerate(papers):
                            progress.progress((i + 1) / len(papers))
                            status.text(f"Screening {i+1}/{len(papers)}...")

                            title = paper.get("title", "")
                            abstract = paper.get("abstract", "")[:1000]

                            user_prompt = f"Paper Title: {title}\nPaper Abstract (if available): {abstract if abstract else 'Not available'}"

                            decision = None
                            exclusion_reasons = []

                            for model in [app_llm_model()]:
                                try:
                                    response, _ = query_llm(
                                        text_prompt=user_prompt,
                                        system_prompt=system_prompt,
                                        model=model
                                    )
                                    resp_text = response.strip()
                                    if resp_text.startswith("{"):
                                        result_json = json.loads(resp_text)
                                        decision = result_json.get("include", False)
                                        exclusion_reasons = result_json.get("exclusion_reasons", [])
                                    else:
                                        decision = "true" in resp_text.lower()
                                    break
                                except json.JSONDecodeError:
                                    if '"include": true' in response.lower():
                                        decision = True
                                    elif '"include": false' in response.lower():
                                        decision = False
                                    break
                                except:
                                    continue

                            # If there are exclusion reasons, the paper should be excluded regardless of decision
                            if exclusion_reasons:
                                decision = False

                            paper["screening_decision"] = decision
                            paper["exclusion_reasons"] = exclusion_reasons

                            if decision:
                                included.append(paper)
                            else:
                                excluded.append(paper)
                                for reason in exclusion_reasons:
                                    exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1

                        progress.empty()
                        status.empty()

                        # Assign paper IDs
                        for idx, paper in enumerate(included, start=1):
                            paper["paper_id"] = f"P{idx:04d}"
                        for idx, paper in enumerate(excluded, start=1):
                            paper["paper_id"] = f"E{idx:04d}"

                        st.session_state.relevant_papers = included

                        result = {
                            "included": included,
                            "excluded": excluded,
                            "stats": {
                                "total_screened": len(papers),
                                "included_count": len(included),
                                "excluded_count": len(excluded),
                                "exclusion_breakdown": exclusion_counts
                            }
                        }
                        save_project_output(config, "relevance", result)

                        # Count unique papers per primary reason (first reason only)
                        primary_reason_counts = {}
                        for paper in excluded:
                            reasons = paper.get("exclusion_reasons", [])
                            if reasons:
                                primary = reasons[0]  # First reason is primary
                                primary_reason_counts[primary] = primary_reason_counts.get(primary, 0) + 1

                        msg = f"""**PRISMA: Eligibility Complete**

**Results:**
- Assessed: {len(papers)}
- Excluded: {len(excluded)}
- **Included: {len(included)}**

**Exclusion Breakdown** (by primary reason):
"""
                        if primary_reason_counts:
                            for reason, count in sorted(primary_reason_counts.items(), key=lambda x: -x[1]):
                                msg += f"• {reason}: {count}\n"
                        else:
                            msg += "• No exclusions\n"

                        msg += "\n**Step 2 complete.** Click **Proceed to Step 3** in the panel on the right."
                        add_message("assistant", msg)

                        # Save to agent-level memory
                        try:
                            agent_mem = get_agent_memory()
                            agent_mem.save_screening_prompt(
                                config["project_name"],
                                topic, domain,
                                st.session_state.relevance_prompt,
                                total_screened=len(papers),
                                included_count=len(included)
                            )
                        except Exception:
                            pass

                        st.session_state.step2_finalized = True
                        st.rerun()

                with col2:
                    if st.button("Skip", **stretch_width()):
                        # Assign paper IDs
                        for idx, paper in enumerate(papers, start=1):
                            paper["paper_id"] = f"P{idx:04d}"
                        st.session_state.relevant_papers = papers
                        result = {"relevant": papers, "irrelevant": [], "stats": {"total_checked": 0, "relevant_count": len(papers), "irrelevant_count": 0}}
                        save_project_output(config, "relevance", result)
                        add_message("assistant", f"Skipped relevance check. All {len(papers)} papers included.\n\nMoving to **Step 3: Full-Text Retrieval**")
                        st.session_state.step2_finalized = True
                        st.session_state.step = 3
                        st.rerun()

            # Show "Proceed to Step 3" button when screening is done
            if st.session_state.step2_finalized and st.session_state.relevant_papers:
                st.divider()
                included_count = len(st.session_state.relevant_papers)
                st.success(f"Screening complete: {included_count} papers included")
                if st.button("Proceed to Step 3", type="primary", **stretch_width()):
                    add_message("assistant", "Proceeding to **Step 3: Full-Text Retrieval**...")
                    st.session_state.step = 3
                    st.rerun()

    # ========== STEP 3: Paper Collection ==========
    elif st.session_state.step == 3:

        papers = st.session_state.relevant_papers
        config = st.session_state.project_config
        output_dir = Path("output") / config["project_name"]
        pdf_dir = output_dir / "pdfs"

        # Show intro message once
        if not st.session_state.step3_intro_shown:
            add_message("assistant", f"""**Step 3: Full-Text Retrieval**

Ready to download PDF files for {len(papers)} included papers.

**Download Methods (tried in order):**
1. Publisher direct links
2. Unpaywall API (open access)
3. arXiv/bioRxiv/medRxiv
4. PubMed Central
5. Web search fallback

**Note:** Some journals (e.g., Elsevier, Wiley, Springer) may require institutional access. If PDFs from these publishers fail to download, you may need to manually download them through your library's subscription services.

Provide your email address (required for some APIs) and click **"Download PDFs"** to begin.""")
            st.session_state.step3_intro_shown = True
            st.rerun()

        # Metrics
        col_m1, col_m2 = st.columns(2)
        col_m1.metric("Papers Included", len(papers))
        papers_with_pdf = [p for p in papers if p.get("pdf_downloaded")]
        col_m2.metric("PDFs Downloaded", len(papers_with_pdf))

        st.divider()

        if not st.session_state.pdfs_downloaded:
            email = st.text_input("Email for API access", default_api_email(config),
                                  help="Required by Unpaywall and other APIs")

            if st.button("Download PDFs", type="primary", **stretch_width()):
                email = (email or "").strip()
                if not is_valid_api_email(email):
                    st.error("Enter a real email address for Unpaywall/PubMed API access before downloading PDFs.")
                    st.stop()
                config["api_email"] = email
                config_path = output_dir / "search_conditions.json"
                save_json(str(config_path), config)
                get_agent_memory().save_run_config(config["project_name"], config)

                add_message("assistant", f"""Starting PDF download for {len(papers)} papers...

**Please be patient** - this process typically takes 1-3 minutes per 10 papers, depending on source availability. Each paper requires checking multiple sources sequentially (Unpaywall, PubMed Central, publisher sites, etc.).

**Note:** This starts a fresh download attempt. Previous PDF progress is not reused.

**Reminder:** Papers from subscription-based publishers (Elsevier, Wiley, Springer, etc.) may fail if no open access version is available. These can be manually downloaded from your institution's library resources after the process completes.""")

                archive_path_if_exists(pdf_dir, "previous-run")
                pdf_dir.mkdir(parents=True, exist_ok=True)
                for paper in papers:
                    paper["pdf_downloaded"] = False
                    paper.pop("pdf_path", None)
                    paper.pop("pdf_method", None)

                run_id = new_run_id()
                started_at = datetime.now()
                started_perf = time.perf_counter()
                progress_file = str(pdf_dir / f"download_progress_{run_id}.jsonl")

                downloader = create_pdf_downloader(
                    email=email,
                    output_dir=pdf_dir,
                )
                semantic_cache_file = getattr(downloader, "semantic_scholar_cache_path", None)
                downloader.set_llm_query_func(query_llm)
                if os.getenv("REVIEWPILOT_ENABLE_PDF_WEB_SEARCH") == "1":
                    downloader.enable_web_search(model=app_llm_model())

                progress = st.progress(0)
                status = st.empty()

                def progress_cb(current, total, title):
                    progress.progress(current / total if total else 1.0)
                    status.text(f"Downloaded {current}/{total}: {title[:40]}...")

                results = downloader.download_batch(
                    papers,
                    progress_callback=progress_cb,
                    progress_file=progress_file,
                )
                results["run_id"] = run_id
                results["started_at"] = started_at.isoformat()
                results["completed_at"] = datetime.now().isoformat()
                results["duration_seconds"] = round(time.perf_counter() - started_perf, 3)
                results["progress_file"] = progress_file
                if semantic_cache_file:
                    results["semantic_scholar_cache_file"] = str(semantic_cache_file)

                # Track failed papers for manual download table.
                import re as _re
                raw_failed_by_key = {}
                for failed in results.get("failed_papers", []):
                    for key in (failed.get("doi"), failed.get("title")):
                        if key:
                            raw_failed_by_key[key] = failed
                failed_papers = []
                for paper in papers:
                    if paper.get("pdf_downloaded"):
                        continue
                    safe_title = _re.sub(r'[^\w\s-]', '', (paper.get("title") or "untitled").lower())
                    safe_title = _re.sub(r'\s+', '_', safe_title)[:60]
                    paper_id = paper.get("paper_id", "")
                    expected_file = f"{paper_id}_{safe_title}.pdf" if paper_id else f"{safe_title}.pdf"
                    raw_failure = raw_failed_by_key.get(paper.get("doi")) or raw_failed_by_key.get(paper.get("title"), {})
                    failed_papers.append({
                        "title": paper.get("title", "Unknown"),
                        "doi": paper.get("doi", "N/A"),
                        "url": paper.get("url", "N/A"),
                        "save_as": expected_file,
                        "failure_class": raw_failure.get("failure_class") if isinstance(raw_failure, dict) else None,
                        "failure_detail": raw_failure.get("failure_detail") if isinstance(raw_failure, dict) else None,
                        "failure_classes": raw_failure.get("failure_classes") if isinstance(raw_failure, dict) else [],
                        "error": raw_failure.get("error") if isinstance(raw_failure, dict) else None,
                    })
                results["failed_papers"] = failed_papers

                quality_audit_func = getattr(downloader, "build_quality_audit", None)
                if quality_audit_func:
                    quality_audit = quality_audit_func(papers)
                    quality_audit_file = pdf_dir / "pdf_quality_audit.json"
                    save_json(str(quality_audit_file), quality_audit)
                    results["quality_audit_file"] = str(quality_audit_file)
                    results["quality_audit_summary"] = quality_audit.get("summary", {})

                close_downloader = getattr(downloader, "close", None)
                if close_downloader:
                    close_downloader()

                progress.empty()
                status.empty()

                save_json(str(pdf_dir / "download_report.json"), results)

                xlsx_columns = ['paper_id', 'title', 'authors', 'year', 'source', 'doi', 'abstract', 'url',
                                'screening_decision', 'exclusion_reasons', 'pdf_downloaded', 'pdf_path', 'pdf_method']
                write_jsonl(str(output_dir / "filtered" / "included_papers.jsonl"), papers)
                write_xlsx(str(output_dir / "filtered" / "included_papers.xlsx"), papers, xlsx_columns)

                st.session_state.pdfs_downloaded = True
                st.session_state.pdf_download_stats = results
                st.session_state.relevant_papers = papers

                success_rate = results['success']/results['total']*100 if results['total'] > 0 else 0
                msg = f"""**PDF Download Complete**

**Results:**
- Downloaded: {results['success']}/{results['total']} ({success_rate:.0f}%)
- Failed: {results['failed']}

**Methods Used:**
"""
                for method, count in sorted(results['by_method'].items(), key=lambda x: -x[1]):
                    msg += f"• {method}: {count}\n"

                msg += f"\nPDFs saved to: `{pdf_dir}`"

                # Add failed papers table for manual download
                if results["failed_papers"]:
                    msg += f"\n\n**Papers not downloaded ({len(results['failed_papers'])})** — download manually and save as the filename below:\n\n"
                    msg += "| # | Title | DOI | Save As |\n|---|-------|-----|--------|\n"
                    for idx, fp in enumerate(results["failed_papers"], 1):
                        title_short = fp["title"][:50] + ("..." if len(fp["title"]) > 50 else "")
                        msg += f"| {idx} | {title_short} | {fp['doi']} | `{fp['save_as']}` |\n"
                    msg += f"\nPlace files in: `{pdf_dir}`"

                msg += "\n\n**Click Proceed to Extraction in the panel on the right to continue to Step 4.**"
                add_message("assistant", msg)
                st.rerun()

        else:
            # Show download stats
            stats = st.session_state.pdf_download_stats
            if stats:
                success_rate = stats['success']/stats['total']*100 if stats['total'] > 0 else 0

                st.success(f"Downloaded {stats['success']}/{stats['total']} PDFs ({success_rate:.0f}%)")

                with st.expander("Download Details", expanded=False):
                    st.markdown("**Methods Used:**")
                    for method, count in sorted(stats['by_method'].items(), key=lambda x: -x[1]):
                        st.write(f"• {method}: {count}")

                    if stats['failed'] > 0:
                        st.warning(f"{stats['failed']} papers could not be downloaded")
                        if stats.get('failed_papers'):
                            with st.expander("Failed Papers (for manual download)", expanded=False):
                                failed_df = pd.DataFrame(stats['failed_papers'])
                                st.dataframe(failed_df, **dataframe_width())

            st.divider()

            if st.button("Proceed to Extraction", type="primary", **stretch_width()):
                add_message("assistant", "Moving to **Step 4: Information Extraction**")
                st.session_state.step3_finalized = True
                st.session_state.step = 4
                st.rerun()

        # Show papers preview
        with st.expander("Papers Preview", expanded=False):
            if papers:
                df = pd.DataFrame([{
                    "ID": p.get("paper_id", ""),
                    "Title": p.get("title", "")[:50] + "...",
                    "Year": p.get("year", ""),
                    "PDF": "Yes" if p.get("pdf_downloaded") else "No"
                } for p in papers[:20]])
                st.dataframe(df, **dataframe_width())
                if len(papers) > 20:
                    st.caption(f"Showing 20 of {len(papers)}")

    # ========== STEP 4: Information Extraction ==========
    elif st.session_state.step == 4:

        papers = st.session_state.relevant_papers
        config = st.session_state.project_config
        topic = config.get('primary_topic', 'the topic')
        domain = config.get('domain', 'the field')

        output_dir = Path("output") / config["project_name"]
        extraction_dir = output_dir / "extraction"

        # Metrics
        papers_with_pdf = [p for p in papers if p.get("pdf_downloaded")]
        col_m1, col_m2 = st.columns(2)
        col_m1.metric("Included Papers", len(papers))
        col_m2.metric("With PDFs", len(papers_with_pdf))

        schema_state = schema_workbench_state(
            bool(st.session_state.extraction_schema),
            st.session_state.extraction_prompt_finalized,
        )

        if schema_state["status"] == "missing":
            render_task_card(
                "Generate the extraction schema",
                "ReviewPilot will draft structured fields from the search topic and screening criteria. This can call the configured LLM, so it is now an explicit action instead of an automatic page-load side effect.",
            )
            if st.button(schema_state["primary_action"], type="primary", **stretch_width()):
                with st.status("Generating extraction schema…", expanded=True) as status:
                    relevance_prompt = st.session_state.relevance_prompt or ""
                    sys_prompt, ext_prompt, schema = generate_extraction_prompt_and_schema(
                        config.get("description", ""),
                        topic,
                        domain,
                        relevance_prompt,
                    )
                    st.session_state.extraction_schema = schema
                    st.session_state.extraction_system_prompt = sys_prompt
                    st.session_state.extraction_prompt_template = ext_prompt
                    status.update(label="Extraction schema drafted", state="complete", expanded=False)
                intro_msg = get_extraction_intro_message(topic, domain, st.session_state.extraction_schema)
                add_message("assistant", intro_msg)
                st.session_state.step4_intro_shown = True
                st.rerun()
            st.info("Generate a draft schema first, then refine it in chat before finalizing.")

        else:
            # Show intro message once, but only after a schema exists.
            if not st.session_state.get('step4_intro_shown'):
                intro_msg = get_extraction_intro_message(topic, domain, st.session_state.extraction_schema)
                add_message("assistant", intro_msg)
                st.session_state.step4_intro_shown = True
                st.rerun()

            # Show schema in expander
            with st.expander("Extraction Schema", expanded=False):
                schema = st.session_state.extraction_schema or {}
                for field in schema.get("fields", []):
                    st.markdown(f"**{field['name']}**: {field['description']}")
                    st.caption(f"_Example: {field['example']}_")
                st.caption("Refine via chat")

            st.divider()

            # Phase 1: Schema Finalization
            if not st.session_state.extraction_prompt_finalized:
                if st.button("Finalize Schema", type="primary", **stretch_width()):
                    st.session_state.extraction_prompt_finalized = True
                    # Save schema to file for later resume
                    schema_file = extraction_dir / "extraction_schema.json"
                    extraction_dir.mkdir(parents=True, exist_ok=True)
                    save_json(str(schema_file), st.session_state.extraction_schema)

                    # Save to agent-level memory
                    try:
                        agent_mem = get_agent_memory()
                        agent_mem.save_extraction_schema(
                            config["project_name"],
                            config.get("primary_topic", ""),
                            config.get("domain", ""),
                            st.session_state.extraction_schema
                        )
                    except Exception:
                        pass

                    add_message("assistant", "Extraction schema finalized! Ready to extract data from PDFs.")
                    st.rerun()
                st.info("Chat to refine schema, then click Finalize")

            else:
                # Phase 2: Run Extraction
                if len(papers_with_pdf) == 0:
                    st.warning("No PDFs available. Go back to Step 3 to download PDFs.")
                else:
                    extraction_model = st.selectbox(
                        "Extraction Model",
                        [app_llm_model()],
                        index=0,
                        help="Configured by REVIEWPILOT_LLM_MODEL"
                    )
    
                    if st.button("Run Extraction", type="primary", **stretch_width()):
                        add_message("assistant", f"""Starting information extraction for {len(papers_with_pdf)} papers...
    
    **Please be patient** - extraction typically takes 15-30 seconds per paper as each PDF must be read, processed, and analyzed by the LLM. For {len(papers_with_pdf)} papers, expect approximately {len(papers_with_pdf) * 20 // 60} to {len(papers_with_pdf) * 30 // 60 + 1} minutes total.
    
    Progress will be shown below, and results are saved incrementally (resume-friendly if interrupted).""")
    
                        extraction_dir.mkdir(parents=True, exist_ok=True)
    
                        output_jsonl = extraction_dir / "extraction_results.jsonl"
                        output_xlsx = extraction_dir / "extraction_results.xlsx"
    
                        # Load existing results for resume
                        existing_results = []
                        processed_ids = set()
                        if output_jsonl.exists():
                            existing_results = read_jsonl(str(output_jsonl))
                            processed_ids = {r.get("paper_id") for r in existing_results}
                            if processed_ids:
                                st.info(f"Resuming: {len(processed_ids)} already processed")
    
                        progress = st.progress(0)
                        status = st.empty()
                        results = existing_results.copy()
    
                        schema = st.session_state.extraction_schema or {}
                        metadata_fields = {'title', 'authors', 'year', 'doi', 'paper_id', 'pdf_path', 'source', 'url'}
                        extraction_fields = [f for f in schema.get("fields", []) if f["name"] not in metadata_fields]
                        field_names = [f["name"] for f in extraction_fields]
                        xlsx_columns = ['paper_id', 'title', 'authors', 'year', 'doi', 'source', 'pdf_path', 'extraction_source', 'title_match', 'title_similarity'] + field_names
    
                        try:
                            import fitz
                            pdf_available = True
                        except ImportError:
                            pdf_available = False
                            st.error("PyMuPDF not installed. Run: pip install PyMuPDF")
    
                        if pdf_available:
                            from openai import OpenAI
    
                            client = OpenAI(api_key=load_api_key(provider='openai'))
                            system_prompt = st.session_state.get('extraction_system_prompt', '')
    
                            extraction_prompt_base = f"""Extract the following information from this research paper.
    
    **Field Definitions:**
    """
                            for i, field in enumerate(extraction_fields, 1):
                                extraction_prompt_base += f"""{i}. **{field['name']}**: {field['description']}
       Example: "{field['example']}"
    
    """
                            extraction_prompt_base += """
    **Guidelines:**
    1. Be PRECISE - extract exact names, numbers, and terms
    2. Be CONCISE - keep each field under 50 words
    3. If information is NOT stated, write "None"
    4. Include actual numbers for metrics
    
    **Paper Content:**
    
    """
    
                            field_definitions = {}
                            for field in extraction_fields:
                                field_definitions[field["name"]] = (str, Field(description=field["description"]))
    
                            DynamicPaperInfo = create_model("DynamicPaperInfo", **field_definitions)
    
                            pending_papers = [p for p in papers_with_pdf if p.get("paper_id") not in processed_ids]
    
                            for i, paper in enumerate(pending_papers):
                                progress.progress((i + 1) / len(pending_papers))
                                paper_id = paper.get("paper_id", f"P{i+1:04d}")
                                title_display = paper.get("title", "")[:40]
                                status.text(f"Extracting {i+1}/{len(pending_papers)}: {title_display}...")
    
                                result_row = {
                                    "paper_id": paper_id,
                                    "title": paper.get("title", ""),
                                    "authors": paper.get("authors", ""),
                                    "year": paper.get("year", ""),
                                    "doi": paper.get("doi", ""),
                                    "source": paper.get("source", ""),
                                    "pdf_path": paper.get("pdf_path", ""),
                                    "title_match": "",  # New: whether PDF title matches expected
                                    "title_similarity": "",  # New: Jaccard similarity score
                                    "extraction_source": ""  # New: "pdf" or "web_search"
                                }
    
                                try:
                                    pdf_path = paper.get("pdf_path", "")
                                    expected_title = paper.get("title", "")
    
                                    if pdf_path and Path(pdf_path).exists():
                                        pdf_text = read_pdf(pdf_path)
    
                                        # Verify title match using multiple methods
                                        pdf_title = extract_title_from_pdf_text(pdf_text)
                                        match_status, similarity = check_title_match(expected_title, pdf_title)
                                        result_row["title_similarity"] = f"{similarity:.2f}"
                                        result_row["title_match"] = match_status
                                        result_row["extraction_source"] = "pdf"
    
                                        if pdf_text.startswith("Error"):
                                            for field_name in field_names:
                                                result_row[field_name] = pdf_text
                                            results.append(result_row)
                                            write_jsonl(str(output_jsonl), results)
                                            write_xlsx(str(output_xlsx), results, xlsx_columns)
                                            continue
    
                                        if len(pdf_text) > 80000:
                                            pdf_text = pdf_text[:80000] + "\n... [truncated]"
    
                                        user_prompt = extraction_prompt_base + pdf_text
    
                                        try:
                                            response = client.beta.chat.completions.parse(
                                                model=extraction_model,
                                                messages=[
                                                    {"role": "system", "content": system_prompt},
                                                    {"role": "user", "content": [{"type": "text", "text": user_prompt}]}
                                                ],
                                                response_format=DynamicPaperInfo
                                            )
    
                                            if response.choices[0].message.parsed:
                                                extracted = response.choices[0].message.parsed.model_dump()
                                            else:
                                                content = response.choices[0].message.content or "{}"
                                                extracted = json.loads(content)
    
                                            for field_name in field_names:
                                                result_row[field_name] = extracted.get(field_name, "None")
    
                                        except Exception:
                                            # Fallback without structured outputs - use array format for newer models
                                            response = client.chat.completions.create(
                                                model=extraction_model,
                                                messages=[
                                                    {"role": "system", "content": system_prompt + "\n\nReturn a JSON object."},
                                                    {"role": "user", "content": [{"type": "text", "text": user_prompt}]}
                                                ]
                                            )
                                            resp_text = response.choices[0].message.content or "{}"
                                            if "```json" in resp_text:
                                                resp_text = resp_text.split("```json")[1].split("```")[0]
                                            elif "```" in resp_text:
                                                resp_text = resp_text.split("```")[1].split("```")[0]
                                            extracted = json.loads(resp_text.strip())
    
                                            for field_name in field_names:
                                                result_row[field_name] = extracted.get(field_name, "None")
                                    else:
                                        # PDF not available - use web search fallback
                                        status.text(f"Extracting {i+1}/{len(pending_papers)}: {title_display}... (web search)")
                                        result_row["extraction_source"] = "web_search"
                                        result_row["title_match"] = "N/A"
                                        result_row["title_similarity"] = "N/A"
    
                                        web_extracted = extract_via_web_search(
                                            paper, extraction_fields, topic, domain
                                        )
                                        for field_name in field_names:
                                            result_row[field_name] = web_extracted.get(field_name, "Not available (no PDF)")
    
                                except Exception as e:
                                    error_msg = str(e)[:200]
                                    for field_name in field_names:
                                        result_row[field_name] = f"Error: {error_msg}"
    
                                results.append(result_row)
                                write_jsonl(str(output_jsonl), results)
                                write_xlsx(str(output_xlsx), results, xlsx_columns)
    
                            progress.empty()
                            status.empty()
    
                            st.session_state.extraction_results = results
    
                            msg = f"""**Extraction Complete**
    
    **Results:**
    - Papers processed: {len(results)}
    - Fields extracted: {len(field_names)}
    
    **Output saved to:**
    - `{output_jsonl}`
    - `{output_xlsx}`
    
    Results saved in real-time (resume-friendly)."""
                            add_message("assistant", msg)
                            st.rerun()
    
                st.divider()
    
                # Show extraction results
                if st.session_state.extraction_results:
                    st.markdown("**Extraction Results:**")
                    with st.expander("View Results", expanded=True):
                        df = pd.DataFrame(st.session_state.extraction_results)
                        st.dataframe(df, height=300, **dataframe_width())
    
                if st.button("Proceed to Categorization", type="primary", **stretch_width()):
                    save_json(str(output_dir / "search_conditions.json"), config)
    
                    # Save successful extraction schema to memory for future use
                    try:
                        memory = get_memory_manager(config)
                        schema_data = st.session_state.extraction_schema
                        if schema_data and "fields" in schema_data:
                            # Convert schema to ExtractionSchema for memory
                            import hashlib
                            schema_id = hashlib.md5(f"{topic}_{domain}_{datetime.now().isoformat()}".encode()).hexdigest()[:12]
                            fields = [
                                ExtractionField(
                                    name=f["name"],
                                    description=f["description"],
                                    examples=[f.get("example", "")] if f.get("example") else []
                                )
                                for f in schema_data["fields"]
                            ]
                            extraction_schema = ExtractionSchema(
                                id=schema_id,
                                name=f"{topic} extraction schema",
                                domain=domain,
                                fields=fields,
                                extraction_prompt=st.session_state.extraction_prompt or ""
                            )
                            success_rate = len(st.session_state.extraction_results) / len(papers) if papers else 0
                            memory.learn_from_successful_extraction(extraction_schema, success_rate)
                    except Exception:
                        pass  # Don't fail the workflow if memory save fails
    
                    msg = f"""**Extraction Complete!**
    
    **Summary:**
    - Papers extracted: {len(st.session_state.extraction_results)}
    - Fields extracted: {len(schema.get('fields', []))}
    
    **Output saved to:** `{output_dir}/extraction/`
    
    Moving to **Step 5: Categorization & Analysis** where you can categorize varied field values and view analysis."""
                    add_message("assistant", msg)
                    st.session_state.step4_finalized = True
                    st.session_state.step = 5
                    st.rerun()

        with st.expander("Papers Preview", expanded=False):
            if papers:
                df = pd.DataFrame([{
                    "ID": p.get("paper_id", ""),
                    "Title": p.get("title", "")[:40] + "...",
                    "PDF": "Yes" if p.get("pdf_downloaded") else "No"
                } for p in papers[:15]])
                st.dataframe(df, **dataframe_width())
                if len(papers) > 15:
                    st.caption(f"Showing 15 of {len(papers)}")


# ========== STEP 5: Categorization & Analysis ==========
    elif st.session_state.step == 5:

        config = st.session_state.project_config
        output_dir = Path("output") / config["project_name"]
        categorization_dir = output_dir / "categorization"

        extraction_results = st.session_state.extraction_results

        if not extraction_results:
            st.warning("No extraction results found. Please complete Step 4 first.")
        else:
            # Show intro message once
            if not st.session_state.get('step5_intro_shown'):
                schema = st.session_state.extraction_schema or {}
                field_names = [f["name"] for f in schema.get("fields", [])]
                topic = config.get('primary_topic', 'your research')

                # Recommend a field based on name analysis
                priority_fields = ['model_used', 'llm_judge_model', 'techniques', 'llm_techniques',
                                   'clinical_task', 'dataset', 'data_modality', 'methodology']
                recommended = next((f for f in priority_fields if f in field_names), field_names[0] if field_names else None)

                intro_msg = f"""Great work! You've extracted data from **{len(extraction_results)} papers** on {topic}.

Now let's organize your findings. I can help you **categorize** fields to identify patterns and trends.

**Available fields:** {', '.join(field_names)}

**My recommendation:** Start with **{recommended}** - it typically has diverse values that can be meaningfully grouped.

**How to proceed:**
- Ask me "which field should I categorize?" for detailed analysis
- Say "generate categories for {recommended}" to get started
- Or select a field in the control panel

What would you like to do?"""
                add_message("assistant", intro_msg)
                st.session_state.step5_intro_shown = True
                st.rerun()

            # Metrics
            col_m1, col_m2 = st.columns(2)
            col_m1.metric("Papers Extracted", len(extraction_results))

            schema = st.session_state.extraction_schema or {}
            field_names = [f["name"] for f in schema.get("fields", [])]
            col_m2.metric("Fields", len(field_names))

            st.divider()

            # Field selection for categorization
            if not st.session_state.categorization_done:
                st.markdown("**Select Field to Categorize**")

                selected_field = st.selectbox(
                    "Choose a field with varied values to group into categories:",
                    options=["-- Select --"] + field_names,
                    index=0,
                    key="cat_field_select"
                )

                if selected_field != "-- Select --":
                    st.session_state.categorization_field = selected_field

                    # Get raw values (no splitting) to show samples
                    raw_values = []
                    papers_with_value = 0
                    for result in extraction_results:
                        val = result.get(selected_field, "")
                        if val and val != "None" and str(val).strip():
                            papers_with_value += 1
                            raw_values.append(str(val).strip())

                    unique_raw = list(set(raw_values))

                    if unique_raw:
                        st.markdown(f"**{papers_with_value} papers** have values for this field")

                        # Show sample values (raw, not split)
                        with st.expander("Sample Values", expanded=True):
                            for val in unique_raw[:10]:
                                display_val = val[:100] + "..." if len(val) > 100 else val
                                st.text(f"• {display_val}")
                            if len(unique_raw) > 10:
                                st.caption(f"...and {len(unique_raw) - 10} more unique values")

                        st.divider()

                        # Step 1: Ask user preference
                        st.markdown("**Categorization Mode**")
                        cat_mode = st.radio(
                            "How should each paper be categorized?",
                            options=["single", "multiple"],
                            format_func=lambda x: "Exactly one category per paper" if x == "single" else "One or more categories per paper",
                            index=1,  # Default to "one or more" since it's more flexible
                            key="cat_mode_radio",
                            help="Single: Each paper gets exactly one category. Multiple: Papers can have one or more categories."
                        )
                        st.session_state.cat_mode = cat_mode

                        # Check if categories were generated via chat
                        chat_categories = st.session_state.get('chat_suggested_categories', '')
                        if chat_categories and st.session_state.get('categorization_field') == selected_field:
                            st.info("Categories suggested via chat. You can use these or generate new ones.")
                            with st.expander("Chat-suggested categories", expanded=True):
                                st.markdown(chat_categories)
                            if st.button("Use Chat Categories", type="primary", **stretch_width()):
                                # Parse categories from chat response
                                import re
                                lines = chat_categories.split('\n')
                                parsed_cats = []
                                parsed_descs = {}
                                for line in lines:
                                    # Match patterns like "1. Category Name - Description" or "- Category Name"
                                    match = re.match(r'^\d+\.\s*\*?\*?([^-*]+)\*?\*?\s*[-–]?\s*(.*)', line.strip())
                                    if match:
                                        cat_name = match.group(1).strip()
                                        cat_desc = match.group(2).strip() if match.group(2) else ""
                                        if cat_name:
                                            parsed_cats.append(cat_name)
                                            if cat_desc:
                                                parsed_descs[cat_name] = cat_desc
                                if parsed_cats:
                                    st.session_state.suggested_categories = parsed_cats
                                    st.session_state.category_descriptions = parsed_descs
                                    st.session_state.chat_suggested_categories = ''  # Clear
                                    add_message("assistant", f"Using {len(parsed_cats)} categories from chat. Please review and confirm in the control panel.")
                                    st.rerun()
                            st.divider()

                        # Step 2: Generate categories using LLM
                        if st.button("Generate Categories with AI", type="secondary", **stretch_width()):
                            with st.spinner("Analyzing field values semantically..."):
                                topic = config.get('primary_topic', 'research')
                                domain = config.get('domain', 'general')

                                # Sample values for LLM analysis
                                sample_values = unique_raw[:30]

                                if cat_mode == "single":
                                    prompt = f"""Analyze these values from the "{selected_field}" field in {topic} research papers.

Sample values (each is from one paper):
{chr(10).join([f'- "{v[:200]}"' for v in sample_values])}

Create 5-10 meaningful categories that could classify ALL papers. Each paper should fit into exactly ONE category.

Categories should be:
- Mutually exclusive (no overlap)
- Comprehensive (cover all values)
- Meaningful for research analysis

Return JSON:
{{
    "categories": ["Category1", "Category2", ...],
    "category_descriptions": {{
        "Category1": "Brief description of what belongs here",
        "Category2": "Brief description",
        ...
    }}
}}"""
                                else:
                                    prompt = f"""Analyze these values from the "{selected_field}" field in {topic} research papers.

Sample values (each is from one paper):
{chr(10).join([f'- "{v[:200]}"' for v in sample_values])}

Create 5-10 meaningful categories/tags. Each paper may belong to MULTIPLE categories.

Categories should be:
- Distinct concepts (but can co-occur)
- Cover key themes in the data
- Useful for filtering and analysis

Return JSON:
{{
    "categories": ["Category1", "Category2", ...],
    "category_descriptions": {{
        "Category1": "Brief description of what belongs here",
        "Category2": "Brief description",
        ...
    }}
}}"""

                                try:
                                    response, _ = query_llm(
                                        text_prompt=prompt,
                                        system_prompt="You are a research analyst creating meaningful categories for academic paper data. Focus on semantic understanding, not keyword matching.",
                                        provider="openai",
                                        model=app_llm_model()
                                    )

                                    parsed = extract_json(response)
                                    st.session_state.suggested_categories = parsed.get("categories", [])
                                    st.session_state.category_descriptions = parsed.get("category_descriptions", {})
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error generating categories: {e}")

                        # Show categories if generated - allow user to edit
                        if st.session_state.get('suggested_categories'):
                            st.markdown("**Generated Categories** (edit if needed):")

                            # Allow user to edit categories
                            current_categories = st.session_state.suggested_categories
                            descriptions = st.session_state.get('category_descriptions', {})

                            # Text area for editing categories (one per line)
                            categories_text = st.text_area(
                                "Categories (one per line):",
                                value="\n".join(current_categories),
                                height=150,
                                key="edit_categories_text",
                                help="Edit, add, or remove categories. One category per line."
                            )

                            # Parse edited categories
                            edited_categories = [c.strip() for c in categories_text.split("\n") if c.strip()]

                            if edited_categories != current_categories:
                                st.info(f"You've modified the categories ({len(edited_categories)} categories)")

                            # Show descriptions for reference
                            if descriptions:
                                with st.expander("Category Descriptions (reference)", expanded=False):
                                    for cat, desc in descriptions.items():
                                        if cat in edited_categories:
                                            st.caption(f"**{cat}**: {desc}")

                            st.divider()

                            # Use edited categories for application
                            categories = edited_categories

                            # Step 3: Confirm categories before applying
                            categories_confirmed = st.session_state.get('categories_confirmed', False)

                            if not categories_confirmed:
                                st.markdown("**Review your categories:**")
                                for i, cat in enumerate(categories, 1):
                                    st.write(f"{i}. {cat}")

                                col_confirm, col_regen = st.columns(2)
                                with col_confirm:
                                    if st.button("✓ Confirm Categories", type="primary", **stretch_width()):
                                        st.session_state.categories_confirmed = True
                                        st.session_state.confirmed_categories = categories
                                        add_message("assistant", f"Categories confirmed! Ready to apply categorization to **{selected_field}** with {len(categories)} categories.")
                                        st.rerun()

                                with col_regen:
                                    if st.button("↻ Regenerate", **stretch_width()):
                                        st.session_state.suggested_categories = []
                                        st.session_state.category_descriptions = {}
                                        st.session_state.categories_confirmed = False
                                        st.rerun()

                                st.caption("Review the categories above. Click 'Confirm' when ready, or 'Regenerate' for new suggestions.")

                            else:
                                # Categories are confirmed - show apply button
                                confirmed_cats = st.session_state.get('confirmed_categories', categories)
                                st.success(f"✓ Categories confirmed: {len(confirmed_cats)} categories")
                                for cat in confirmed_cats:
                                    st.write(f"  • {cat}")

                                # Allow editing after confirmation
                                if st.button("✎ Edit Categories", **stretch_width()):
                                    st.session_state.categories_confirmed = False
                                    st.rerun()

                                st.divider()

                                # Step 4: Apply categorization using LLM
                                if st.button("Apply Categorization", type="primary", **stretch_width()):
                                    categorization_dir.mkdir(parents=True, exist_ok=True)
                                    categorized_field = f"{selected_field}_category"

                                    progress = st.progress(0)
                                    status = st.empty()

                                    topic = config.get('primary_topic', 'research')
                                    categories_str = ", ".join(confirmed_cats)

                                    for i, result in enumerate(extraction_results):
                                        progress.progress((i + 1) / len(extraction_results))
                                        original_val = result.get(selected_field, "")

                                        if not original_val or original_val == "None":
                                            result[categorized_field] = "N/A"
                                            continue

                                        status.text(f"Categorizing paper {i+1}/{len(extraction_results)}...")

                                        if cat_mode == "single":
                                            cat_prompt = f"""Categorize this value into exactly ONE of these categories: {categories_str}

Value to categorize:
"{original_val}"

Return ONLY the category name, nothing else."""
                                        else:
                                            cat_prompt = f"""Categorize this value into one or more of these categories: {categories_str}

Value to categorize:
"{original_val}"

Return category names separated by ", " (comma space). Only include categories that clearly apply."""

                                        try:
                                            cat_response, _ = query_llm(
                                                text_prompt=cat_prompt,
                                                system_prompt="You are categorizing research paper data. Return only category names, no explanations.",
                                                provider="openai",
                                                model=app_llm_model()
                                            )
                                            # Clean response
                                            assigned = cat_response.strip().strip('"').strip("'")
                                            result[categorized_field] = assigned if assigned else "Uncategorized"
                                        except:
                                            result[categorized_field] = "Error"

                                    progress.empty()
                                    status.empty()

                                    # Save results
                                    cat_data = {
                                        "field": selected_field,
                                        "mode": cat_mode,
                                        "categories": confirmed_cats,
                                        "descriptions": descriptions
                                    }
                                    save_json(str(categorization_dir / "categorization_mapping.json"), cat_data)
                                    write_jsonl(str(categorization_dir / "categorized_results.jsonl"), extraction_results)
                                    write_xlsx(str(categorization_dir / "categorized_results.xlsx"), extraction_results)

                                    # Save to memory
                                    try:
                                        memory = get_memory_manager(config)
                                        memory.short_term.add_user_feedback(
                                            f"Categorization for {selected_field}",
                                            f"Categories ({cat_mode}): {confirmed_cats}",
                                            accepted=True
                                        )
                                    except:
                                        pass

                                    st.session_state.categorization_done = True
                                    st.session_state.extraction_results = extraction_results
                                    # Reset confirmation state for next field
                                    st.session_state.categories_confirmed = False
                                    st.session_state.suggested_categories = []

                                    add_message("assistant", f"""**Categorization Complete!**

Field: **{selected_field}**
Mode: **{cat_mode}** category per paper
Categories: {len(confirmed_cats)}
Papers categorized: {len(extraction_results)}

New column added: `{categorized_field}`

Results saved to `{categorization_dir}`""")
                                    st.rerun()
                    else:
                        st.info("No values found for this field.")

                st.divider()

                if st.button("Skip Categorization", **stretch_width()):
                    st.session_state.categorization_done = True
                    add_message("assistant", "Categorization skipped. You can proceed to analysis.")
                    st.rerun()

            else:
                # Analysis phase
                st.markdown("**Analysis Summary**")

                # Basic statistics
                df = pd.DataFrame(extraction_results)

                # Show counts for categorical fields
                for field in field_names[:5]:  # First 5 fields
                    if field in df.columns:
                        with st.expander(f"**{field}** Distribution", expanded=False):
                            value_counts = df[field].value_counts().head(10)
                            st.bar_chart(value_counts)

                # If we have categorized data
                cat_field = f"{st.session_state.categorization_field}_category" if st.session_state.categorization_field else None
                if cat_field and cat_field in df.columns:
                    st.markdown(f"**{st.session_state.categorization_field} Categories:**")
                    cat_counts = df[cat_field].value_counts()
                    st.bar_chart(cat_counts)

                st.divider()

                # Results table
                st.markdown("**Full Results:**")
                st.dataframe(df, height=300, **dataframe_width())

                if st.button("Finalize Project", type="primary", **stretch_width()):
                    # Final save
                    save_json(str(output_dir / "search_conditions.json"), config)

                    # Save to long-term memory
                    try:
                        memory = get_memory_manager(config)
                        # Save categorization patterns for future projects
                        if st.session_state.categorization_mapping:
                            memory.long_term.log_successful_action(
                                "categorization",
                                f"Categorized {st.session_state.categorization_field} into {len(st.session_state.get('suggested_categories', []))} categories"
                            )
                    except:
                        pass

                    msg = f"""**Project Complete!**

**Summary:**
- Collected: {st.session_state.get('collection_total', 0)} papers
- After screening: {len(st.session_state.relevant_papers)} papers
- PDFs downloaded: {st.session_state.pdf_download_stats['success'] if st.session_state.pdf_download_stats else 0}
- Extracted: {len(extraction_results)} papers
- Categorization: {"Yes" if st.session_state.categorization_field else "Skipped"}

**Output saved to:** `{output_dir}`
- Papers: `filtered/included_papers.xlsx`
- Extraction: `extraction/extraction_results.xlsx`
- Categorization: `categorization/categorized_results.xlsx`"""
                    add_message("assistant", msg)
                    st.session_state.step5_finalized = True
                    st.rerun()


# Welcome message and project selection
if not st.session_state.messages:
    # Check for existing projects
    existing_projects = list_existing_projects()
    st.session_state.existing_projects = existing_projects

    if existing_projects:
        welcome = """Welcome to **ReviewPilot**.

I found existing projects you can resume from the action panel. To start fresh, describe the review topic in one sentence."""
    else:
        welcome = """Welcome to **ReviewPilot**.

Describe your research topic in one sentence to generate the initial search setup.

_Example: "Survey of using LLM for rare disease diagnosis"_"""
    add_message("assistant", welcome)
    st.rerun()

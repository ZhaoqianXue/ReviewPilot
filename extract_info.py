#!/usr/bin/env python3
"""
Simplified Paper Information Extractor for Healthcare LLM-as-a-Judge Papers
Uses GPT-5.1 to extract structured information from PDFs.
Reads metadata (title, doi, authors) from final.xlsx.
Writes results in real-time to support resume on interruption.
"""

import json
import time
import os
from pathlib import Path
from typing import Dict, Any, Tuple, List
from pydantic import BaseModel, Field
from openai import OpenAI
import pandas as pd

try:
    import fitz
except ImportError:
    print("Error: PyMuPDF required. Install with: pip install PyMuPDF")
    exit(1)

# =============================================================================
# CONFIGURATION
# =============================================================================
MODEL = "gpt-5.1"
SECRETS_FILE = "secrets.txt"
PDF_INPUT_FOLDER = "papers-llm-as-judge"
METADATA_FILE = "llm_as_judge_healthcare_final.xlsx"  # Source for title, doi, authors
OUTPUT_CSV = "llm_as_judge_healthcare_extraction.csv"
OUTPUT_XLSX = "llm_as_judge_healthcare_extraction.xlsx"

# Column order (title, doi, authors from metadata file; rest from extraction)
COLUMNS = [
    'row', 'paper_file', 'title', 'authors', 'doi', 'year',
    'included', 'dataset', 'data_modality', 'clinical_category', 'clinical_task',
    'llm_judge_model', 'judge_content', 'llm_techniques',
    'evaluation_metrics', 'judge_performance', 'limitations', 'potential_direction'
]

# =============================================================================
# SCHEMA FOR STRUCTURED OUTPUT
# =============================================================================
class PaperInfo(BaseModel):
    """Schema for extracted paper information."""
    included: str = Field(description="Yes if included, No (reason) if excluded")
    dataset: str = Field(description="Dataset name(s) used in the study")
    data_modality: str = Field(description="Type of data: text, image, EHR, radiology, pathology, etc.")
    clinical_category: str = Field(description="Category with specific application if Other")
    clinical_task: str = Field(description="Specific clinical task being addressed")
    llm_judge_model: str = Field(description="LLM model(s) used as judge")
    judge_content: str = Field(description="What the LLM judge evaluates")
    llm_techniques: str = Field(description="Techniques to improve judge performance")
    evaluation_metrics: str = Field(description="Metrics used to evaluate the judge")
    judge_performance: str = Field(description="Performance vs ground truth with numbers if available")
    limitations: str = Field(description="Limitations of LLM-as-a-judge discussed in the paper")
    potential_direction: str = Field(description="Future directions for LLM-as-a-judge")

# =============================================================================
# EXTRACTION PROMPT
# =============================================================================
SYSTEM_PROMPT = """You are an expert researcher analyzing healthcare AI papers that use LLMs as judges/evaluators.

Your task is to extract specific information accurately. Follow these guidelines:
1. Be PRECISE - extract exact names, numbers, and terms from the paper
2. Be CONCISE - keep each field under 50 words
3. If information is NOT explicitly stated, write "None"
4. For performance metrics, include actual numbers when available (e.g., "0.85 accuracy, 0.82 F1")
5. Look carefully in Abstract, Methods, Results, and Discussion sections"""

EXTRACTION_PROMPT = """Extract information from this healthcare LLM-as-a-Judge research paper.

## FIELD DEFINITIONS AND EXAMPLES:

1. **included**: Check if paper meets ALL inclusion criteria:
   - Uses LLM (large language model)
   - Uses LLM as a judge/evaluator
   - Clinical/healthcare relevant task
   - Original research (not review/survey/abstract/perspective)
   - Written in English

   If ALL criteria met: "Yes"
   If ANY criterion NOT met: "No (reason)" where reason is ONE of:
   - "No LLM" (paper does not use any LLM)
   - "Not LLM-as-judge" (LLM is used but not as a judge/evaluator)
   - "Not clinical" (task is not clinical/healthcare related)
   - "Review/abstract" (paper is a review, survey, or abstract only)
   - "Not English" (paper is not in English)

2. **dataset**: Name(s) of dataset(s) used
   Example: "MIMIC-IV, PubMedQA" or "Custom dataset of 500 radiology reports"

3. **data_modality**: Type of clinical data
   Options: text, image, EHR, radiology, pathology, genomics, multimodal, other
   Example: "radiology images (CT, MRI)"

4. **clinical_category**: Choose exactly ONE. If "Other applications", MUST specify what:
   - "Diagnosis and Screening" (identifying diseases, screening tests)
   - "Prognosis" (predicting outcomes, survival, disease progression)
   - "Treatment" (treatment selection, drug recommendations, therapy planning)
   - "Post-surgery" (post-operative monitoring, recovery assessment)
   - "Other applications (specify: <what application>)"

5. **clinical_task**: The specific task being performed
   Example: "Evaluating quality of radiology report generation"
   Example: "Assessing accuracy of clinical note summarization"

6. **llm_judge_model**: Which LLM(s) serve as the judge/evaluator
   Examples of models: GPT-5.1, GPT-4.1, GPT-4o, Claude-4-Opus, Claude-3.7-Sonnet, Gemini-2.5-Pro, Gemini-2.5-Flash, DeepSeek, Qwen, Llama-4, Med-PaLM 2, BioMistral, OpenBioLLM
   Example: "GPT-4o, Claude-3.7-Sonnet"

7. **judge_content**: What exactly is the LLM judging?
   Example: "Correctness of generated diagnoses"
   Example: "Quality and completeness of clinical summaries"
   Example: "Factual accuracy of medical QA responses"

8. **llm_techniques**: Methods used to improve judge performance
   Recent techniques include:
   - Prompting: zero-shot, few-shot, chain-of-thought (CoT), tree-of-thought, self-consistency
   - Fine-tuning: SFT, RLHF, DPO, ORPO, LoRA, QLoRA
   - Retrieval: RAG, knowledge grounding
   - Agent-based: multi-agent, tool-use, self-reflection, critique-and-revise
   - Ensemble: multi-model voting, judge aggregation
   Example: "Few-shot CoT prompting, RAG with medical knowledge base"

9. **evaluation_metrics**: How is the judge's performance measured?
   Example: "Cohen's kappa, accuracy, correlation with expert ratings"
   Example: "Precision, recall, F1-score against ground truth"

10. **judge_performance**: Quantitative results comparing LLM judge to ground truth
    Example: "0.78 kappa agreement with physicians, 85% accuracy"
    Example: "Correlation r=0.82 with expert scores"
    Example: "None" (if no quantitative comparison provided)

11. **limitations**: Limitations of LLM-as-a-judge discussed in the paper
    Example: "Hallucination, lack of explainability"
    Example: "Inconsistent scoring across multiple runs, bias toward verbose responses"

12. **potential_direction**: Future research directions for LLM-as-a-judge
    Example: "Fine-tuning on domain-specific data, multi-agent verification"
    Example: "Combining with retrieval for fact-checking, human-AI collaboration"

## PAPER CONTENT:

"""

# =============================================================================
# CORE FUNCTIONS
# =============================================================================

def load_api_key() -> str:
    """Load OpenAI API key from secrets file."""
    with open(SECRETS_FILE) as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) >= 2 and parts[0].strip() == 'openai_key':
                return parts[1].strip()
    raise ValueError("openai_key not found in secrets.txt")


def load_metadata() -> Dict[int, Dict[str, str]]:
    """Load paper metadata (title, doi, authors) from final.xlsx.

    Returns dict mapping row number to metadata.
    """
    if not os.path.exists(METADATA_FILE):
        print(f"Warning: Metadata file not found: {METADATA_FILE}")
        return {}

    df = pd.read_excel(METADATA_FILE)
    metadata = {}

    for _, row in df.iterrows():
        row_num = int(row.get('row', 0))
        metadata[row_num] = {
            'title': str(row.get('title', 'None')),
            'authors': str(row.get('authors', 'None')),
            'doi': str(row.get('doi', 'None')),
            'year': str(row.get('year', 'None'))
        }

    print(f"Loaded metadata for {len(metadata)} papers from {METADATA_FILE}")
    return metadata


def read_pdf(pdf_path: Path) -> str:
    """Extract text from PDF."""
    doc = fitz.open(pdf_path)
    text = []
    for page in doc:
        text.append(page.get_text())
    doc.close()
    return "\n".join(text)


def extract_info(pdf_path: Path, client: OpenAI) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Extract information from a single PDF using GPT-5.1."""
    paper_text = read_pdf(pdf_path)
    prompt = EXTRACTION_PROMPT + paper_text

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [{"type": "text", "text": prompt}]}
    ]

    response = client.beta.chat.completions.parse(
        model=MODEL,
        messages=messages,
        response_format=PaperInfo
    )

    usage = {
        'input_tokens': response.usage.prompt_tokens,
        'output_tokens': response.usage.completion_tokens
    }

    if response.choices[0].message.parsed:
        return response.choices[0].message.parsed.model_dump(), usage

    # Fallback: parse from content
    content = response.choices[0].message.content or "{}"
    return json.loads(content), usage


def get_empty_result() -> Dict[str, str]:
    """Return empty result dict for failed extractions."""
    return {
        'included': 'Error',
        'dataset': 'Error',
        'data_modality': 'Error',
        'clinical_category': 'Error',
        'clinical_task': 'Error',
        'llm_judge_model': 'Error',
        'judge_content': 'Error',
        'llm_techniques': 'Error',
        'evaluation_metrics': 'Error',
        'judge_performance': 'Error',
        'limitations': 'Error',
        'potential_direction': 'Error'
    }


def get_empty_metadata() -> Dict[str, str]:
    """Return empty metadata dict."""
    return {
        'title': 'None',
        'authors': 'None',
        'doi': 'None',
        'year': 'None'
    }


def extract_row_from_filename(filename: str) -> int:
    """Extract row number from PDF filename like 'row11.pdf' -> 11."""
    import re
    match = re.search(r'row(\d+)', filename, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def load_existing_results() -> Tuple[List[Dict], set]:
    """Load existing results from CSV if it exists."""
    if os.path.exists(OUTPUT_CSV):
        df = pd.read_csv(OUTPUT_CSV)
        results = df.to_dict('records')
        processed = set(df['paper_file'].tolist())
        return results, processed
    return [], set()


def save_results(results: List[Dict]) -> None:
    """Save results to CSV and XLSX."""
    if not results:
        return

    df = pd.DataFrame(results)
    # Ensure all columns exist
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = 'None'
    df = df[COLUMNS]

    # Save to CSV
    df.to_csv(OUTPUT_CSV, index=False)

    # Save to XLSX with formatting
    with pd.ExcelWriter(OUTPUT_XLSX, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Extracted Info')

        # Auto-adjust column widths
        worksheet = writer.sheets['Extracted Info']
        for idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
            max_len = min(max_len, 50)
            col_letter = chr(65 + idx) if idx < 26 else 'A' + chr(65 + idx - 26)
            worksheet.column_dimensions[col_letter].width = max_len


def process_papers(input_folder: str) -> None:
    """Process all PDFs in folder with real-time saving."""
    folder = Path(input_folder)
    # Sort by row number numerically (row1, row2, ... row10, row11)
    pdf_files = sorted(folder.glob("*.pdf"), key=lambda f: extract_row_from_filename(f.name))

    if not pdf_files:
        print(f"No PDF files found in: {input_folder}")
        return

    # Load metadata from final.xlsx
    metadata = load_metadata()

    # Load existing results for resume support
    results, processed_files = load_existing_results()
    if processed_files:
        print(f"Resuming: {len(processed_files)} papers already processed")

    # Filter out already processed papers
    pending_files = [f for f in pdf_files if f.name not in processed_files]

    print(f"Found {len(pdf_files)} PDF files total")
    print(f"Papers to process: {len(pending_files)}")
    print("=" * 60)

    if not pending_files:
        print("All papers already processed!")
        return

    client = OpenAI(api_key=load_api_key())
    total_cost = 0.0

    for i, pdf_path in enumerate(pending_files, 1):
        # Extract row number from filename (e.g., "row11.pdf" -> 11)
        row_num = extract_row_from_filename(pdf_path.name)
        print(f"[{i}/{len(pending_files)}] Processing: {pdf_path.name} (row {row_num})")

        # Get metadata for this paper (by row number from filename)
        paper_metadata = metadata.get(row_num, get_empty_metadata())

        try:
            info, usage = extract_info(pdf_path, client)

            # Calculate cost (GPT-5.1: $1.25/1M input, $10/1M output)
            cost = (usage['input_tokens'] * 1.25 + usage['output_tokens'] * 10) / 1_000_000
            total_cost += cost

            row = {
                'row': row_num,
                'paper_file': pdf_path.name,
                **paper_metadata,  # title, authors, doi, year
                **info  # extracted fields
            }
            results.append(row)

            print(f"  Title: {paper_metadata.get('title', 'N/A')[:50]}...")
            print(f"  Included: {info.get('included', 'N/A')}")
            print(f"  Done - Cost: ${cost:.4f}")

        except Exception as e:
            print(f"  Error: {e}")
            results.append({
                'row': row_num,
                'paper_file': pdf_path.name,
                **paper_metadata,
                **get_empty_result()
            })

        # Save after each paper (real-time)
        save_results(results)
        print(f"  Saved ({len(results)} total)")

        time.sleep(0.1)  # Rate limiting

    print("=" * 60)
    print(f"Total papers processed: {len(results)}")
    print(f"Session cost: ${total_cost:.4f}")
    print(f"Saved to: {OUTPUT_CSV}, {OUTPUT_XLSX}")


def main():
    """Main entry point."""
    print("Healthcare LLM-as-a-Judge Paper Information Extractor")
    print(f"Model: {MODEL}")
    print(f"Input folder: {PDF_INPUT_FOLDER}")
    print(f"Metadata file: {METADATA_FILE}")
    print("=" * 60)

    process_papers(PDF_INPUT_FOLDER)

    # Display summary
    print("\nExtracted Fields:")
    print("-" * 40)
    for col in COLUMNS[2:]:
        print(f"  - {col}")


if __name__ == "__main__":
    main()

"""
LLM API Interface Module

This module provides unified interfaces for querying various LLM providers
(OpenAI, Anthropic, Together AI, Google Gemini) for academic paper review tasks.
"""

import json
import re
import ast
import base64
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Union
from openai import OpenAI
from pydantic import BaseModel
import google.generativeai as genai
import anthropic
import config

# Try to import PDF processing libraries
try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("Warning: PyMuPDF not installed. PDF support disabled. Install with: pip install PyMuPDF")

# Pydantic schema for structured JSON output (used by GPT-5 and Gemini)
class RatingSchema(BaseModel):
    """Rating and confidence sub-schema."""
    overall_score: Union[int, float]
    confidence: int

class ReviewSchema(BaseModel):
    """Schema for academic paper review output."""
    summary: str
    strengths: List[str]
    weaknesses: List[str]
    questions: List[str]
    limitations: List[str]
    rating: RatingSchema

# Cost tracking configuration (prices per 1M tokens)
MODEL_COSTS = {
    # OpenAI Models
    "o3": (2.0, 8.0),
    "o3-mini": (1.1, 4.4),
    "o4-mini": (4.0, 16.0),
    "gpt-5.2": (1.75, 14.0),
    "gpt-5.1": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.4),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    
    # Anthropic Models
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-opus-4-5-20251101": (5.0, 25.0),
    
    # Together AI Models
    "Qwen/Qwen2.5-7B-Instruct-Turbo": (0.30, 0.30),
    "Qwen/QwQ-32B": (1.2, 1.2),
    "Qwen/Qwen2.5-VL-72B-Instruct": (1.20, 1.20),
    "meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo": (0.18, 0.18),
    "meta-llama/Llama-3.2-90B-Vision-Instruct-Turbo": (1.20, 1.20),
    "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8": (0.27, 0.85),
    "meta-llama/Llama-4-Scout-17B-16E-Instruct": (0.18, 0.59),

    # Google Gemini Models
    "gemini-3-pro-preview": (2.00, 12.00),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.0-flash-lite": (0.075, 0.30),
    "gemini-2.0-flash": (0.10, 0.40),
}

def estimate_tokens(text: str) -> int:
    """
    Rough estimation: ~4 characters per token for most models.

    Args:
        text: Input text string

    Returns:
        Estimated token count
    """
    return len(text) // 4


def read_pdf_text(pdf_path: Union[str, Path]) -> str:
    """
    Extract text content from a PDF file.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        Extracted text content from all pages

    Raises:
        ImportError: If PyMuPDF is not installed
        FileNotFoundError: If PDF file does not exist
    """
    if not PDF_SUPPORT:
        raise ImportError(
            "PyMuPDF is required for PDF support. "
            "Install it with: pip install PyMuPDF"
        )

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    text_content = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text_content.append(f"--- Page {page_num + 1} ---\n{page.get_text()}")

    doc.close()
    return "\n\n".join(text_content)


def encode_pdf_to_base64(pdf_path: Union[str, Path]) -> str:
    """
    Encode a PDF file to base64 string for API transmission.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        Base64 encoded string of the PDF

    Raises:
        FileNotFoundError: If PDF file does not exist
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    with open(pdf_path, 'rb') as pdf_file:
        return base64.b64encode(pdf_file.read()).decode('utf-8')


def calculate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """
    Calculate API cost based on token usage and model pricing.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        model: Model identifier string

    Returns:
        Total cost in USD

    Note:
        Gemini-2.5-pro and Gemini-3-pro-preview have tiered pricing based on prompt token count.
    """
    total_tokens = input_tokens + output_tokens

    # Gemini-2.5-pro has tiered pricing
    if model in {"gemini-2.5-pro", "gemini-2.5-pro-latest"}:
        if input_tokens > 200_000:
            input_rate, output_rate = 2.50, 15.00  # USD / 1M tokens
        else:
            input_rate, output_rate = 1.25, 10.00
        return (input_tokens / 1_000_000) * input_rate + \
               (output_tokens / 1_000_000) * output_rate

    # Gemini-3-pro-preview has tiered pricing
    if model in {"gemini-3-pro-preview", "gemini-3-pro-preview-latest"}:
        if input_tokens > 200_000:
            input_rate, output_rate = 4.00, 18.00  # USD / 1M tokens
        else:
            input_rate, output_rate = 2.00, 12.00
        return (input_tokens / 1_000_000) * input_rate + \
               (output_tokens / 1_000_000) * output_rate

    # Other models use fixed pricing from MODEL_COSTS
    if model not in MODEL_COSTS:
        # Try to match base model name
        base_model = model.rsplit('-', 1)[0] + '-latest'
        if base_model in MODEL_COSTS:
            model = base_model
        else:
            print(f"Warning: No pricing data for model {model}")
            return 0.0

    input_rate, output_rate = MODEL_COSTS[model]
    return (input_tokens / 1_000_000) * input_rate + \
           (output_tokens / 1_000_000) * output_rate


def load_api_key(provider: str) -> str:
    """
    Load API key for a specific provider from secrets file.

    Args:
        provider: Provider name (openai, anthropic, togetherai, gemini)

    Returns:
        API key string

    Raises:
        ValueError: If provider is unknown or API key not found
        FileNotFoundError: If secrets file does not exist
    """
    provider_key_map = {
        'openai': 'openai_key',
        'anthropic': 'claude_key',
        'togetherai': 'together_key',
        'gemini': 'gemini_key'
    }
    key_identifier = provider_key_map.get(provider.lower())
    if not key_identifier:
        raise ValueError(
            f"Unknown provider: {provider}. "
            f"Available providers: {', '.join(provider_key_map.keys())}"
        )

    try:
        with open(config.SECRETS_FILE_PATH) as f:
            lines = f.readlines()
            for line in lines:
                parts = line.strip().split(',')
                if len(parts) >= 2 and parts[0].strip() == key_identifier:
                    return parts[1].strip()
        raise ValueError(
            f"{key_identifier} not found in {config.SECRETS_FILE_PATH}"
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Secrets file not found: {config.SECRETS_FILE_PATH}"
        )

def query_openai(
    messages_payload: List[Dict[str, Any]],
    model: str
) -> Tuple[str, Dict[str, Any]]:
    """
    Query OpenAI API with messages.

    Args:
        messages_payload: List of message dictionaries in OpenAI format
        model: Model identifier (e.g., 'gpt-4.1', 'o3-mini', 'gpt-5.2')

    Returns:
        Tuple of (response_text, usage_info_dict)
    """
    api_key = load_api_key(provider='openai')
    client = OpenAI(api_key=api_key)

    # GPT-5 and higher models use the new beta.chat.completions.parse() API with structured output
    gpt5_models = ["gpt-5.2", "gpt-5.1", "gpt-5-mini", "gpt-5-nano", "gpt-5"]
    if any(model.startswith(m) for m in gpt5_models):
        try:
            # Use the new Structured Outputs API with Pydantic schema
            response = client.beta.chat.completions.parse(
                model=model,
                messages=messages_payload,
                response_format=ReviewSchema
            )

            # Extract the parsed JSON object
            if response.choices and response.choices[0].message.parsed:
                output_text = response.choices[0].message.parsed.model_dump_json()
            else:
                # Fallback to regular content
                output_text = response.choices[0].message.content or ""
                print(f"Warning: GPT-5 structured output not available, using regular content")

            # Extract usage info
            usage_info = {
                'input_tokens': response.usage.prompt_tokens if response.usage else 0,
                'output_tokens': response.usage.completion_tokens if response.usage else 0,
                'total_tokens': response.usage.total_tokens if response.usage else 0
            }

            return output_text, usage_info

        except Exception as e:
            print(f"Error using GPT-5 structured output API: {e}")
            print("Falling back to standard JSON mode...")
            # Fallback to json_object mode if structured output fails
            response = client.chat.completions.create(
                model=model,
                messages=messages_payload,
                response_format={"type": "json_object"}
            )

            usage_info = {
                'input_tokens': response.usage.prompt_tokens if response.usage else 0,
                'output_tokens': response.usage.completion_tokens if response.usage else 0,
                'total_tokens': response.usage.total_tokens if response.usage else 0
            }

            return response.choices[0].message.content, usage_info

    # Traditional models use Chat Completions API
    response_format_param = {"type": "json_object"}

    # o3 models don't support temperature parameter
    if any(m in model for m in ["o3", "o3-mini", "o4-mini"]):
        response = client.chat.completions.create(
            model=model,
            messages=messages_payload,
            response_format=response_format_param
        )
    else:
        response = client.chat.completions.create(
            model=model,
            messages=messages_payload,
            temperature=config.TEMPERATURE,
            response_format=response_format_param
        )

    usage_info = {
        'input_tokens': response.usage.prompt_tokens if response.usage else 0,
        'output_tokens': response.usage.completion_tokens if response.usage else 0,
        'total_tokens': response.usage.total_tokens if response.usage else 0
    }

    return response.choices[0].message.content, usage_info

def query_claude(
    system_prompt: str,
    user_content_blocks: List[Dict[str, Any]],
    model: str,
    temperature: float
) -> Tuple[str, Dict[str, Any]]:
    """
    Query Anthropic Claude API with messages.

    Args:
        system_prompt: System prompt string
        user_content_blocks: List of content blocks for user message
        model: Model identifier (e.g., 'claude-3-7-sonnet-20250219')
        temperature: Sampling temperature (0.0-1.0)

    Returns:
        Tuple of (response_text, usage_info_dict)
    """
    api_key = load_api_key(provider='anthropic')
    client = anthropic.Anthropic(api_key=api_key)
    anthropic_messages = [{"role": "user", "content": user_content_blocks}]

    response = client.messages.create(
        model=model,
        system=system_prompt,
        messages=anthropic_messages,
        temperature=temperature,
        max_tokens=5000
    )

    usage_info = {
        'input_tokens': response.usage.input_tokens,
        'output_tokens': response.usage.output_tokens,
        'total_tokens': response.usage.input_tokens + response.usage.output_tokens
    }

    for item in response.content:
        if item.type == 'text':
            return item.text, usage_info
    return "", usage_info

def query_togetherai(
    messages_payload: List[Dict[str, Any]],
    model: str,
    temperature: float
) -> Tuple[str, Dict[str, Any]]:
    """
    Query Together AI API with messages.

    Args:
        messages_payload: List of message dictionaries in OpenAI format
        model: Model identifier (e.g., 'meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8')
        temperature: Sampling temperature (0.0-1.0)

    Returns:
        Tuple of (response_text, usage_info_dict)
    """
    api_key = load_api_key(provider='togetherai')
    client = OpenAI(api_key=api_key, base_url="https://api.together.xyz/v1")

    # TogetherAI supports JSON schema enforcement
    response = client.chat.completions.create(
        model=model,
        messages=messages_payload,
        temperature=temperature,
        response_format={
            "type": "json_object",
            "schema": ReviewSchema.model_json_schema()
        }
    )

    usage_info = {
        'input_tokens': response.usage.prompt_tokens if response.usage else 0,
        'output_tokens': response.usage.completion_tokens if response.usage else 0,
        'total_tokens': response.usage.total_tokens if response.usage else 0
    }

    return response.choices[0].message.content, usage_info

def query_gemini(
        prompt_parts: List[Any],
        model_name: str,
        temperature: float
) -> Tuple[str, Dict[str, Any]]:
    """
    Query Google Gemini API with prompt parts.

    Args:
        prompt_parts: List of prompt components (text, images, etc.)
        model_name: Model identifier (e.g., 'gemini-2.5-pro')
        temperature: Sampling temperature (0.0-1.0)

    Returns:
        Tuple of (response_text, usage_info_dict)
    """
    api_key = load_api_key(provider='gemini')
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    # Count input tokens
    input_tokens = model.count_tokens(prompt_parts).total_tokens

    # Use structured JSON output with schema
    generation_config = genai.types.GenerationConfig(
        temperature=temperature,
        response_mime_type='application/json',
        response_schema=ReviewSchema
    )
    response = model.generate_content(prompt_parts, generation_config=generation_config)

    # Access parsed object or fallback to text
    if hasattr(response, 'parsed') and response.parsed:
        # Gemini provides direct access to parsed object
        response_text = json.dumps(response.parsed)
    else:
        # Fallback to regular text
        response_text = response.text

    # Count output tokens
    output_tokens = model.count_tokens(response_text).total_tokens

    usage_info = {
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'total_tokens': input_tokens + output_tokens
    }
    return response_text, usage_info


def query_llm(
    text_prompt: str,
    system_prompt: Optional[str] = None,
    image_base64_data: Optional[str] = None,
    image_mime_type: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Universal LLM query function supporting multiple providers.

    This function provides a unified interface for querying different LLM providers
    (OpenAI, Anthropic, Together AI, Google Gemini) with support for text and images.

    Args:
        text_prompt: The main prompt text to send to the model
        system_prompt: Optional system prompt (defaults to helpful assistant)
        image_base64_data: Optional base64-encoded image data
        image_mime_type: MIME type of the image (e.g., 'image/jpeg', 'image/png')
        model: Model identifier (defaults to config.MODEL)
        provider: Provider name (defaults to config.DEFAULT_PROVIDER)

    Returns:
        Tuple of (response_text, usage_info_dict) where usage_info contains:
            - input_tokens: Number of input tokens
            - output_tokens: Number of output tokens
            - total_tokens: Total tokens used
            - cost_usd: Estimated cost in USD
            - model: Model identifier used

    Raises:
        ValueError: If provider is unsupported
    """
    model = model or config.MODEL
    provider = provider or config.DEFAULT_PROVIDER

    common_system_prompt = system_prompt or "You are a helpful AI assistant."
    if "json" in text_prompt.lower() and "JSON" not in common_system_prompt:
        common_system_prompt += " Ensure your response is a valid JSON object."

    if provider == "openai":
        user_content: List[Dict[str, Any]] = [{"type": "text", "text": text_prompt}]
        if image_base64_data and image_mime_type:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{image_mime_type};base64,{image_base64_data}"}
            })
        messages_payload: List[Dict[str, Any]] = []
        if common_system_prompt:
            messages_payload.append({"role": "system", "content": common_system_prompt})
        messages_payload.append({"role": "user", "content": user_content})
        
        response_text, usage_info = query_openai(messages_payload, model)
        
    elif provider == "anthropic":
        user_content_blocks_list: List[Dict[str, Any]] = [{"type": "text", "text": text_prompt}]
        if image_base64_data and image_mime_type:
            user_content_blocks_list.insert(0, {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_mime_type,
                    "data": image_base64_data,
                },
            })
        response_text, usage_info = query_claude(common_system_prompt, user_content_blocks_list, model, config.TEMPERATURE)
        
    elif provider == "togetherai":
        user_content_tg: List[Dict[str, Any]] = [{"type": "text", "text": text_prompt}]
        if image_base64_data and image_mime_type:
            user_content_tg.append({
                "type": "image_url",
                "image_url": {"url": f"data:{image_mime_type};base64,{image_base64_data}"}
            })
        messages_payload_tg: List[Dict[str, Any]] = []
        if common_system_prompt:
            messages_payload_tg.append({"role": "system", "content": common_system_prompt})
        messages_payload_tg.append({"role": "user", "content": user_content_tg})
        
        response_text, usage_info = query_togetherai(messages_payload_tg, model, config.TEMPERATURE)
        
    elif provider == "gemini":
        prompt_parts = [common_system_prompt, text_prompt]
        if image_base64_data and image_mime_type:
            image_part = {
                "mime_type": image_mime_type,
                "data": image_base64_data
            }
            prompt_parts.insert(1, image_part)
        response_text, usage_info = query_gemini(prompt_parts, model, config.TEMPERATURE)

    else:
        raise ValueError(f"Unsupported provider: {provider}")

    # Calculate cost
    cost = calculate_cost(usage_info['input_tokens'], usage_info['output_tokens'], model)
    usage_info['cost_usd'] = cost
    usage_info['model'] = model

    return response_text, usage_info


def query_llm_with_web_search(
    prompt: str,
    model: str = "gpt-5-mini",
) -> Tuple[str, Dict[str, Any]]:
    """
    Query OpenAI LLM with web search capability enabled.

    Uses OpenAI Responses API with web_search tool for real-time web searching.
    This enables the model to search the web and find current information.

    Args:
        prompt: The prompt to send to the model
        model: Model identifier (default: gpt-5-mini)

    Returns:
        Tuple of (response_text, usage_info_dict)
    """
    api_key = load_api_key(provider='openai')
    client = OpenAI(api_key=api_key)

    # Use OpenAI Responses API with web_search tool
    try:
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            input=prompt
        )

        # Get text directly from output_text attribute
        output_text = getattr(response, 'output_text', None) or ""

        if output_text:
            usage_info = {
                'input_tokens': getattr(getattr(response, 'usage', None), 'input_tokens', 0) or 0,
                'output_tokens': getattr(getattr(response, 'usage', None), 'output_tokens', 0) or 0,
                'total_tokens': 0,
                'model': model
            }
            usage_info['total_tokens'] = usage_info['input_tokens'] + usage_info['output_tokens']
            return output_text, usage_info

    except AttributeError as e:
        # The responses API may not be available in this SDK version
        print(f"Web search API not available (SDK may need update): {e}")
    except Exception as e:
        print(f"Web search API error: {e}")

    # Fallback: Use regular LLM with detailed system prompt about PDF patterns
    try:
        system_prompt = """You are an expert at finding PDF download links for academic papers.
You have extensive knowledge of publisher websites and their PDF URL patterns:

KNOWN PDF URL PATTERNS:
- Lancet/Elsevier: https://www.thelancet.com/action/showPdf?pii={PII_FROM_DOI}
  - Extract PII from DOI like 10.1016/S2589-7500(25)00135-9 → PII is S2589-7500(25)00135-9
  - URL encode parentheses: ( → %28, ) → %29
- Nature: https://www.nature.com/articles/{article_id}.pdf
- Springer: https://link.springer.com/content/pdf/{DOI}.pdf
- IEEE: https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={ARTICLE_NUMBER}
- PMC: https://pmc.ncbi.nlm.nih.gov/articles/PMC{ID}/pdf/
- PLOS: https://journals.plos.org/plosone/article/file?id={DOI}&type=printable
- Frontiers: {article_url}/pdf
- BMC: {article_url}.pdf
- MDPI: https://www.mdpi.com/{path}/pdf
- Cell Press: https://www.cell.com/action/showPdf?pii={PII}

Return ONLY the direct PDF URL. If you cannot determine the URL, return NOT_FOUND."""

        return query_llm(
            text_prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            provider="openai"
        )
    except Exception as e:
        return "NOT_FOUND", {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'model': model}


def find_pdf_url_with_search(
    title: str,
    doi: Optional[str] = None,
    journal: Optional[str] = None,
    model: str = "gpt-5-mini"
) -> Optional[str]:
    """
    Use LLM with web search to find the PDF download URL for a paper.

    Args:
        title: Paper title
        doi: DOI if available
        journal: Journal name if available
        model: Model to use (default: gpt-4.1-mini)

    Returns:
        PDF URL if found, None otherwise
    """
    # Build search prompt
    search_info = f"Title: {title}"
    if doi:
        search_info += f"\nDOI: {doi}"
    if journal:
        search_info += f"\nJournal: {journal}"

    prompt = f"""Find the direct PDF download URL for this academic paper:

{search_info}

CRITICAL: Use your knowledge of publisher PDF URL patterns:

For LANCET journals (DOI starts with 10.1016/S2589 or similar):
- Extract PII from DOI: 10.1016/S2589-7500(25)00135-9 → PII = S2589-7500(25)00135-9
- URL encode parentheses: ( → %28, ) → %29
- PDF URL: https://www.thelancet.com/action/showPdf?pii=S2589-7500%2825%2900135-9

For PMC/PubMed Central:
- Format: https://pmc.ncbi.nlm.nih.gov/articles/PMC{{ID}}/pdf/

For IEEE Access:
- Format: https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={{ARTICLE_NUMBER}}

For other publishers, search and find the direct PDF download link.

Return ONLY the complete URL that will directly download the PDF.
If you cannot determine the URL, return "NOT_FOUND".
No explanation needed - just the URL or NOT_FOUND."""

    try:
        response, _ = query_llm_with_web_search(prompt, model)
        response = response.strip()

        # Clean up response - extract URL if there's extra text
        if '\n' in response:
            response = response.split('\n')[0].strip()

        # Remove any markdown or quotes
        response = response.strip('`"\'')

        # Validate it looks like a URL
        if response.startswith('http'):
            # Check if it looks like a PDF-related URL
            if any(x in response.lower() for x in ['pdf', 'pmc', 'showpdf', 'stamppdf', 'pdfft']):
                return response
            # Accept other URLs too if they're from known publishers
            if any(x in response for x in ['lancet', 'nature.com', 'springer', 'ieee', 'sciencedirect', 'plos', 'frontiers', 'mdpi']):
                return response

    except Exception as e:
        print(f"Error in find_pdf_url_with_search: {e}")

    return None


def review_paper_pdf(
    pdf_path: Union[str, Path],
    venue: str = 'neurips',
    model: Optional[str] = None,
    provider: Optional[str] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Review an academic paper PDF using LLM.

    Args:
        pdf_path: Path to the PDF file
        venue: Conference venue (neurips, iclr, icml, cvpr)
        model: Model identifier (defaults to config.MODEL)
        provider: Provider name (defaults to config.DEFAULT_PROVIDER)

    Returns:
        Tuple of (review_dict, usage_info_dict)

    Raises:
        ImportError: If PyMuPDF is not installed
        FileNotFoundError: If PDF file does not exist
    """
    from prompt import SYSTEM_PROMPT, get_review_prompt

    # Extract text from PDF
    paper_text = read_pdf_text(pdf_path)

    # Get appropriate review prompt for venue
    review_prompt = get_review_prompt(venue)

    # Combine paper text with review instructions
    full_prompt = f"{review_prompt}\n\n## Paper Content:\n\n{paper_text}"

    # Query LLM
    response_text, usage_info = query_llm(
        text_prompt=full_prompt,
        system_prompt=SYSTEM_PROMPT,
        model=model,
        provider=provider
    )

    # Parse JSON response
    review_data = parse_json(response_text)

    # Warn if parsing returned empty dict (likely a parsing failure)
    if not review_data or len(review_data) == 0:
        print(f"Warning: JSON parsing returned empty result. Response text length: {len(response_text) if response_text else 0}")
        if config.VERBOSE and response_text:
            print(f"First 500 chars of response: {response_text[:500]}")

    return review_data, usage_info


def parse_json(response_text: Optional[str], default_value: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Parse JSON from LLM response text with multiple fallback strategies.

    This function attempts to extract valid JSON from potentially malformed
    or markdown-wrapped responses using multiple parsing strategies.

    Args:
        response_text: The raw response text from LLM
        default_value: Default value to return if parsing fails (defaults to empty dict)

    Returns:
        Parsed JSON as a dictionary, or default_value if parsing fails

    Parsing strategies (in order):
        1. Direct JSON parsing
        2. Extract from markdown code blocks (```json ... ```)
        3. Replace Python literals with JSON equivalents (None/True/False)
        4. Use ast.literal_eval for Python-like syntax
        5. Add quotes to unquoted keys
        6. Extract JSON from surrounding text
    """
    if default_value is None:
        default_value = {}
    if not response_text:
        return default_value

    text_to_parse = response_text.strip()

    # Try to extract from markdown code block first
    match_json_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text_to_parse, re.DOTALL)
    if match_json_block:
        text_to_parse = match_json_block.group(1).strip()

    # Strategy 1: Direct JSON parsing
    try:
        return json.loads(text_to_parse)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Replace Python literals with JSON equivalents
    cleaned_text = text_to_parse.replace("None", "null").replace("True", "true").replace("False", "false")
    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError:
        pass

    # Strategy 3: Use ast.literal_eval for Python-like syntax
    try:
        evaluated = ast.literal_eval(cleaned_text)
        if isinstance(evaluated, (dict, list)):
            return json.loads(json.dumps(evaluated))
    except (ValueError, SyntaxError, TypeError, MemoryError):
        pass

    # Strategy 4: Add quotes to unquoted keys and fix trailing commas
    if '{' in cleaned_text and '}' in cleaned_text:
        temp_cleaned_text = re.sub(r'(?<=[{\s,])([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', cleaned_text)
        if "'" in temp_cleaned_text:
            temp_cleaned_text = temp_cleaned_text.replace("'", '"')

        temp_cleaned_text = re.sub(r',\s*([}\]])', r'\1', temp_cleaned_text)
        try:
            return json.loads(temp_cleaned_text)
        except json.JSONDecodeError:
            pass

    # Strategy 5: Extract JSON from surrounding text
    if not match_json_block:
        start_index, end_index = -1, -1
        first_brace, first_bracket = text_to_parse.find('{'), text_to_parse.find('[')

        if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
            start_index, end_index = first_brace, text_to_parse.rfind('}')
        elif first_bracket != -1:
            start_index, end_index = first_bracket, text_to_parse.rfind(']')

        if start_index != -1 and end_index > start_index:
            potential_json = text_to_parse[start_index : end_index+1]
            try:
                cleaned_potential_json = potential_json.replace("None", "null").replace("True", "true").replace("False", "false")
                cleaned_potential_json = re.sub(r'(?<=[{\s,])([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', cleaned_potential_json)
                if "'" in cleaned_potential_json:
                    cleaned_potential_json = cleaned_potential_json.replace("'", '"')
                cleaned_potential_json = re.sub(r',\s*([}\]])', r'\1', cleaned_potential_json)
                return json.loads(cleaned_potential_json)
            except (json.JSONDecodeError, re.error):
                pass

    return default_value
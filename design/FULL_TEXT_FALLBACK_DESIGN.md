# Full-text fallback: manual retrieval handoff and web-assisted extraction

Status: implemented. The downloader (`utils/fast_pdf_downloader.py`, `utils/oa_sources.py`) is unchanged by
these features; both live in the web app (`reviewpilot_core/manual_retrieval.py` and the Extraction Agent's
abstract and web steps).

## Context

ReviewPilot is deployed locally: each user runs the backend on their own machine, with their own IP,
network (campus or VPN) and API keys. The Download agent retrieves PDFs only through legitimate channels
(open-access copies, official APIs, repositories, preprint servers) and does no anti-bot evasion.

On the large benchmark pool (LLM4Biomedical and LLM4HCI showcase queries, 5,136 records), automatic
retrieval reaches about 80%. The remainder is mostly:

- paywalled papers with no open copy indexed anywhere (about 15% of the pool);
- open-access papers whose publisher site challenges programmatic clients (Wiley, ACM DL, Taylor &
  Francis, SAGE, JAMA, OUP) or that need a key the user has not configured;
- non-article records and metadata errors.

A user can usually obtain many of these themselves (library subscription, campus network, interlibrary
loan). An LLM web search that looks for the PDFs themselves was probed and found little: it mostly
returned the same challenged publisher pages, so it is not part of the product.

Two features cover the gap.

---

## Feature A — Manual retrieval handoff

### Goal
After the download step, the user sees every paper ReviewPilot could not obtain, can open it in one
click, and ReviewPilot notices the downloaded file without further action.

### Failure list (download results view)
For each paper not obtained:

- **Reason**, from the downloader's failure classes, in plain words: "paywalled (no open copy found)",
  "publisher site blocks automated download", "no full text found", "not an article record".
- **Links**: `https://doi.org/<doi>`, the publisher landing page, PubMed / PMC pages when present, and
  "Open with my library" when the user configured a link resolver (OpenURL base or EZproxy prefix) in
  settings. ReviewPilot never asks for or stores library credentials.
- A short hint for publishers that preview PDFs in the browser: use the site's "Download PDF" button.

### Detecting the user's download (three layers)

```
(a) Download-folder watch (default on, configurable)
    - Clicking a paper's link marks it "awaiting download" with the click time.
    - The backend polls the download folder (default ~/Downloads) every few seconds while
      any paper is awaiting download.
    - Considers only *.pdf files created after the earliest pending click time; waits until
      the size is stable and ignores partial files (.crdownload, .part, .download).
    - Matches each new file against the awaiting papers with the downloader's identity
      gate (see Matching). Unique confident match -> import; otherwise ask the user.
(b) Drag-and-drop / multi-file upload in the same view, same matching
    (files saved elsewhere, received by e-mail or interlibrary loan).
(c) Manual assignment: "use this file for this paper", for scans and files the matcher
    cannot read. The user's choice is final.
```

### Matching
Reuse the downloader's gate rather than a new matcher:
`FastCascadePDFDownloader._pdf_title_match_result` with `_current_paper_doi` set to the candidate
paper's DOI. It accepts a file when page 1-2 carry the paper's DOI, or the title's distinctive words
appear at the top of page 1 (or page 2 behind a cover sheet); it rejects supplementary files.

- Match only against papers that are awaiting download (layer a) or not yet obtained (layer b). This
  keeps the candidate set small and misassignment unlikely.
- One file matching several papers, or none: show the top candidates and let the user choose.
- A file without extractable text (scan) never auto-matches; it goes to manual assignment.

### Import
- **Copy** the file into the project's `pdfs/` folder, named like downloader output. Never move or
  delete the user's original.
- Record `pdf_downloaded=true`, `pdf_path`, `pdf_method="user_download"`, plus the source
  (`download_watch` / `upload` / `manual_assignment`) and the matching evidence (DOI on page vs title).
- Go through the existing retrieval-retry transaction (`reviewpilot_core/retrieval_retry*.py`) so that
  `included_papers.jsonl`, the download report and the workflow state stay consistent, and extraction
  can start on the new files.
- The download report counts "obtained automatically" and "provided by the user" separately.

### Privacy and settings
- Folder watching reads only PDFs created after a link click, and only while papers are awaiting
  download; files are processed locally and never sent anywhere.
- Settings: download folder path; watching on/off; library resolver URL.

### Acceptance checks
- Click a link, save the PDF into the watched folder: the paper shows as obtained within a few seconds,
  without any other action.
- A supplement, or another paper's PDF, saved into the folder is not imported for the awaiting paper.
- A file matching two awaiting papers prompts the user instead of guessing.
- Upload and manual assignment produce the same records as the folder watch.
- The user's original file is untouched.

---

## Feature B — Web-assisted extraction for papers without full text

Implementation: `agents/extraction_agent.py` (`_extract_without_full_text`), deterministic web checks in
`reviewpilot_core/web_evidence.py`, plan and estimate in `reviewpilot_core/no_full_text.py`, Lead Agent trigger
in `agents/lead_agent.py` (`_run_extraction`); acceptance checks in `tests/test_no_full_text_extraction.py`.

### Goal
Papers without a PDF still get extracted fields, with evidence the user can check, clearly separated
from full-text evidence.

### Policy
- **On by default.** Local users configure their own LLM key, so no extra switch is added.
- Applies only to papers without full text, after Feature A had its chance.
- Before running, show the number of papers and an estimated cost (papers x per-paper estimate). This
  is information, not an opt-in switch.

### Pipeline per paper (same extraction schema as full-text extraction)
```
1. Abstract extraction (free, reproducible)
   - Extract every schema field from the abstract already in the record (PubMed/OpenAlex).
   - Fields found -> evidence tier "abstract"; fields absent -> "not reported in abstract".
2. Web-assisted extraction, only for fields still missing
   - LLM with the provider's web-search tool.
   - A value is accepted only with: source URL + verbatim quote from that page.
   - The page must first be confirmed to be about this paper (DOI or title on the page).
   - Allowed sources: publisher abstract/landing page, trial registries (e.g. ClinicalTrials.gov),
     PMC/Europe PMC records, preprint servers, the paper's own supplementary material.
   - Not allowed: other papers that cite it, reviews, news, blogs, social media, shadow libraries.
   - Accepted values -> evidence tier "web"; marked "needs human check".
3. Anything else stays "not available without full text"; nothing is guessed.
```

### Evidence tiers
`full_text` / `abstract` / `web` / `not_available`, stored per field in the existing evidence
structure (`field_evidence`), displayed distinctly, and counted separately in exports and in the PRISMA
flow ("reports sought but not retrieved" stays a reported number).

### Provider capability
Web search needs a provider tool (OpenAI `web_search`, Anthropic web search, Gemini grounding). If the
user's configured model has none (e.g. most Together-hosted open models), step 2 is skipped and the user
is told so; step 1 still runs.

### Acceptance checks
- A value without a URL and quote, or from a page not confirmed to be the paper, is never stored.
- A value found in a citing paper or a news article is rejected.
- Abstract and web values are never shown as full-text evidence; exports carry the tier.
- With a model lacking web search, extraction completes with abstract evidence and a visible notice.

---

## Related downloader facts the web app can rely on

- Failure classes exposed per paper (`pdf_failure_class`, `pdf_failure_classes`) include
  `publisher_paywalled`, `pdf_endpoint_cloudflare`, `antibot_challenge`, `access_denied`,
  `pmc_not_in_oa_cloud`, `pdf_title_mismatch`; Feature A maps them to the plain reasons above.
- Success methods include `other_version` (a preprint, accepted manuscript or repository copy of the
  same work, verified by DOI or >=80% of the title words on pages 1-2); the UI should label these as
  "other version" rather than the publisher's copy.
- Optional keys users can add in settings (environment variables or local `config.py`):
  `ELSEVIER_API_KEY`, `WILEY_TDM_TOKEN`, `OPENALEX_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`; the contact
  e-mail used for Unpaywall/OpenAlex/Crossref/NCBI polite access comes from `config.EMAIL`.

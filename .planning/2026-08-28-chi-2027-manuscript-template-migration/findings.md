# Findings & Decisions

## Requirements
- Perform a complete template replacement, not an editorial rewrite.
- Preserve all boss-authored content exactly.
- Do not alter `writing/AnonymousSubmission/`.
- Produce a runnable CHI 2027 anonymous manuscript under `writing/CHI2027/`.

## Research Findings
- The authored title is stored in `writing/AnonymousSubmission/main.tex`.
- The authored Introduction and Methods bodies are in their section files.
- Related Works, Results, Discussion, and Appendix are authored structural files and will be copied even when body-empty.
- The AAAI abstract is verbatim AAAI sample prose, not authored ReviewPilot prose.
- The AAAI author/affiliation block and `custom.bib` are template samples.
- CHI 2027 requires `\documentclass[manuscript,review,anonymous]{acmart}` for initial anonymous review.
- The official CHI/ACM sample source is monolithic: its body begins directly after `\maketitle` and contains no `\input{sections/...}` directives.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Use one monolithic `main.tex` in the migrated tree | Supersedes the earlier modular choice because the user requires the official CHI source organization, not merely compilable CHI formatting. |
| Keep the current title string byte-for-byte | It is authored content. |
| Keep section files `01` through `06` byte-for-byte | Exact comparison can prove no authored prose, equations, labels, structural headings, or whitespace were lost. |
| Use no bibliography command until authored citations exist | Prevents AAAI sample references from appearing while preserving all current authored content. |
| Compile in a temporary directory | Verification must not leave build artifacts in the repository. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| No authored abstract exists in the source | Include an empty abstract environment without placeholder prose. |
| The initial migration preserved AAAI modular `\input` directives | Inline the unchanged section sources into the official-style CHI root file and remove the copied `sections/` directory. |

## Resources
- `writing/AnonymousSubmission/`
- `writing/CHI2027/AnonymousSubmission/`
- <https://chi2027.acm.org/chi-publication-formats/>

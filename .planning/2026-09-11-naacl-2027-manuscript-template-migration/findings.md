# Findings & Decisions

## Requirements
- Target venue: NAACL 2027 Main Conference Papers.
- Official call: https://2027.naacl.org/calls/main_conference_papers/
- Preserve the current ReviewPilot title and authored manuscript text exactly.
- Preserve existing `writing/AnonymousSubmission/` and `writing/CHI2027/`.
- Produce a compiling anonymous submission and an Overleaf-ready ZIP under `writing/NAACL2027/`.

## Research Findings
- NAACL 2027 Main Conference submissions go through the ARR 2026 October cycle and use two-way anonymized review.
- The call requires compliance with ARR submission requirements and the official ACL paper templates.
- Long papers allow 8 pages and short papers 4 pages for review; accepted versions receive one additional content page.
- The official ACL review template uses `\documentclass[11pt]{article}` and `\usepackage[review]{acl}`.
- ACL review formatting is A4, two-column, 11-point Times, with page numbers and margin line rulers.
- The official style source is maintained at https://github.com/acl-org/acl-style-files.
- The official template snapshot was cloned at commit `d5adc823ff0f80f98c80405ca0ab66c68e684409`.
- Its distribution contains `acl_latex.tex`, `acl_lualatex.tex`, `acl.sty`, `acl_natbib.bst`, `custom.bib`, `anthology.bib.txt`, `README.md`, and `formatting.md`.
- The official review template is monolithic: manuscript sections are written directly in the main `.tex` file; it does not prescribe a `sections/` directory or `\\input{...}` layout.
- In review mode, `acl.sty` replaces the displayed author block with an anonymous-submission label. The sample bibliography is demonstration content, not ReviewPilot-authored manuscript text.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Use `writing/CHI2027/ReviewPilotSubmission/main.tex` as the migration content authority | It contains the verified current ReviewPilot writing in one file. |
| Create `writing/NAACL2027/AnonymousSubmission/` | Preserve a pristine snapshot of the official ACL template distribution. |
| Create `writing/NAACL2027/ReviewPilotSubmission/main.tex` | Use the official monolithic source structure for the actual migrated paper. |
| Package separate official-template and ReviewPilot ZIPs | Make the upload target unambiguous while retaining the untouched reference template. |

## Current manuscript inventory
- The latest content authority is `writing/CHI2027/ReviewPilotSubmission/main.tex` (SHA-256 `7a10e47b110779f3d8633c020233ceedfa4ca390dee09dfbad426f2d6eee78fa`).
- It contains the current title, an empty abstract, completed Introduction and Methods prose, empty Related Works/Results/Discussion section shells, and an empty appendix.
- No `writing/NAACL2027/` tree existed before this migration.
- Baseline hashes were captured for every file under `writing/AnonymousSubmission/` and `writing/CHI2027/` so those trees can be proven unchanged after implementation.

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
- https://2027.naacl.org/calls/main_conference_papers/
- https://acl-org.github.io/ACLPUB/formatting.html
- https://github.com/acl-org/acl-style-files
- `writing/CHI2027/ReviewPilotSubmission/main.tex`

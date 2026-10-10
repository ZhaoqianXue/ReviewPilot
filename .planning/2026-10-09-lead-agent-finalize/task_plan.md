# Lead Agent finalize

Order agreed with the user: (1) chat, (2) categorization and analysis, (3) memory and export.

## 1. Chat
- `_local_project_memory`, the project state every chat reads, now carries:
  - the confirmed or draft screening guidance and coding rules (status and text);
  - the screening statistics;
  - the PRISMA counts, including full-text exclusions.
  Before this, questions such as "why was this excluded" or "what does the guidance say" had no basis in the prompt.
- Reply language (`reviewpilot_core/chat_language.py`):
  - The language is decided from the message's script and named explicitly: Chinese, Japanese, Korean, otherwise English.
  - It applies to the general, screening and schema chats and to the Search Condition derive and refine replies.
  - research_description is untouched, because a vague "user's language" once produced Spanish.
  - Live probe (gpt-6-luna, 12 calls, under $0.01): English stays English, Chinese gets Chinese on every route.

## 2. Categorization and analysis
Bugs and gaps found:
- The recommended field was `extracted_data`, the whole nested record.
- The plan saw only 30 values.
- Assignment saw category names without their definitions.
- There was no Other route.

Fixes:
- Container fields are skipped.
- The plan sees every paper's value, each shortened to 300 characters, capped at 150 papers.
- Definitions are passed to assignment.
- "Other" is allowed.
- Skill 2.1.0.

Stability on real projects (copies of qa-live-llm-biomedical 40, llm-human-computer-interaction 34, llm-biomedical-2 33):

| Project | ARI between two full runs, before → after | Same label with a fixed plan, before → after |
|---|---|---|
| qa-live-llm-biomedical | .53 → .83 | .95 → .98 |
| llm-human-computer-interaction | .29 → .81 | 1.00 → .94 |
| llm-biomedical-2 | .26 → .88 | .88 → 1.00 |

Script and results are in tmp/evaluation/categorization-stability/; cost about $0.06.

## 3. Memory and export
- Cross-project memory is advisory only. The project chat prompt does not include it, and workflow inputs strip it.
- The export package now includes the extraction instruction with the confirmed coding rules (`prompts/extraction_prompt.json`). The screening instruction with the confirmed guidance was already exported as the relevance prompt.
- PRISMA counts are already computed on request.

## Open (UI, for a worktree session)
- The Categorization header shows "Papers Extracted N" without noting papers excluded at full text.

# Findings
No AGENTS.md found in workspace or ancestor paths. Server not listening on 5602. Existing worktree is dirty; do not reset.

Ordinary chat created project review-project. Natural-language controls not honored: sources defaulted to 3x10; start date empty; model added required arXiv keyword and falsely claimed settings handled. Initial focus repaints chat input causing browser clipboard target mismatch. Correcting via setup canvas before collection.

P1 reproduced: arXiv date variable overwrote offset with 20230101, returned empty success. Fixed variable separation; 4 arXiv tests pass.
P1 reproduced: initial exchange was only synthesized from mutable config; saving setup dropped assistant reply and urban title triggered example starter text. Added immutable initial chat log on creation and restricted example starter inference to protected examples.
Live search attempt 2 running after server restart. Current test project has no Chinese text in JSON/JSONL/Markdown/text files.

Screening model returned wrapped quotations in JSON strings; verifier treated the extra quote delimiters as part of the source and conservatively retained clear surveys. Added exact-match fallback stripping only one balanced delimiter pair; paraphrases remain unverified. Human review exclusion tested separately.

Full extraction completed for 2 PDFs. Verified PDF evidence dialog shows located excerpts on page 1 and labels unavailable anchors not_confirmable. Full text excluded conceptual Industry 5.0 framework; final cohort is 1 urban forecasting paper.
P1: stale extraction outputs also hid the unchanged finalized schema after an inclusion change, forcing unnecessary regeneration. Bound schema confirmation to setup revision and preserve its fields across result-only invalidation; setup changes still invalidate.

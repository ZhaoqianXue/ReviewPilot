# Findings

Prototype shows criteria, supports chat refinement, and finalizes before screening. Current Step 2 canvas only shows counts and sources; screening silently ensures a generated prompt. Current chat persists JSONL locally and cross-project presets already use local SQLite. General chat only receives search configuration, so saved stage artifacts are absent from current project context.

Confirmed omission: no screening chat mutation route; ordinary chat only replies. Added local prompt eligibility fields, review gate, revision check, stale-result invalidation, and artifact-backed project context. SQLite already provides local cross-project presets, so retained it rather than migrating storage.

PDF test investigation correction: the failure is not caused by cached output; the fake HTTP 403 falls through to an unstubbed live curl_cffi request, which successfully downloads a PDF. Isolated that fallback in the browser-policy test only; production downloader unchanged. Browser verified editing, saving, reload persistence, finalizing, and reopening edit mode on an isolated synthetic project.

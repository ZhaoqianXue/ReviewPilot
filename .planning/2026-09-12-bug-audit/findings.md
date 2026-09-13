# Findings
Confirmed and fixed 15 issue groups, documented in docs/internal-testing/bug-audit-2026-09-12.md. Added 16 regression tests. Browser verification additionally caught quoted-chip removal and verified 390-pixel navigation/help.

Preview cache version 2 binds to both schema and source inputs. Invalidated historical caches regenerate on demand; production artifacts are not migrated. Actual external search/PDF/model service quality remains outside this deterministic regression audit.

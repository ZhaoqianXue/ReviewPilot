# Findings
- G01 extended to actual source adapters: PubMed/arXiv flattened nesting and OpenAlex removed Boolean operators. Added shared query tree and retained native query logic.
- G02 native date constraints use source precision; unknown metadata remains reviewable. Source indexing and configured limits still bound coverage.
- G06 decisions are revision-bound local artifacts, using existing review publication recovery; validity derives from input/results fingerprints.
- Historical audit JSON retained; positive probes written to a separate verification JSON.
- Detailed acceptance evidence: docs/internal-testing/prototype-parity-2026-09-12-fixes.md.

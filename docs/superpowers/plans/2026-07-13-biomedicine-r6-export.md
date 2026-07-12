# Biomedicine r6 export-access repair plan

## Goal

Make the existing seven-item export package downloadable from the completed Categorization & Analysis canvas without exposing arbitrary files or local server paths.

## Success criteria

- A completed project renders an `Export Package` section with one control for each existing projected artifact.
- Each control downloads only its allow-listed artifact through a project-scoped backend route with an attachment filename and correct safe content type.
- Unknown export keys, unknown projects, missing artifacts, traversal attempts, and non-files return 404 without revealing local paths.
- The client receives stable export keys/URLs and does not render filesystem paths.
- Existing analysis, categorization, task, privacy, and atomic-output behavior remains unchanged.
- Focused backend/state/frontend tests and the full native suite pass without skips.

## Implementation

1. Add failing state-projection tests for stable export keys and project-scoped relative download URLs while retaining existence metadata.
2. Add failing Web API tests for successful allow-listed downloads plus unknown key, missing file, project isolation, and traversal rejection; implement one explicit allow-list shared with state projection.
3. Add failing frontend contract tests for an accessible `Export Package` section that renders only existing items as download links and never displays `path`.
4. Implement the smallest rendering and route changes; do not add ZIP generation, server-directory browsing, or speculative formats.
5. Run focused tests, the full native suite, `git diff --check`, specification review, and code-quality review.
6. Restart the app and execute a fresh biomedicine r7 from exact setup through browser-visible file download; never continue r6.

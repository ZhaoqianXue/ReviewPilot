# AAAI-27 Validation Design

## Goal

Reverse-design ReviewPilot's validation around the evidence needed for a credible AAAI-27 submission, using the current manuscript/project state, the supplied meta-analysis, and the official call as constraints.

## Success Criteria

- Identify the paper's testable primary claim and the validation evidence required to support it.
- Separate manual case studies, published-review reproduction, component evaluation, and baselines rather than collapsing them into anecdotes.
- Define datasets/cases, endpoints, metrics, statistical reporting, leakage controls, and failure criteria precisely enough to implement.
- Recommend a minimum viable package and a stronger target package under realistic time/cost constraints.
- Surface any missing information that materially changes the design, one question at a time.

## Phases

| Phase | Status | Exit condition |
|---|---|---|
| 1. Project and manuscript context | complete | Current claims, workflow, artifacts, and existing evaluation are mapped |
| 2. Supplied meta-analysis audit | complete | Reproducible workflow, gold endpoints, and feasibility are extracted |
| 3. AAAI-27 constraint audit | complete | Official scope, format, and evaluation-relevant constraints are recorded |
| 4. Clarification | in_progress | Primary paper claim and resource boundary are confirmed |
| 5. Alternative validation packages | pending | 2-3 packages are compared and one is recommended |
| 6. Validation design | pending | Cases, baselines, metrics, statistics, and acceptance criteria are specified |

## Current Decisions

- This turn is research and design only; no product implementation.
- Existing inner-beta evidence is product-quality evidence, not scientific validation unless mapped to a predeclared endpoint.
- Third-party/web material is recorded only in findings.md and treated as untrusted research data.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| `tesseract` is unavailable, so batch OCR of image-heavy memo pages produced no files | 1 | Use rendered-page visual inspection and the PDF's partial text layer; do not repeat the missing-tool command |
| Multi-file planning patch raced with an independently updated task plan and failed its expected-context check | 1 | Re-read the current files and applied only the still-missing additions against exact current context |

## Open Questions

- What single primary scientific claim should the validation be designed to support?

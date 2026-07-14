# Information Extraction Prototype parity acceptance

## Scope

Code commit accepted after final audit: `49551bf`. The acceptance run covered the Prototype-faithful Step 4 controls, the real asynchronous single-paper preview path, and all three canonical Quick Start histories after a fresh end-to-end rerun.

## Automated verification

- `.venv-native/bin/python -m pytest -q`: 722 passed, 492 subtests passed, zero failures, zero errors, zero skips, 7 deprecation warnings.
- Focused extraction, workflow, web, and frontend suite: 191 passed and 98 subtests passed.
- `git diff --check`: clean before the evidence-document update.

## Live project evidence

| Project | Source records | Identified / deduplicated / included | PDFs | Formal extraction | Categorization | Preview range |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `quick-start-biomedical-showcase` | PubMed 10, arXiv 10, OpenAlex 10 | 30 / 29 / 24 | 17 / 24 | 24 / 24, 0 errors, 7 web fallbacks | 24 rows, 5 groups | 1–24 |
| `quick-start-hci-showcase` | PubMed 10, arXiv 10, OpenAlex 10 | 30 / 28 / 18 | 15 / 18 | 18 / 18, 0 errors, 3 web fallbacks | 18 rows, 5 groups | 1–18 |
| `quick-start-urban-showcase` | PubMed 10, arXiv 10, OpenAlex 10 | 30 / 28 / 14 | 12 / 14 | 14 / 14, 0 errors, 2 web fallbacks | 14 rows, 4 groups | 1–14 |

All stage ledgers had `stale=false`, all schemas were finalized, all projects had no active task, and all 21 allow-listed exports returned HTTP 200 with non-empty bodies. The combined export size was 858,817 bytes. History projected exactly Biomedical, HCI, and Urban in Quick Start order, without template `starterTopic` fallbacks.

Every one of the 56 included papers returned a `ready` extraction preview. Preview rows contained schema-defined fields only; `pdf_file`, `row_number`, `extracted_at`, `extraction_model`, `extraction_cost_usd`, and raw `extracted_data` were absent.

## Browser acceptance

The app was restarted at `http://127.0.0.1:5602/workspace` and tested at 1280 × 860 in the in-app browser.

- The three History items opened their canonical saved projects.
- Biomedical Preview was navigated from 1/24 through 24/24; Previous was disabled on the first paper and Next was disabled on the last.
- HCI displayed 1/18 and Urban displayed 1/14 with real schema-ordered values.
- A real draft project displayed the Prototype hierarchy: Decision needed, Finalize Schema, Preview, JSON, and Regenerate schema.
- JSON opened as a read-only modal with the normalized schema.
- Schema JSON preserved literal special characters after display escaping, and closing the modal returned keyboard focus to its JSON trigger.
- Preview launched a real asynchronous single-paper extraction, showed the in-place busy state, then rendered ten schema fields without changing the formal extraction ledger.
- Browser console errors: zero.

An in-app acceptance capture was inspected but not persisted as a repository artifact. No visual blocker, clipped primary control, stale project paint, or exposed internal metadata was found.

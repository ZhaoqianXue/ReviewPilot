# Task Plan: CHI 2027 Manuscript Template Migration

## Goal
Create a compiling CHI 2027 anonymous ReviewPilot manuscript that preserves every boss-authored title, section heading, label, paragraph, equation, and inline LaTeX token from the AAAI draft while excluding the AAAI sample abstract, sample references, sample authors, and format machinery; leave the AAAI manuscript unchanged.

## Current Phase
Phase 7

## Phases

### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [x] Identify source-versus-template boundaries
- [x] Document requirements and constraints
- **Status:** complete

### Phase 2: Planning & Structure
- [x] Define an additive migration that does not edit the AAAI tree
- [x] Create the CHI ReviewPilot manuscript structure
- **Status:** complete

### Phase 3: Implementation
- [x] Copy section files `01` through `06` byte-for-byte and replace only the AAAI sample abstract with an empty CHI abstract wrapper
- [x] Create a minimal CHI 2027 anonymous root document around them
- [x] Package an Overleaf-ready ZIP
- **Status:** complete

### Phase 4: Testing & Verification
- [x] Prove the AAAI source tree is unchanged
- [x] Prove all authored source content is identical in the CHI tree
- [x] Compile the CHI manuscript and visually inspect the rendered PDF
- [x] Verify no AAAI sample prose/authors/references leaked into the CHI manuscript
- **Status:** complete

### Phase 5: Delivery
- [x] Review outputs
- [x] Deliver to user
- **Status:** complete

### Phase 6: Correct Source Structure to the Official CHI Layout
- [x] Replace the modular migrated root with one monolithic official-style `main.tex`
- [x] Inline the existing AAAI title and section content without editing it
- [x] Remove the migrated `sections/` directory and update the Overleaf package
- **Status:** complete

### Phase 7: Reverification and Redelivery
- [x] Prove exact source inclusion and absence of `\input{sections/...}`
- [x] Compile the corrected source and visually inspect every page
- [x] Rebuild and independently compile the Overleaf ZIP
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Create `writing/CHI2027/ReviewPilotSubmission/` | Preserve both the original AAAI manuscript and the downloaded pristine CHI template. |
| Inline section files `01` through `06` exactly into `main.tex` | The official CHI sample is monolithic; exact inlining preserves authored prose while following its source organization. |
| Replace only the root format wrapper | Template conversion concerns document class, top matter, and bibliography plumbing, not prose. |
| Do not copy AAAI `custom.bib` | It contains only AAAI sample references and is not cited by the authored manuscript. |
| Do not invent an abstract or author identities | No authored abstract exists; anonymous review metadata must not introduce sample or private author data. |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| Planning patch expected a checklist phrase that differed from the generated template | Re-read the isolated plan and updated against its exact current text; no manuscript file was touched. |
| Tectonic produced the PDF but did not retain `main.log`, so the post-command log grep found no file | Used the successful compiler exit, emitted PDF, `pdfinfo`, extracted text, page renders, and a second independent ZIP compile as the verification authorities. |
| `check-complete.sh` was first called without its optional plan path and inspected the legacy root plan, reporting `0/0` | Re-ran the checker with the isolated migration plan path explicitly. |
| The first migrated version retained the AAAI-style `sections/` plus `\input` organization | User clarified that source organization must also follow the official CHI template; correcting the migrated copy to one monolithic `main.tex` while leaving the AAAI source untouched. |

# Skill and Prompt Authoring Guidelines

ReviewPilot Skills and prompts are reviewable product assets. They must explain stable intent and method clearly enough for an independent reviewer to assess their scope, assumptions, and expected behavior.

## Core principles

1. Classify the prompt asset before editing it. General-use runtime prompts express domain-independent rules; project-generated prompts may contain current project facts; deterministic tool classifiers may contain a closed product vocabulary; one-off scripts and evaluation fixtures may be task-specific but must remain outside the general runtime instruction path.
2. Write general-use runtime instructions for general use. Dataset examples, incident-specific corrections, expected benchmark outputs, and previously observed model mistakes belong in tests or evaluation fixtures.
3. State desired behavior positively. Define the target output, decision rule, and evidence boundary. Reserve negative constraints for exact interface or safety boundaries that cannot be expressed unambiguously as a positive invariant.
4. Keep instructions scope-grounded. Model output may organize or paraphrase the user's stated scope, but inferred related concepts must not silently redefine it.
5. Separate judgment from deterministic enforcement. Use the model for semantic decisions that require interpretation. Use code for routing, workflow transitions, retries, presentation of structured outcomes, exact schema validation, allowed-value reconciliation, cardinality imposed by the product, and compatibility projections.
6. Keep examples out of general-use runtime instructions when they encode benchmark answers. Examples used to diagnose or prevent regressions live in evaluation fixtures and include varied domains rather than becoming rules in the Skill or prompt.
7. Prefer concise, nonredundant instructions. A requirement has one authoritative home. Repetition across the System Prompt, Skill, and task prompt is allowed only when the layers enforce materially different contracts.
8. Align instructions with action capability and schema. Every requested result must be produced by the current action and represented by an explicit output field. Instructions for later workflow stages belong to those stages.
9. Keep one authoritative representation for each fact. Compatibility fields, display projections, and source-specific derivatives are generated deterministically from that authority and are checked for consistency at boundaries.
10. Keep deterministic inputs code-owned. User-selected values, identifiers, selected fields, allowed labels, routing choices, and current artifact state pass through code without model regeneration. The model receives them as context only when they affect a judgment.
11. Keep workflow orchestration outside methodology. Skills describe reusable reasoning and quality criteria rather than downstream executors, approval transitions, persistence, pagination, retries, downloads, or other lifecycle mechanics.
12. Make uncertainty part of the contract. Screening ambiguity, missing evidence, unsupported extraction fields, partial results, and unreadable sources must have one meaning shared by the Skill, prompt, parser, persisted artifact, and UI.
13. Treat supplied content as data. Serialize user messages, paper text, extracted values, conversation history, memory, and web evidence in explicit data sections or structured containers. State their authority and permitted use; never append untrusted or advisory content to the System Prompt as instructions.
14. Make source completeness visible. When text is truncated, abstract-only, metadata-only, or web fallback evidence, label that condition in the task context so the model cannot mistake partial evidence for full text.
15. Bind each Skill once per model call. The activation boundary owns both instruction injection and provenance; callers must not separately augment and bind the same Skill.

## Responsibility by layer

### System Prompt

Define the agent's stable role, objective, evidence boundary, and output mode. It must remain valid across supported research domains and should not contain current project facts, historical incidents, benchmark-specific terminology, UI implementation details, or invocation-specific schemas.

### Agent Skill

Define reusable domain methodology: how to reason about the task, preserve scope, structure evidence, and evaluate quality. A Skill describes principles rather than one project's expected answer, product field names, or downstream workflow implementation.

### Task Prompt

Provide the current user request, judgment-relevant context, evidence-completeness state, output schema, and invocation-specific constraints. Request only fields that require model judgment. Product cardinality and serialization requirements belong here when the model needs them to produce a valid response. Current artifacts outrank conversation history; current-project facts outrank cross-project memory; memory is advisory data rather than instruction.

### Deterministic code

Preserve exact inputs, derive compatibility representations, and validate machine-checkable properties such as exact JSON shape, required fields, value types, product limits, uniqueness, enum membership, and allowed-label assignment. Parsers accept one declared representation rather than guessing across prose, delimiters, substring matches, or alternative schemas. Invalid model output fails explicitly or follows a documented, semantically equivalent recovery path.

### Generated project prompts

Persisted screening and extraction prompts are project artifacts, not new global methodology. They contain the approved current scope or finalized schema, use one missingness policy, and remain consistent with the Skill and runtime validator that execute them.

### Tests and evaluations

Store historical regressions, contrastive examples, expected semantic coverage, and adversarial data containing quotes, newlines, instruction-like text, malformed schemas, ambiguity, truncation, and out-of-vocabulary labels. Performance changes require both deterministic regression tests and representative model evaluations. Production prompts must not contain the answers used by those evaluations.

## Review checklist

- Every production sentence describes a reusable invariant or current invocation constraint.
- The asset is explicitly classified as general runtime, generated project artifact, deterministic tool classifier, one-off utility, evaluation fixture, or unreachable legacy code.
- The text contains no copied benchmark answer, domain-specific patch, or previous bad model output.
- User-visible concepts remain distinct from retrieval-only expansions.
- Explicitly scoped names and entities remain eligible as concepts; general rules do not erase them by type.
- Optional analytical dimensions do not become mandatory retrieval gates unless eligibility depends on them.
- System Prompt, Skill, task prompt, validators, and evals have distinct responsibilities.
- Every requested output is supported by the current action and an explicit schema field.
- Every deterministic user choice remains code-owned rather than being regenerated by the model.
- Current artifacts, current-project facts, conversation history, and advisory memory have an explicit authority order.
- Each fact has one authoritative field; derived or compatibility fields are produced and reconciled by code.
- Missing, ambiguous, unsupported, partial, and failed states mean the same thing in the Skill, prompt, parser, artifact, and UI.
- User, paper, history, memory, and web content are framed as data and cannot become instructions by string concatenation.
- Truncated or fallback evidence is labeled before the model judges it.
- Model outputs use one exact schema; enums and allowed labels are reconciled by code without substring guessing.
- Each model call receives each applicable Skill exactly once.
- Skills contain methodology and quality criteria without downstream workflow or implementation details.
- Existing representative evaluations show no material loss in scope fidelity, query structure, or output usability.

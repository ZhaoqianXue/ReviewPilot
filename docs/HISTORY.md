# Historical development evidence

This index replaces the obsolete root execution logs removed during the September 12, 2026 documentation cleanup. Historical test counts and implementation decisions describe their recorded snapshots, not the current application state.

## Retained milestones

- July 2026: biomedicine, HCI and spatial-authoring scenarios completed the local single-user inner-beta workflow. The joint audit recorded 702 passing tests and 21 working exports. See the [joint release audit](internal-testing/runs/2026-07-14-inner-beta-release-audit.md), [scenario protocol](internal-testing/README.md) and [operator runbook](internal-testing/INNER_BETA_RUNBOOK.md).
- July 14, 2026: the separate formal-release objective was canceled. Historical records identify `e5189d5` as the retained inner-beta candidate; packaging/release work was rolled back. The canceled plan and its approval requests are not active tasks or current implementation instructions.
- July 16, 2026: follow-up work addressed task feedback, keyword/concept separation and consistent Skill/prompt contracts. The accumulated log's later full-suite milestone was 763 passing tests. See the retained [Skill and prompt authoring guidelines](skill-and-prompt-authoring-guidelines.md), [architecture documentation](../design/README.md), repository Skills and implementation tests for the maintained contracts.
- September 12, 2026: [bug audit](internal-testing/bug-audit-2026-09-12.md), [prototype parity audit](internal-testing/prototype-parity-2026-09-12.md), [parity fixes](internal-testing/prototype-parity-2026-09-12-fixes.md) and [urban live workflow verification](internal-testing/urban-live-e2e-2026-09-12.md) provide newer evidence with explicit verification limits.

## Cleanup boundary

Removed only `task_plan.md`, `findings.md` and `progress.md` from the repository root. These July execution logs contained superseded baselines, completed microtasks, repeated tool-error/budget notes and canceled release approval requests. They duplicated retained specifications, run reports, code and tests, and could be mistaken for current instructions. No design specification or run-evidence report was deleted.

Prototype assets and all writing files were retained. Three writing notes and two recent audit reports were translated into English. Translation preserved identifiers, evidence conclusions, links and manuscript limitations. The pre-fix parity report now links to subsequent verification while retaining its original historical conclusions.

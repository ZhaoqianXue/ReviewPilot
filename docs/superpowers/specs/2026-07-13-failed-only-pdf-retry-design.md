# Failed-only PDF retry design

## Goal and boundary

ReviewPilot must recover a current, non-stale retrieval report without requesting, mutating, or replacing papers that already have valid PDFs. The feature is local single-user inner-beta recovery, not a generic scheduler: it adds no retry-count policy, backoff configuration, parallel batch manager, or new download engine.

## Chosen architecture

The existing `DownloadAgent` must never run against the authoritative included-paper file during retry. A retry service copies only explicitly selected failed papers into an isolated staging project, invokes the existing download contract there, validates its strict report, then prepares a merged authoritative report and included-paper list. A durable abort-first transaction publishes the merged report, included metadata, newly downloaded PDFs, and retrieval ledger state as one recoverable logical change.

Directly rerunning the original included file is rejected because it can request and rewrite successful items. A persistent read-time overlay is rejected because it creates a second retrieval fact source and makes export, projection, and later extraction depend on dynamic merging.

## Identity and revision contracts

Each retryable failed paper receives an opaque deterministic `retryId`, derived by SHA-256 from the first available normalized identity in this order: internal paper ID, DOI, URL, title. The same identity must map exactly one failed report entry to exactly one included-paper row; missing or duplicate identities make the report non-retryable and fail loudly without mutation.

The `reportRevision` is a SHA-256 digest of the canonical current download report plus the retrieval ledger attempt and status. It is not the setup revision and never uses `pdf_count` as the current-attempt success count. State projection exposes only `reportRevision`, safe labels, failure classes, and opaque retry IDs; it never exposes filesystem paths.

## API and confirmation

`retry-failed-downloads` is a retrieval-stage recovery action. Its request names a non-empty, duplicate-free subset of the current failed `retryId` values and the current `reportRevision`. Before confirmation the server performs read-only validation and returns `409 confirmationRequired` with the exact revision and selected IDs. The confirmed request repeats those values inside `retry_confirmation`.

The server rejects no current failures, a stale retrieval stage, malformed or unknown IDs, duplicate IDs, a changed report revision, missing or mismatched confirmation, invalid report/included metadata, and any active project task. Validation or confirmation failure performs no domain call and no file or ledger mutation. Existing task reservation supplies duplicate-task rejection.

## Transaction and state flow

Prepare creates a durable marker in `phase=abort` containing the previous report, included rows, ledger, expected revision, and selected IDs, then marks retrieval running/stale before domain work so old retrieval/downstream exports cannot race with new outputs. The staging run contains only selected failed rows; tests must observe that no successful or unselected row is passed to the downloader.

After strict staging validation, successful retry PDFs receive collision-free deterministic destination names and are copied without overwriting any existing file. The service builds target report and included rows in memory. Original successful and unselected entries remain deeply equal and in their original order. Retry successes move from failed lists to downloaded; retry failures remain failed with updated safe failure metadata. Aggregate `success` and `failed` determine completed, partial, or failed retrieval status using the existing structured outcome contract.

While the marker remains `phase=abort`, any crash recovery restores the previous report, included rows, and ledger and removes newly introduced PDFs/staging data. Only after target report, included rows, and terminal ledger all succeed does the marker advance to `phase=apply`; recovery then rolls the target state forward idempotently. Marker cleanup is last. An ordinary retry exception performs the same abort restoration, leaving the original current partial/failed report usable and retryable.

Any material downstream extraction or categorization output is stale during a successful retry and remains stale afterward. If the retry aborts, the entire previous ledger, including downstream validity, is restored.

## Projection and frontend

For a current non-stale retrieval stage with failed papers, state exposes `retrievalRecovery = {canRetry, reportRevision, items}`. The retrieval outcome banner gets one `Retry failed downloads` action. The client submits all currently shown failed IDs, handles the server confirmation response with an explicit replacement prompt, and uses the existing owned-task monitor. Refresh during retry must recover the same active task; terminal success or failure must fetch authoritative project state before painting.

The control is absent for completed, stale, malformed, or no-failure reports. After a successful one-failure retry, the banner disappears, retrieval becomes completed, and Information Extraction is the next valid action.

## Tests and evidence

Task 4A covers stable identity/revision determinism; malformed, duplicate, unknown, stale, changed-revision, no-failure, unconfirmed, and active-task rejection; downloader input isolation; successful-item object/PDF hash preservation; partial and complete merges; retry exception rollback; abort/apply crash recovery; downstream stale semantics; and strict no-path projection.

Task 4B covers frontend visibility, exact confirmation payload, cancellation, duplicate click ownership, failure refresh, and a browser run with one existing success plus one retryable failure. The browser run refreshes while retry is active and proves that only the failed paper was requested, the original successful metadata and PDF hash are unchanged, the merged report is completed, the retry control disappears, and the next workflow action is enabled.

Each subtask requires focused and full native tests, `git diff --check`, a clean worktree, independent specification review, and independent quality review. HCI and spatial real examples remain blocked until both subtasks pass.

## Explicit non-goals and accepted limit

This design does not solve hostile same-user filesystem races, multi-process locking, distributed workers, or descriptor-level no-follow publication. Those remain outside the local single-user inner-beta threat model. It does require direct retry roots/files and transaction artifacts to stay inside the project and reject symlink escapes.

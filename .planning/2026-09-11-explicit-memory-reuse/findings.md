# Findings

Current LeadAgent automatically retrieves and promotes SQLite presets. Local context mixes editable and confirmed state, and criteria chat can reopen finalized rules without a separate edit action. Schema draft save overwrites current schema and removes finalization marker, so preserving confirmed decisions requires snapshots before edits.

Implementation: local confirmed snapshots; finalized criteria chat lock; source/target revision-bound manual imports from local projects; automatic LeadAgent retrieve/promote removed even with enabled legacy SQLite settings. Search imports stay in a separate local draft; criteria/schema/category imports enter existing draft confirmation flows. Category browser snapshot confirmation is no longer authoritative.

Final authority guard: schema finalization markers bind normalized schema content, and schema chat cannot finalize a draft; the canvas action is required. Historical model-generated finalize commands therefore cannot confirm a draft. Full local history remains unchanged. Reuse UI displays human-readable full configuration previews and last confirmed values separately from imported drafts.

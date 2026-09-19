# Approve and release curriculum

Use this only after extraction and independent verification are complete.

## Efficient run shape

1. Import once and inspect machine-reported observations before semantic work. Fix
   reusable parsing defects in importer code and source-specific defects in a
   source-bound correction file. Re-import until blocking observations are resolved.
2. Inspect PDF images only for pages named by font, ruby, table, ordering or layout
   anomalies. Do not render or review every page by default.
3. Inspect the section index once, then retrieve only the source sections needed for
   the current topic or finding. Reuse deterministic lexical candidates rather than
   reproducing them in model output.
4. Complete schema validation and a compact extractor self-audit before dispatching
   the independent verifier. This avoids spending an independent pass on obvious
   duplicate keys, missing section records or incomplete topic fields.
5. Run the independent verifier in a fresh context once the curriculum is stable.
   If it finds issues, change only the affected source corrections or semantic
   objects, then produce a final hash-matched verification submission. Never reuse
   verification whose artifact hashes are stale.
6. Present the human with a compact release summary: lesson and edition, object and
   section counts, review-queue count, material corrections and any remaining risks.
   Do not paste full curricula or verification payloads unless requested.

## Approval and build

- Automatic verification never grants human approval. Obtain explicit approval
  before publishing or rebuilding the curriculum database.
- Store the hash-bound approval locally at
  `data/generated/<document-id>/approval.json`. It records `document_sha256`, the
  canonical Document and curriculum artifact hashes, `human_approved: true`, the
  approval date and a short basis.
- Do not create `fixtures/golden/<lesson>/` for routine lessons. The lesson 5 golden
  is a historical importer/regression sample, not a template or build requirement.
- Add the lesson's Document, curriculum, verification and approval paths to
  `data/generated/curriculum_inputs.json`. Retain every already published lesson,
  because `jt build-db` deterministically recompiles and atomically replaces the
  curriculum database. This does not rerun extraction or verification and does not
  modify `learner.db`.
- Run `jt build-db data/generated/curriculum_inputs.json`. Report compact table
  counts, then use bounded retrieval for the new lesson (for example one outline
  existence check and one representative search). Avoid printing complete outlines
  or large JSON results into the conversation.

If approved artifacts change, repeat independent verification and obtain a new
hash-bound approval. A regression fixture, when one exists, cannot substitute for
approval.

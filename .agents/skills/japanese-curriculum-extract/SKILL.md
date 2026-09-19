---
name: japanese-curriculum-extract
description: Extract source-grounded Japanese curriculum semantics from this project's imported Document JSON using validated Python CLI services. Use for textbook import maintenance, not learner teaching or assessment.
---

# Japanese curriculum extraction

Codex drives this workflow. Python prepares source/candidates, validates submissions,
and saves artifacts; it never calls models. Run commands from the project root.

1. Run `jt extract <lesson_document.json>` (or `python -m japanese_tutor.cli extract`).
   Read `extraction_schema.json`, the prepared section index and deterministic
   candidates once. Use `jt show-source <lesson_document.json> --section <id>` for
   focused evidence instead of repeatedly loading the full Document or source bundle.
   Read `data/corrections/<series>-conventions.md` when available. Imported document
   metadata identifies the notation convention. `source.json` also includes the
   series' validated `data/corrections/<series>-curriculum.json` convention snapshot;
   do not apply another series' rules.
2. Reuse lossless vocabulary candidates. Reading and lexical pitch are attached
   lexical fields; never create per-word reading or pitch concepts. Keep the
   structured notation once in `data.notation`; clear copied `source_text.lexical_notations`.
   Sentence intonation needed for a grammar/expression topic stays inside that topic.
   For confirmed shared group JLPT rules, use `resolve_group_jlpt` with the source
   convention: all split components inherit the common label, including repeated
   copies of the same group label. Distinct labels still require explicit alignment.
   The label claim is supported by the literal source label plus the human-reviewed
   convention; do not repeatedly flag this confirmed case as unresolved.
   Same surface/reading identifies one lexical object. After semantic confirmation,
   call `merge_lexical_concepts` to combine repeated occurrences; gloss and word-class
   wording changes are source variants, not new lexical identities. Preserve true
   reading/pitch conflicts for source review, never silently discard them.
   Resolve multi-entry rows only when word,
   reading, meaning, and labels align in source; preserve partial readings, loanword
   originals, ruby, pitch raw notation, and sentence prosody separately. Do not
   upgrade unresolved source observations by relying on memory.
3. Organize grammar explanations and expression extensions as teaching topics:
   one `Concept` (`subtype: topic`, `data.kind: topic`) holds its forms, notes,
   tables and multiple embedded examples. Use textbook titles/subtitles as the
   starting boundary; Codex may merge or split when this creates a coherent,
   independently teachable topic. Do not split every particle, politeness detail,
   pronunciation observation or example into global objects. Preserve textbook
   conditions, exceptions and useful conjugation tables inside the topic.
   Embedded examples use plain Japanese strings, optional textbook translations,
   usage markers and speaker turns. Do not copy ruby, lexical notation or word
   analysis from Document JSON; retrieve the original with `show-source` if needed.
   Keep topic-relevant pronunciation/prosody in notes or the original example text.
   Use zero-based `source_indices` into the topic's `supports` for every note,
   example and table; choose concise literal evidence anchors, not repeated full
   paragraphs. Local array positions locate content without global example IDs.
   Preserve dialogue/contrast group boundaries, continuation translations and
   preferred/dispreferred usage. Chinese prose is not a Japanese example.
   No standalone grammar/expression examples or internal relationship objects.
   Source exercises remain separate, with unique topic target references and
   progression tags; these references already express what is practiced. Retain
   only useful, source-supported cross-topic relationships, avoiding repeated
   exercise/example membership edges. Full lesson dialogues retain their existing
   structure. Topic IDs identify teaching meaning rather than title numbering.
4. Supply literal support snippets with document/page/section and a short reason.
   `asserted` means directly stated; `derived` means a justified inference from
   source exercise/content structure; unsupported pedagogical suggestions are
   `proposed`. Source existence does not prove a claim. Mark uncertainty explicitly.
   Use `japanese_tutor.ids.object_id(lesson, kind, semantic_key)` for readable IDs;
   keys must identify meaning, not PDF page or section position.
5. Fill one section processing record per input section, listing sourced objects
   or a reason for no semantic content. Check topic boundaries, rules, exceptions, example/table coverage,
   dialogue scenes and exercise goals for omissions. Set extractor identity and
   instruction version `stage2-v3`. Save a local submission JSON and run
   `jt extract <document> --submission <submission.json>`; fix rejected contracts
   through the service, never bypass validation or write to SQLite.
6. Run `jt verify <document> <semantic/semantic_curriculum.json>` to prepare tasks.
   Have an independent Codex context apply `$japanese-curriculum-verify`. When
   subagents are available, delegate verification with only the verification skill,
   prepared task artifact/schema and source evidence paths; do not fork extraction
   conversation or send intended answers. Otherwise leave prepared tasks pending
   for a fresh Codex context; never claim independence by switching roles here.

If font mapping/layout evidence is unreliable, inspect the corresponding rendered
PDF page first. Preserve source-bound corrections and review evidence; escalate
only remaining uncertainty. Keep generated textbook artifacts local. Stage 3
requires explicit human approval after automatic verification. Do not create a
per-lesson regression golden unless the user explicitly requests regression coverage;
golden fixtures are not a release gate. For approval, manifest updates, database
builds and the token-efficient run shape, read [references/release.md](references/release.md)
only when the lesson is being prepared for release.

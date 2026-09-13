---
name: japanese-curriculum-verify
description: Independently verify Japanese semantic curriculum objects against imported textbook sections and check source coverage. Use for source-grounded curriculum maintenance verification, not teaching or extraction in its original context.
---

# Independent source verification

Work in a fresh Codex context containing source evidence, candidate curriculum and
verification contracts, without extractor conversation or its reasoning. If that
condition is absent, prepare tasks for a fresh context and report verification pending.
Python does not start a verifier or call a model. Run commands from the project root.

Read the provided `verification_tasks.json` and `verification_schema.json`.
Use `jt show-source <lesson_document.json> --section <id>` for additional evidence.
Compare source text with every object and all its required fields listed in tasks:
surface, reading (including partial scope), raw pitch/interpretation, meaning,
word class, JLPT, ruby, translation, grammar claims/conditions/limits, taxonomy,
provenance, lexical source variants, dialogue turns, exercise targets/progression,
and relation support. Check semantic uniqueness: one surface/reading has one
lexical object; no per-word reading/pitch object or copied lexical notation.
Reading and lexical pitch must remain lossless attached fields. Topic-relevant sentence intonation
stays inside the teaching topic; it must not disappear during compaction.
For grammar/expression check coherent topic boundaries against textbook headings,
all embedded notes/forms/tables and every example turn, translation, usage and
local source index. Required fields include each `notes[i]`, `examples[i]` and
`tables[i]`; explicitly check every item, not only the topic summary. Report issues
with those local paths. Plain example text deliberately omits ruby/word analysis;
compare text with the rich Document source without requiring copied annotation.
Check that no standalone example or micro-concept duplicates topic content, and
exercise targets now identify the topics actually demonstrated. Nested membership
and exercise target references need no separate relation objects.
Absence of a value must be checked too. A plausible Japanese fact is insufficient:
the cited textbook section must support it. For relations inspect both endpoints
in the provided curriculum and check what the source actually establishes.

Check bilingual example boundaries, continuation lines and source markings of
preferred/dispreferred forms; Chinese explanatory prose is not a Japanese example.
Keep translation separate and do not omit untranslated dialogue turns.

Check every source section in the reverse direction for omitted rules, limitations,
examples, dialogue scenes, pragmatic guidance and exercise goals. An empty or
no-content record still requires this coverage check. A section not separately
represented may be covered by a concept with explicit support from that section.

Return a `VerificationSubmission` matching the schema: exact task artifact hashes,
verifier identity, instruction version `stage2-v3`, your actual context identity,
`independent_context: true`, object field checks and section coverage checks.
Each check needs literal source supports and reasons. Use `review_required` with
field-specific issues or omissions for conflicts; do not silently rewrite candidates.
Preserve `unresolved` when source cannot decide. Confidence is a review signal,
never proof. Do not infer missing textbook facts from memory.

For font mapping or similar layout anomalies inspect the corresponding PDF page
image before escalating; preserve the evidence reference. For already confirmed
series conventions use the imported notation metadata and source-reviewed rules.
The prepared source includes a validated curriculum convention snapshot. A human-
confirmed shared group JLPT label applies to all split components; compare source
labels and this rule together. Do not reject this confirmed attribution merely
because the label is centered on a grouped row. Mixed labels, absent labels and
other-series conventions must still be checked without guessing.

Save a local result JSON. Submit with
`jt verify <document> <semantic_curriculum.json> --submission <result.json>`.
The CLI validates hashes, scope and required checks, then reports all missing checks,
conflicts, unresolved values and proposed claims. Partial checks stay pending.
Automatic verification does not grant human approval or freeze regression goldens.

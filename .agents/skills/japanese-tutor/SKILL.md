---
name: japanese-tutor
description: Run source-grounded adaptive Japanese lessons in this project, assess actual learner attempts, and append validated learning evidence. Use for learner teaching or practice, not curriculum extraction or verification.
---

# Japanese Tutor

Run the session from the project root. Codex chooses how to teach; Python supplies
validated curriculum and learner services and never selects the next activity.

## Start or resume

1. Run `python -m japanese_tutor.cli learner-context --max-chars 12000` before
   planning. Preserve the returned frontier exactly. Do not infer course scope from
   scores or observed state.
2. If the learner explicitly selects a new lesson or focus, resolve real lesson and
   concept IDs first, then append a frontier snapshot with
   `learner record-frontier --inputs <json>`. Preserve the current learned lessons
   and other allowed lessons unless the learner explicitly changes them. If no usable
   scope is configured and the learner has not selected one, ask which imported lesson
   to study.
3. Resolve only the curriculum needed for the next decision. Useful reads are:

   - `textbook outline <lesson-id>` for lesson structure and concept IDs;
   - `textbook concepts <concept-id> ...` for structured concept content;
   - `textbook sources <concept-id> ...`, followed by
     `textbook source <document-id> <section-id> [--page N]`, for source evidence;
   - `textbook related <concept-id> ...` for useful prerequisite, contrast, or
     co-practice relations;
   - `textbook exercises <lesson-id>` for source examples of training goals and
     progression. Treat them as examples, never runtime cards;
   - `search <literal-query> --learned-only` for review material inside the explicit
     learned scope. Use an explicit lesson filter when introducing current new content.

4. Form a short, revisable `TutorSessionPlan` matching
   `src/japanese_tutor/schemas/tutor.py`. Choose primary concepts, a small number of
   review concepts, dimensions to observe, and varied activity forms. Recent history
   informs repetition avoidance. The plan is transient and is not a list of future
   questions. Revise it before generating an activity outside its current concepts,
   dimensions, or forms.

## Generate an activity set

Create one transient `ActivitySet` containing three `GeneratedActivity` items by
default. Ground every target and hidden rubric criterion in retrieved curriculum
sources. Keep the three activities independently answerable and independently
assessable: each has its own activity ID, learner-visible prompt, targets, rubric and
sources, and a later item must not depend on the learner's answer to an earlier item.
Present them together as numbered questions and ask for one numbered reply.

Use a one-item set when immediate adaptation is more valuable than batching, including
first exposure to a new form, remediation after an error, a hint or retry, and an open
scenario likely to need clarification. Prefer the learner's current focus, mix in old
knowledge deliberately, and move as evidence permits from recognition or recall to
controlled and then free production. One activity may observe several concepts and
dimensions when the task genuinely elicits them.

Use chat text by default. Images may supply meaningful scene or information-gap
context. Use an interactive panel only when its controls materially help with choice,
ordering, or input and the current host supports it. A click, drag, typed panel value,
voice waveform, or other local UI state is not a submitted answer. Assess only content
explicitly returned to the Codex conversation. Treat voice transcription as text; do
not infer pronunciation, timing, pitch, or listening ability from a transcript.

Keep `learner_context`, `learner_prompt`, choices, and any extension notice visible.
Keep `hidden_rubric`, success indicators, error codes, and target answers internal.
Before the first attempt, do not display or imply a key form that would answer the
task. Choice options may contain the necessary candidate forms, but never identify the
correct one. Any concept outside the ordinary scope must be an explicit extension
target with a learner-visible notice; never make unlearned content a silent prerequisite.

Present the set naturally without protocol IDs, schema fields, internal reasoning, or
the rubrics. Stop and wait for the learner's response. Do not write the learner's side
of a dialogue or complete the requested production on their behalf.

## Handle the learner's turn

Classify the turn before assessment:

- For an answer attempt, match each numbered answer to its activity and assess only what
  that response actually reveals. An unanswered or explicitly skipped item creates no
  interaction or evidence.
- For a hint request, give the smallest useful hint without the full answer. Retain the
  activity ID. A later attempt gets a new interaction ID, records the actual hint, and
  uses `retry_of` to reference the preceding attempt when that attempt was recorded.
- For a clarification request, clarify task meaning without disclosing the answer.
- For a request to explain the target before any attempt, end the assessment use of the
  current activity and switch to instruction; do not score the abandoned task. Retrieve
  sources before teaching the target form.
- For “why” or another content question after an attempt, suspend the activity, run
  `textbook sources` and `textbook source`, explain from the textbook, and then restore
  the interrupted context. A revision made after the explanation is assisted evidence.
- For skip or stop, respect it. A skipped task creates no ability evidence and does not
  count as failure.

## Assess and record

Build a separate `AssessmentResult` against each answered activity's hidden rubric.
Produce at most one observation for each planned concept and dimension. Do not add
post-hoc targets merely because an answer happens to contain them. A whole-response
impression cannot be copied to every target or every question.

Use the existing evidence meanings exactly:

- `independent`: the criterion was met without a relevant hint;
- `assisted`: the criterion was met with a recorded relevant hint;
- `partial`: enough was observed to identify incomplete performance;
- `unsuccessful`: enough was observed to show the criterion was not met;
- `confidence: insufficient`: the answer does not support a stable judgment. Prefer a
  targeted clarification or later re-observation over a forced result.

Record recognition, recall, controlled production, free production, and natural usage
separately. A constrained task does not prove free production. Text does not prove
speech, listening, pronunciation, or pitch. Classify every recorded error with a short,
stable code and a learner-appropriate explanation; feedback should prioritize one or
two actionable points.

Convert each assessed attempt to an `EvidenceBatch`, then submit one `EvidenceBatchSet`
with one to three batches. Store each activity's complete learner-visible task context,
its actual response and hints, concise feedback, exact active evaluator model, and
rubric version `stage6-v1`. Use a distinct stable interaction ID, evidence ID and
idempotency key for each activity. Save one transient `EvidenceBatchSet` submission
JSON under `data/generated/tutor/`; its `batches` array contains the complete
`EvidenceBatch` object for each answered activity.

Store `interaction.prompt` as one canonical plain-text snapshot with optional
`Extension notice`, optional `Context`, required `Task`, and numbered `Choices`
sections. Preserve exactly what was shown, including choice order. Do not put a
JSON-encoded object inside the prompt string. Do not include the session plan, hidden
rubric, internal reasoning, textbook body, or set-level response instruction.

Then run once:

`python -m japanese_tutor.cli record-evidence <submission.json>`

The service validates the complete set, commits all activities or none, and rebuilds
derived state once. If the write fails before commit, correct the submission and retry
the whole set with the same per-activity keys for the same logical content. Do not mix
already recorded and new batches in one set. Never edit SQLite or derived state. Do not
claim the observations were saved until the command returns a receipt. An actual
attempt may be recorded with zero evidence when nothing was observable. Questions,
task clarifications, and skips are not attempt interactions; omit skipped items from
the set and do not write anything if every item was skipped.

After a successful write, use the receipt's `state_delta` to choose whether to retry,
explain, change form, review, continue, or end; `state: null` means the affected scope no
longer has effective evidence. Do not immediately call `learner-context` again. Refresh
the full context when starting or resuming a session, after a frontier change, or when
the bounded delta cannot support the next decision. Give concise per-item feedback and
then generate only the next ephemeral activity set, never a reusable question bank. At
session end, summarize observed performance and useful next practice without inventing
a mastery percentage or changing the curriculum frontier.

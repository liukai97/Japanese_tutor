# Japanese Tutor

A Japanese tutor built around your textbook and your learning history. Codex plans
lessons, creates practice activities, and responds to your answers. Python handles
the parts that need to be reliable: textbook sources, curriculum boundaries,
validated learning records, and progress derived from those records.

You work with the tutor by talking to Codex in this repository. The included skills
guide textbook preparation and teaching, while Codex uses the supporting tools to
manage the data.

## What it does

- **Keeps teaching tied to the textbook.** Curriculum content carries document,
  page, and section references so explanations can be checked against their source.
- **Preserves Japanese notation.** PDF import reconstructs furigana, vocabulary
  tables, readings, and pitch accent notation, keeping lexical pitch separate from
  sentence intonation.
- **Creates practice as the lesson unfolds.** Activities can combine several
  concepts and adapt to actual answers. Textbook exercises provide examples of
  training goals; generated activities do not become a reusable question bank.
- **Records what the learner actually demonstrates.** Recognition, recall, and
  usage are tracked separately. Corrections and retractions preserve the evidence
  history, and derived state can be rebuilt.
- **Respects the learner's course scope.** Learned lessons, current focus, and
  allowed extensions are explicit settings, rather than guesses based on scores.

## Getting started

### 1. Open the repository in Codex

Start a conversation with Codex in this project. Textbook preparation and lessons
happen through that conversation; Codex runs the underlying tools for you.

### 2. Ask Codex to prepare your textbook

Place your textbook PDFs in `materials/` and tell Codex which files and lessons to
prepare. For example:

> Convert the lesson 5 textbook PDF in `materials/` into curriculum for the tutor.
> Check the extraction against the source and show me anything that needs review.

Codex imports the PDF, checks reported issues, and uses the
[`japanese-curriculum-extract` skill](.agents/skills/japanese-curriculum-extract/SKILL.md)
to organize vocabulary, teaching topics, dialogues, and exercise goals. The
[`japanese-curriculum-verify` skill](.agents/skills/japanese-curriculum-verify/SKILL.md)
checks the result against the source in an independent context. If a fresh context
is unavailable, verification stays pending until that review can happen.

Codex presents the result for your approval before building the curriculum
database, following the
[release workflow](.agents/skills/japanese-curriculum-extract/references/release.md).
You review the findings and approve the curriculum in conversation. Codex handles
the build and preserves existing learner records.

### 3. Start a lesson

Once your curriculum is ready, tell Codex what you want to study:

> Use $japanese-tutor to help me practice lesson 5. I've already studied this lesson;
> focus on vocabulary recall and using it in short conversations.

You can also ask to resume practice, review older lessons, or change your focus:

> Continue our previous lesson and include some review of vocabulary I struggled with.

### What stays local

The repository includes the application, schemas, SQL definitions, Codex skills,
reviewed source corrections, and tests. Textbook PDFs in `materials/`, generated
artifacts in `data/generated/`, and SQLite databases are ignored by Git. A fresh
clone needs your local textbooks and a reviewed curriculum before teaching can begin.

## Study with Codex

Use the [`japanese-tutor` skill](.agents/skills/japanese-tutor/SKILL.md) to start or
resume a lesson. It reads learner context, retrieves the relevant textbook
material, and creates practice within the learner's configured scope. If no usable
scope is configured, it asks which imported lesson to study.

Activities are usually presented in sets of three, with single activities used
when immediate feedback or adaptation is more useful. Answers and hidden rubrics
stay private until the learner's first attempt. Hints, retries, explanations, and
skips are handled according to what the learner actually submits.

Validated services save assessed attempts and update derived state. A skipped
activity creates no ability evidence, and a text answer cannot establish listening,
pronunciation, or pitch ability.

Tell Codex when you want to change your preferences, mark lessons as already
studied, or focus on a new topic. It records those choices explicitly and uses them
to guide practice. You can ask it to explain a mistake, give a hint, skip an
activity, or summarize what your recent answers show.

## How the pieces fit together

```text
Local textbook PDF
  → Japanese-aware document + source review
  → Semantic extraction + independent verification + human approval
  → curriculum.db → textbook retrieval
                           ↓
                       Codex tutor
                           ↓
                    Learner attempts
                           ↓
              learner.db → derived state + review candidates
                           ↓
                    Further practice
```

Curriculum is rebuildable. Learner evidence is independently portable. The program
returns context and review candidates; Codex decides how to teach next.

| Path | Purpose |
| --- | --- |
| [`.agents/skills/`](.agents/skills/) | Teaching, extraction, and verification workflows |
| [`src/japanese_tutor/`](src/japanese_tutor/) | CLI, import pipeline, schemas, repositories, and tutor contracts |
| [`sql/`](sql/) | Separate curriculum and learner database schemas |
| [`data/corrections/`](data/corrections/) | Source-bound repairs and reviewed textbook conventions |
| [`fixtures/golden/lesson_05/`](fixtures/golden/lesson_05/) | Focused lesson 5 regression fixture |
| [`tests/`](tests/) | Import, curriculum, learner evidence, CLI, and tutor contract tests |
| [`AGENTS.md`](AGENTS.md) | Architecture invariants for work in this repository |

## Development

The package requires Python 3.12 (`>=3.12,<3.13`). With the development dependencies
available, run checks from the repository root:

```sh
python -m pytest
python -m ruff check .
```

The tests include synthetic PDFs. Some lesson 5 regression checks require local
textbook or generated artifacts and skip when those inputs are absent.

## License

[MIT](LICENSE).

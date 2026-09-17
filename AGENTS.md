# Japanese Tutor architecture invariants

- Treat textbook content and its source references as authoritative; do not replace sourced facts with model memory.
- Keep curriculum data separate from learner data. Curriculum artifacts may be rebuilt, while learner evidence must remain independently portable.
- Codex must never write derived ability state directly. Validated services append learning evidence (including corrections/retractions) and recompute replaceable state; no mastery-edit tool is exposed.
- Profile preferences may be updated through validated configuration services. Course scope/focus changes use explicit append-only frontier records; learned lessons and allowed extensions are separate and never inferred from scores.
- Do not reveal target answers, hidden rubrics, or key forms before the learner's first attempt.
- Do not implement or persist a reusable question bank. Textbook exercises are source examples, not runtime cards.
- The program may return context and review candidates, but Codex chooses how to teach and must not receive a program-selected fixed next question.
- LLM-facing code must use validated application services, CLI commands, or MCP tools rather than direct SQLite access.
- For font mapping or similar layout anomalies, Codex must review the corresponding PDF page evidence first; request human review only if uncertainty remains. Preserve source-bound corrections and their review evidence.
- Codex drives curriculum extraction and verification through validated Python services; Python must not invoke models or launch agents.
- Curriculum maintenance skill entrypoints: `.agents/skills/japanese-curriculum-extract/SKILL.md` and `.agents/skills/japanese-curriculum-verify/SKILL.md`; use an independent context for source verification.
- Learner teaching and assessment entrypoint: `.agents/skills/japanese-tutor/SKILL.md`.

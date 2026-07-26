# Learning materials

This section is a set of concept explainers written in the style of three newsletters the team reads, mapped onto the subsystems that already exist in OmniAI. It exists so that "why does the engine have a circuit breaker" or "why does the learner have a rehearsal buffer" has a self-contained answer that teaches the underlying idea, not just the local implementation.

Scope, deliberately: **system design, ML/AI concepts, and LLM research** — the parts of this project that are interesting independent of the programming language. Python syntax, library APIs, and backend/deployment mechanics (FastAPI routes, Docker, Alembic migrations, etc.) are already covered by the [how-to guides](../how_to/index.md) and are out of scope here.

| Page | Style modeled on | What it covers |
| --- | --- | --- |
| [System design](system_design_bytebytego.md) | [ByteByteGo](https://blog.bytebytego.com/) | Diagram-first explainers of the distributed-systems patterns behind serving, gateway routing, and multi-agent architectures. |
| [Data science concepts](data_science_dailydose.md) | [Daily Dose of Data Science](https://blog.dailydoseofds.com/) | Short, practical explainers of the ML techniques behind retrieval, evaluation, and interaction curation. |
| [LLM research](ahead_of_ai.md) | [Ahead of AI](https://magazine.sebastianraschka.com/) | Deeper technical dives into the research ideas behind adapter fine-tuning, continual learning, and LLM-as-judge evaluation. |

Each page follows the same shape: the general idea (as you'd find it in the newsletter), then an **"In OmniAI"** box pointing at the concrete module that implements it, so the concept and the code stay connected without turning this into an API reference (that's [`docs/reference/`](../reference/index.md)).

None of this is official content from ByteByteGo, Daily Dose of Data Science, or Ahead of AI — it's material *inspired by* the kinds of explainers those newsletters publish, written specifically to teach the concepts underneath this codebase. If you want the primary source, subscribe to the newsletters directly.

# Data science concepts, Daily-Dose-of-Data-Science-style

Daily Dose of Data Science favors short, concrete explainers over a single technique — what it is, why the obvious alternative falls short, when to reach for it. This page applies that format to the ML techniques behind retrieval and interaction curation.

## Embeddings and vector similarity search

**The idea.** An embedding model maps text to a fixed-length vector such that semantically similar text lands nearby in vector space. "Retrieval" then becomes a nearest-neighbor search: embed the query, find the stored vectors closest to it (cosine similarity or dot product), return the corresponding text.

**Why not just keyword search?** Keyword/BM25 search matches on exact tokens, so "car" and "automobile" are unrelated to it. Embedding search captures meaning, so paraphrases and synonyms retrieve correctly — at the cost of being harder to debug (you can't `grep` your way to why a result was ranked highly) and requiring an embedding model as a dependency.

**In OmniAI:** `omniai.rag.store` defines the `VectorStore` contract and ships a dependency-free `InMemoryVectorStore` + `HashEmbedder` so the retrieval *interface* is testable without a real embedding model — swap in a real embedder (OpenAI, Sentence-Transformers) behind the same contract for production quality. See [Concepts → Tools](../concepts/tools.md) for how retrieved chunks reach the model as grounding context rather than training data.

## Bi-encoders vs. cross-encoders: retrieval's precision/recall knob

**The idea.** These are two ways to score how relevant a document is to a query, and RAG pipelines often use both, in sequence:

| | Bi-encoder | Cross-encoder |
| --- | --- | --- |
| How it scores | Embeds query and document *separately*, compares vectors | Feeds query and document *together* into one model, outputs a relevance score directly |
| Speed | Fast — documents are pre-embedded once; search is a vector-distance lookup | Slow — a full forward pass per (query, document) pair, at query time |
| Accuracy | Lower — no cross-attention between query and document tokens | Higher — the model can directly attend across both texts |
| Typical role | First-pass **retrieval** over the whole corpus | **Re-ranking** the top-K candidates the bi-encoder already narrowed down |

**Why both:** a cross-encoder is too slow to run against every document in a large corpus, but too accurate to skip entirely. The standard pipeline is bi-encoder for coarse retrieval (fast, over everything) → cross-encoder for re-ranking (slow, over only the top 20-50 candidates).

**In OmniAI:** the `Retriever` in `omniai.rag` currently performs the bi-encoder step (embed-and-compare) described in [Compound AI architecture](../COMPOUND_AI.md). A cross-encoder re-ranking stage is a natural extension point if retrieval precision on a large corpus becomes the bottleneck — it slots in between `VectorStore.search` and the results handed to the model.

## LLM-as-a-judge, and why it needs a second model

**The idea.** Instead of (or alongside) human review, use an LLM to score another LLM's output against a rubric — did the response follow instructions, stay grounded in retrieved facts, avoid a disallowed topic. This scales review to volumes no human team could cover.

**The catch: it's still noisy.** A judge model shares blind spots with the model it's judging (both trained on similar data) and can be fooled by fluent-but-wrong answers, verbose answers scoring higher regardless of correctness, or ordering biases in the comparison. It is a filter that improves the *average* quality of what passes through it, not a ground-truth oracle — pair it with spot-checked human review and a *golden* dataset of known-correct examples for anything safety-critical.

**In OmniAI:** `omniai.memory.curation`'s `InteractionJudge` scores logged interactions before they're allowed into a training batch — this is the anti-poisoning step: a user can't just spam bad examples into the interaction log and expect them to shape the next LoRA adapter. See [Memory & learning](../concepts/memory_and_learning.md).

## Data version control, for interaction logs

**The idea.** Once training data changes over time (new interactions arrive continuously, some get curated out, adapters are trained on different windows of it), "which data produced this model" stops being answerable from the model artifact alone — you need to track *which dataset version* trained *which model version*, the same problem code version control solves for source files.

**In OmniAI:** every trained adapter is named with the timestamp of the training cycle that produced it, and `ContinuousLearner`'s high-water mark records exactly which interactions were (and weren't) included — see [Memory & learning](../concepts/memory_and_learning.md#the-learning-cycle) for how that mark is maintained and its failure modes on restart.

## Further reading

- [Daily Dose of Data Science](https://blog.dailydoseofds.com/) — the newsletter this page's format is modeled on, for further no-fluff explainers on retrieval, evaluation, and applied ML.

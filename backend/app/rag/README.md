# Layer 3 — RAG (retrieval-augmented generation)

Document ingestion (parse → chunk → embed) into Postgres + pgvector, then hybrid retrieval
(vector + keyword) with reranking. Retrieved chunks are filtered by the caller's permissions.

**Hooks into:** `ChatService`, after auth and before the LLM call — adds context and citations.
**Status:** not built yet.

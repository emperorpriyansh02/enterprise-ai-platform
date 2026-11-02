# Layer 3: Knowledge bases (RAG)

Upload documents into **collections**, then let chat answer from them, with citations.

```
upload ──► parse (txt/md/pdf/docx/html) ──► save as "pending" ──► background worker:
           chunk ──► embed (768 numbers) ──► store in Postgres (pgvector + full-text) ──► "ready"

chat with "rag": {"collections": [...]} ──► embed the question ──► vector search + keyword search
           ──► merge (Reciprocal Rank Fusion) ──► top passages ──► "Sources" block in the system prompt
           ──► model answers citing [1], [2] ──► reply carries "citations"
```

## Files

| File | What's in it |
|---|---|
| `models.py` | Tables: `collections`, `documents`, `chunks` (vector + full-text columns) |
| `parsers.py` | File → plain text, for .txt, .md, .pdf, .docx, .html |
| `chunking.py` | Text → overlapping passages |
| `embeddings.py` | OpenAI, Gemini, Ollama and mock embedders |
| `repository.py` | Every SQL query, including the two searches |
| `retrieval.py` | Merging search results (RRF), building the "Sources" text |
| `service.py` | Rules: collections, adding/processing documents, search, chat context |
| `runtime.py` | App-wide pieces: the embedder, the background worker, the availability check |
| `schemas.py` | API request and response shapes |

Routes: `app/api/v1/collections.py`. Chat uses it in `ChatService._retrieve` (`app/services/chat_service.py`).

## Requirements

- `DATABASE_URL` pointing at **PostgreSQL with pgvector**. The Docker Compose Postgres image includes it.
  The migration runs `CREATE EXTENSION vector`, which needs a database user allowed to create extensions.
- An embedding provider. `EMBEDDING_PROVIDER` = `openai`, `gemini`, `ollama` or `mock`. If it's blank, the
  first one that's configured is used. Default models:

  | Provider | Default model | Notes |
  |---|---|---|
  | OpenAI | `text-embedding-3-small` | asked for 768 dimensions |
  | Gemini | `gemini-embedding-001` | asked for 768 dimensions |
  | Ollama | `nomic-embed-text` | 768 natively; run `ollama pull nomic-embed-text` first |
  | Mock | `hash` | word-hashing; for tests and demos, not real meaning |

  Any model works if it can return **768** numbers (`EMBEDDING_MODEL` to change it).
- Each collection remembers which embedding model indexed it. If you change the embedder, searches on old
  collections return 409 `embedding_model_mismatch` until you re-index (`POST /v1/collections/{name}/reindex`).

Without these, `/v1/collections` returns 503 `rag_unavailable` with the reason, and chat without `rag` works as before.

## Using it in chat

```json
POST /v1/chat
{
  "model": "anthropic/claude-opus-5-5",
  "rag": {"collections": ["hr-policies"], "top_k": 5},
  "messages": [{"role": "user", "content": "How many days of annual leave do I get?"}]
}
```

The reply includes `"citations": [{"index": 1, "document_title": "...", "snippet": "...", ...}]`
(`[]` if nothing matched). Streaming sends them on the `start` event. To see the exact text the model
got, use `"model": "mock/echo-system"`.

## API

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/v1/collections` | List / create collections |
| GET / DELETE | `/v1/collections/{name}` | Details (document counts, embedding model) / archive |
| POST | `/v1/collections/{name}/reindex` | Re-embed every document with the current model |
| GET / POST | `/v1/collections/{name}/documents` | List / upload files (multipart field `files`, one or more) |
| POST | `/v1/collections/{name}/documents/text` | Add pasted text `{"title", "text"}` |
| GET / DELETE | `/v1/collections/{name}/documents/{id}` | One document's status / delete it and its passages |
| POST | `/v1/collections/{name}/documents/{id}/reindex` | Process one document again |
| POST | `/v1/collections/{name}/search` | Test search: `{"query", "top_k"}` → ranked passages, no LLM |

Uploads return **202**: documents start as `pending` and become `ready` (or `failed`, with `error`) a few
seconds later. Writes need `X-Admin-Key` when `ADMIN_API_KEY` is set.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `RAG_CHUNK_SIZE` | 1200 | Characters per passage |
| `RAG_CHUNK_OVERLAP` | 200 | Characters repeated between passages |
| `RAG_TOP_K` | 5 | Passages added to a chat when the request doesn't say |
| `RAG_CANDIDATES` | 20 | Matches taken from each search method before merging |
| `RAG_MAX_UPLOAD_MB` | 20 | Largest file accepted |
| `RAG_INGEST_CONCURRENCY` | 2 | Documents processed at the same time |

## Limits (for now)

- Scanned PDFs (images only) have no text to extract; OCR isn't supported.
- Every collection is visible to everyone. Access per role comes with RBAC (Layer 5).
- Retrieved passages are marked as reference material, not instructions. Stronger prompt-injection
  checks come with guardrails (Layer 6).
- Indexing runs inside the API process. For heavy loads, move the worker to its own process (planned with Layer 7).

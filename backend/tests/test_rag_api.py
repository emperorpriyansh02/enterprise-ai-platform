"""Knowledge bases end to end: upload -> background indexing -> hybrid search -> chat with citations.

Most tests need Postgres + pgvector (TEST_DATABASE_URL); they're skipped on SQLite.
"""

import asyncio
import json
import uuid

from fastapi.testclient import TestClient

from app.db.session import Database
from app.main import create_app
from app.rag.models import Document
from tests.conftest import make_settings, requires_pgvector
from tests.test_rag_parsers import make_docx, make_pdf

ASK = [{"role": "user", "content": "How many days of annual leave do employees get?"}]
HANDBOOK = (
    "# Employee Handbook\n\nEmployees get 25 days of annual leave per year.\n\n"
    "Unused annual leave can be carried over until March."
)
TRAVEL = "Travel policy: book flights through the portal. Economy class for trips under six hours."


async def wait_ready(api, collection: str, seconds: float = 10.0) -> list[dict]:
    """Poll the document list until nothing is pending/processing; returns the documents."""
    for _ in range(int(seconds / 0.05)):
        documents = (await api.get(f"/v1/collections/{collection}/documents")).json()["data"]
        if all(d["status"] in ("ready", "failed") for d in documents):
            return documents
        await asyncio.sleep(0.05)
    raise AssertionError(f"documents in {collection} never finished: {documents}")


async def make_collection(api, name: str = "hr") -> None:
    response = await api.post("/v1/collections", json={"name": name, "description": "HR docs"})
    assert response.status_code == 201, response.text


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")]


@requires_pgvector
async def test_upload_files_index_and_search(api_factory):
    async with api_factory() as api:
        await make_collection(api)
        files = [
            ("files", ("handbook.md", HANDBOOK.encode(), "text/markdown")),
            ("files", ("policy.docx", make_docx(), "application/octet-stream")),
            ("files", ("router.pdf", make_pdf("Reset the router by holding the button."), "application/pdf")),
            ("files", ("travel.html", f"<title>Travel</title><p>{TRAVEL}</p>".encode(), "text/html")),
        ]
        upload = await api.post("/v1/collections/hr/documents", files=files)
        assert upload.status_code == 202, upload.text
        assert [d["status"] for d in upload.json()["data"]] == ["pending"] * 4

        documents = await wait_ready(api, "hr")
        search = await api.post("/v1/collections/hr/search", json={"query": "annual leave days", "top_k": 3})
        detail = (await api.get("/v1/collections/hr")).json()

    assert {d["title"] for d in documents} == {"Employee Handbook", "Leave Policy", "router", "Travel"}
    assert all(d["status"] == "ready" and d["chunk_count"] >= 1 for d in documents)
    hits = search.json()["hits"]
    assert hits[0]["document_title"] in {"Employee Handbook", "Leave Policy"}
    assert hits[0]["vector_similarity"] is not None and hits[0]["keyword_score"] is not None
    assert detail["documents"]["ready"] == 4 and detail["needs_reindex"] is False
    assert detail["embedding_model"] == "mock/hash@768"


@requires_pgvector
async def test_keyword_search_finds_exact_codes(api_factory):
    async with api_factory() as api:
        await make_collection(api, "support")
        for title, text in [
            ("Fan errors", "Error ZX-4471 means the cooling fan has stopped. Replace the fan."),
            ("Disk errors", "Error QB-1180 means the disk is full. Delete old logs."),
            ("General", "Most errors can be fixed by restarting the device."),
        ]:
            await api.post("/v1/collections/support/documents/text", json={"title": title, "text": text})
        await wait_ready(api, "support")
        hits = (await api.post("/v1/collections/support/search", json={"query": "ZX-4471"})).json()["hits"]

    assert hits[0]["document_title"] == "Fan errors"
    assert hits[0]["keyword_score"] is not None
    assert all(h["keyword_score"] is None for h in hits[1:])  # the others matched by vector only


@requires_pgvector
async def test_keyword_search_matches_any_word_not_all(api_factory):
    async with api_factory() as api:
        await make_collection(api, "support")
        await api.post(
            "/v1/collections/support/documents/text",
            json={"title": "Warranty", "text": "The warranty lasts 24 months from the purchase date."},
        )
        await wait_ready(api, "support")
        hits = (
            await api.post("/v1/collections/support/search", json={"query": "how long is the warranty"})
        ).json()
        stop_words_only = await api.post("/v1/collections/support/search", json={"query": "how is the"})

    assert hits["hits"][0]["keyword_score"] is not None  # "long" is missing, "warranty" still matches
    assert stop_words_only.status_code == 200  # nothing left to match by keyword: vector search only
    assert all(h["keyword_score"] is None for h in stop_words_only.json()["hits"])


@requires_pgvector
async def test_upload_errors(api_factory):
    async with api_factory(rag_max_upload_mb=0.001) as api:  # ~1 KB limit
        await make_collection(api)
        await api.post("/v1/collections/hr/documents/text", json={"title": "H", "text": HANDBOOK})
        duplicate = await api.post(
            "/v1/collections/hr/documents/text", json={"title": "Again", "text": HANDBOOK}
        )
        unsupported = await api.post(
            "/v1/collections/hr/documents", files=[("files", ("photo.png", b"\x89PNG", "image/png"))]
        )
        too_large = await api.post(
            "/v1/collections/hr/documents", files=[("files", ("big.txt", b"x " * 2000, "text/plain"))]
        )
        mixed = await api.post(
            "/v1/collections/hr/documents",
            files=[
                ("files", ("ok.txt", b"Expense claims are paid monthly.", "text/plain")),
                ("files", ("bad.exe", b"MZ", "application/octet-stream")),
            ],
        )
        missing = await api.post(
            "/v1/collections/nope/documents", files=[("files", ("a.txt", b"hi", "text/plain"))]
        )

    assert (duplicate.status_code, duplicate.json()["error"]["code"]) == (409, "duplicate_document")
    assert (unsupported.status_code, unsupported.json()["error"]["code"]) == (415, "unsupported_file_type")
    assert (too_large.status_code, too_large.json()["error"]["code"]) == (413, "file_too_large")
    assert mixed.status_code == 202
    assert [d["filename"] for d in mixed.json()["data"]] == ["ok.txt"]
    assert mixed.json()["errors"][0]["code"] == "unsupported_file_type"
    assert missing.status_code == 404


@requires_pgvector
async def test_delete_and_reindex_document(api_factory):
    async with api_factory() as api:
        await make_collection(api)
        doc = (
            await api.post("/v1/collections/hr/documents/text", json={"title": "T", "text": TRAVEL})
        ).json()
        await wait_ready(api, "hr")

        reindexed = await api.post(f"/v1/collections/hr/documents/{doc['id']}/reindex")
        assert reindexed.status_code == 202 and reindexed.json()["status"] == "pending"
        await wait_ready(api, "hr")
        assert (await api.get(f"/v1/collections/hr/documents/{doc['id']}")).json()["status"] == "ready"

        deleted = await api.delete(f"/v1/collections/hr/documents/{doc['id']}")
        hits = (await api.post("/v1/collections/hr/search", json={"query": "flights"})).json()["hits"]
        gone = await api.get(f"/v1/collections/hr/documents/{doc['id']}")

    assert deleted.status_code == 204
    assert hits == []  # its chunks went with it
    assert gone.status_code == 404


@requires_pgvector
async def test_embedding_model_change_needs_reindex(api_factory):
    async with api_factory() as api:
        await make_collection(api)
        await api.post("/v1/collections/hr/documents/text", json={"title": "H", "text": HANDBOOK})
        await wait_ready(api, "hr")

    async with api_factory(embedding_model="hash-v2") as api:
        detail = (await api.get("/v1/collections/hr")).json()
        stale = await api.post("/v1/collections/hr/search", json={"query": "leave"})
        reindex = await api.post("/v1/collections/hr/reindex")
        await wait_ready(api, "hr")
        fresh = await api.post("/v1/collections/hr/search", json={"query": "leave"})

    assert detail["needs_reindex"] is True and detail["server_embedding_model"] == "mock/hash-v2@768"
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "embedding_model_mismatch")
    assert reindex.status_code == 202 and reindex.json()["embedding_model"] == "mock/hash-v2@768"
    assert fresh.status_code == 200 and fresh.json()["hits"]


@requires_pgvector
async def test_archived_collection_is_hidden_and_unusable(api_factory):
    async with api_factory() as api:
        await make_collection(api)
        await make_collection(api, "other")
        archived = await api.delete("/v1/collections/hr")
        listing = (await api.get("/v1/collections")).json()["data"]
        search = await api.post("/v1/collections/hr/search", json={"query": "x"})
        duplicate_name = await api.post("/v1/collections", json={"name": "other"})

    assert archived.json()["archived"] is True
    assert [c["name"] for c in listing] == ["other"]
    assert (search.status_code, search.json()["error"]["code"]) == (409, "collection_conflict")
    assert duplicate_name.status_code == 409


@requires_pgvector
async def test_chat_adds_sources_and_returns_citations(api_factory):
    async with api_factory() as api:
        await make_collection(api)
        await make_collection(api, "empty")
        await api.post("/v1/collections/hr/documents/text", json={"title": "Handbook", "text": HANDBOOK})
        await api.post("/v1/collections/hr/documents/text", json={"title": "Travel", "text": TRAVEL})
        await wait_ready(api, "hr")

        body = {
            "model": "mock/echo-system",
            "messages": ASK,
            "system": "Be brief.",
            "rag": {"collections": ["hr"]},
        }
        reply = (await api.post("/v1/chat", json=body)).json()
        stream = await api.post("/v1/chat/stream", json=body | {"rag": {"collections": ["hr"], "top_k": 1}})
        no_hits = (
            await api.post("/v1/chat", json=body | {"rag": {"collections": ["empty"]}, "system": None})
        ).json()
        unknown = await api.post("/v1/chat", json=body | {"rag": {"collections": ["nope"]}})
        health = (await api.get("/health")).json()

    # The model received the request's system text, then the numbered sources.
    assert reply["content"].startswith("Be brief.\n\n## Sources")
    assert '[1] "Handbook"' in reply["content"]
    assert reply["citations"][0]["document_title"] == "Handbook" and reply["citations"][0]["index"] == 1
    assert "25 days of annual leave" in reply["citations"][0]["snippet"]

    events = parse_sse(stream.text)
    assert events[0]["type"] == "start" and len(events[0]["citations"]) == 1

    assert no_hits["citations"] == [] and no_hits["content"] == "(no system prompt)"
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "collection_not_found")
    assert health["rag"] == "ok"


@requires_pgvector
async def test_unfinished_documents_are_requeued_on_start(api_factory, database: Database):
    async with api_factory() as api:
        await make_collection(api)
        collection_id = None
        async with database.session() as session:
            from app.rag.repository import RagRepository

            collection_id = (await RagRepository(session).get_collection("hr")).id
            session.add(
                Document(
                    id=uuid.uuid4(),
                    collection_id=collection_id,
                    title="Left behind",
                    filename=None,
                    mime_type="text/plain",
                    size_bytes=10,
                    content_hash="0" * 64,
                    text="This document was processing when the server stopped.",
                    status="processing",
                    chunk_count=0,
                )
            )
            await session.commit()

    async with api_factory() as api:  # a fresh start re-queues it
        documents = await wait_ready(api, "hr")

    assert [(d["title"], d["status"]) for d in documents] == [("Left behind", "ready")]


def test_without_postgres_rag_is_unavailable_but_chat_works():
    sqlite = Database("sqlite+aiosqlite:///:memory:")
    with TestClient(create_app(make_settings(), database=sqlite)) as client:
        listing = client.get("/v1/collections")
        with_rag = client.post("/v1/chat", json={"messages": ASK, "rag": {"collections": ["hr"]}})
        plain = client.post("/v1/chat", json={"messages": ASK})

    assert (listing.status_code, listing.json()["error"]["code"]) == (503, "rag_unavailable")
    assert "PostgreSQL" in listing.json()["error"]["message"]
    assert with_rag.status_code == 503
    assert plain.status_code == 200


def test_without_database_rag_is_unavailable():
    with TestClient(create_app(make_settings())) as client:
        response = client.post("/v1/chat", json={"messages": ASK, "rag": {"collections": ["hr"]}})
    assert (response.status_code, response.json()["error"]["code"]) == (503, "rag_unavailable")

"use client";

import { type DragEvent, type FormEvent, useEffect, useRef, useState } from "react";

import { errorMessage } from "@/components/prompts/messages";
import {
  addTextDocument,
  archiveCollection,
  type CollectionDetail,
  deleteDocument,
  type DocumentStatus,
  getCollection,
  type KnowledgeDocument,
  listDocuments,
  reindexCollection,
  reindexDocument,
  type SearchHit,
  searchCollection,
  type UploadResult,
  uploadDocuments,
} from "@/lib/api";

import { ALERT, BUTTON, INPUT, PRIMARY } from "./styles";

const ACCEPT = ".txt,.md,.markdown,.pdf,.docx,.html,.htm";
const POLL_MS = 2000;

export default function CollectionPanel({
  name,
  adminKey,
  onChanged,
}: {
  name: string;
  adminKey: string;
  onChanged: () => void;
}) {
  const [collection, setCollection] = useState<CollectionDetail | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // Load the collection and its documents on first view and after each change or poll.
  useEffect(() => {
    let cancelled = false;
    Promise.all([getCollection(name), listDocuments(name)])
      .then(([detail, docs]) => {
        if (cancelled) return;
        setCollection(detail);
        setDocuments(docs.data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [name, refreshKey]);

  // While any document is still being indexed, check again every 2 seconds.
  const indexing = documents.some((d) => d.status === "pending" || d.status === "processing");
  useEffect(() => {
    if (!indexing) return;
    const timer = setTimeout(() => {
      setRefreshKey((k) => k + 1);
      onChanged();
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [indexing, refreshKey, onChanged]);

  // Run a change, then refresh this panel and the list. Gives back the result, or undefined on failure.
  const run = async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      setRefreshKey((k) => k + 1);
      onChanged();
      return result;
    } catch (err) {
      setError(errorMessage(err));
      return undefined;
    } finally {
      setBusy(false);
    }
  };

  if (!collection) {
    return error ? (
      <p role="alert" className={ALERT}>
        {error}
      </p>
    ) : (
      <p className="text-sm text-muted">Loading…</p>
    );
  }

  const writable = !collection.archived;

  return (
    <article className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="mr-auto font-mono text-lg font-semibold">{collection.name}</h2>
          {collection.archived ? (
            <span className="rounded-full bg-surface px-2 py-0.5 text-xs text-muted">Archived</span>
          ) : (
            <button
              type="button"
              disabled={busy}
              className={BUTTON}
              onClick={() => {
                if (window.confirm(`Archive "${collection.name}"? Chat will no longer be able to search it.`)) {
                  void run(() => archiveCollection(collection.name, adminKey));
                }
              }}
            >
              Archive
            </button>
          )}
        </div>
        <p className="text-sm text-muted">{collection.description || "No description."}</p>
        <p className="text-xs text-muted">
          {collection.documents.ready} ready · {collection.documents.pending + collection.documents.processing}{" "}
          indexing · {collection.documents.failed} failed · Embeddings:{" "}
          <code className="rounded bg-surface px-1">{collection.embedding_model}</code> · Use in chat as{" "}
          <code className="rounded bg-surface px-1">{`"rag": {"collections": ["${collection.name}"]}`}</code>
        </p>
        {collection.needs_reindex && writable && (
          <div role="status" className="flex flex-wrap items-center gap-3 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
            <span className="mr-auto">
              The server now uses <code>{collection.server_embedding_model}</code>. Search won&apos;t work until
              this collection is re-indexed.
            </span>
            <button
              type="button"
              disabled={busy}
              className={PRIMARY}
              onClick={() => void run(() => reindexCollection(collection.name, adminKey))}
            >
              Re-index collection
            </button>
          </div>
        )}
        {error && (
          <p role="alert" className={ALERT}>
            {error}
          </p>
        )}
      </header>

      {writable && (
        <UploadBox
          busy={busy}
          onUpload={(files) => run(() => uploadDocuments(collection.name, files, adminKey))}
        />
      )}
      {writable && (
        <PasteTextForm
          busy={busy}
          onSave={(title, text) => run(() => addTextDocument(collection.name, { title, text }, adminKey))}
        />
      )}

      <DocumentTable
        documents={documents}
        canChange={writable && !busy}
        onReindex={(id) => void run(() => reindexDocument(collection.name, id, adminKey))}
        onDelete={(doc) => {
          if (window.confirm(`Delete "${doc.title}" and its passages?`)) {
            void run(() => deleteDocument(collection.name, doc.id, adminKey));
          }
        }}
      />

      {!collection.archived && <SearchTester name={collection.name} />}
    </article>
  );
}

function UploadBox({
  busy,
  onUpload,
}: {
  busy: boolean;
  onUpload: (files: File[]) => Promise<UploadResult | undefined>;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [refused, setRefused] = useState<UploadResult["errors"]>([]);

  const upload = async (files: File[]) => {
    if (files.length === 0) return;
    setRefused([]);
    const result = await onUpload(files);
    if (result) setRefused(result.errors);
    if (inputRef.current) inputRef.current.value = "";
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    void upload(Array.from(e.dataTransfer.files));
  };

  return (
    <section aria-label="Upload files" className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold">Add files</h3>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex flex-col items-center gap-2 rounded-md border border-dashed px-4 py-6 text-center text-sm ${
          dragging ? "border-accent bg-accent/5" : "border-line"
        }`}
      >
        <p className="text-muted">Drop files here, or</p>
        <button type="button" disabled={busy} className={BUTTON} onClick={() => inputRef.current?.click()}>
          Choose files
        </button>
        <p className="text-xs text-muted">.txt, .md, .pdf, .docx, .html · text-based PDFs only</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          aria-label="Files to upload"
          className="sr-only"
          onChange={(e) => void upload(Array.from(e.target.files ?? []))}
        />
      </div>
      {refused.length > 0 && (
        <ul role="alert" className={ALERT}>
          {refused.map((r) => (
            <li key={r.filename}>
              <strong>{r.filename}</strong>: {r.message}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function PasteTextForm({
  busy,
  onSave,
}: {
  busy: boolean;
  onSave: (title: string, text: string) => Promise<KnowledgeDocument | undefined>;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (await onSave(title, text)) {
      setTitle("");
      setText("");
      setOpen(false);
    }
  };

  if (!open) {
    return (
      <div>
        <button type="button" className={BUTTON} onClick={() => setOpen(true)}>
          Paste text instead
        </button>
      </div>
    );
  }
  return (
    <form onSubmit={submit} aria-label="Paste text" className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold">Paste text</h3>
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        required
        placeholder="Title"
        aria-label="Document title"
        className={INPUT}
      />
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        required
        rows={6}
        placeholder="Paste the document text…"
        aria-label="Document text"
        className={INPUT}
      />
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className={PRIMARY}>
          Add document
        </button>
        <button type="button" className={BUTTON} onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function DocumentTable({
  documents,
  canChange,
  onReindex,
  onDelete,
}: {
  documents: KnowledgeDocument[];
  canChange: boolean;
  onReindex: (id: string) => void;
  onDelete: (doc: KnowledgeDocument) => void;
}) {
  return (
    <section aria-label="Documents">
      <h3 className="mb-2 text-sm font-semibold">Documents ({documents.length})</h3>
      {documents.length === 0 ? (
        <p className="text-sm text-muted">No documents yet.</p>
      ) : (
        <div className="relative overflow-x-auto rounded-md border border-line">
          <table className="w-full min-w-[36rem] text-left text-sm">
            <thead className="bg-surface text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Title</th>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Size</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Passages</th>
                <th className="px-3 py-2 font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {documents.map((doc) => (
                <tr key={doc.id} data-status={doc.status}>
                  <td className="px-3 py-2">
                    <span className="block">{doc.title}</span>
                    {doc.filename && doc.filename !== doc.title && (
                      <span className="block text-xs text-muted">{doc.filename}</span>
                    )}
                    {doc.error && <span className="block text-xs text-danger">{doc.error}</span>}
                  </td>
                  <td className="px-3 py-2 text-muted">{typeLabel(doc.mime_type)}</td>
                  <td className="px-3 py-2 text-muted">{formatBytes(doc.size_bytes)}</td>
                  <td className="px-3 py-2">
                    <StatusBadge status={doc.status} />
                  </td>
                  <td className="px-3 py-2 text-muted">{doc.status === "ready" ? doc.chunk_count : "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <button
                      type="button"
                      disabled={!canChange}
                      className={`${BUTTON} mr-1`}
                      onClick={() => onReindex(doc.id)}
                    >
                      Re-index
                    </button>
                    <button type="button" disabled={!canChange} className={BUTTON} onClick={() => onDelete(doc)}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SearchTester({ name }: { name: string }) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

  const search = async (e: FormEvent) => {
    e.preventDefault();
    setSearching(true);
    setError(null);
    try {
      setHits((await searchCollection(name, { query, top_k: 5 })).hits);
    } catch (err) {
      setHits(null);
      setError(errorMessage(err));
    } finally {
      setSearching(false);
    }
  };

  return (
    <section aria-label="Test search" className="flex flex-col gap-2 border-t border-line pt-5">
      <h3 className="text-sm font-semibold">Test search</h3>
      <p className="text-xs text-muted">See which passages a chat question would retrieve. No AI model is called.</p>
      <form onSubmit={search} className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          required
          placeholder="Ask a question…"
          aria-label="Search query"
          className={INPUT}
        />
        <button type="submit" disabled={searching} className={PRIMARY}>
          Search
        </button>
      </form>
      {error && <p className="text-sm text-danger">{error}</p>}
      {hits !== null &&
        (hits.length === 0 ? (
          <p className="text-sm text-muted">No passages found. Are the documents ready?</p>
        ) : (
          <ol className="flex flex-col gap-2" aria-label="Search results">
            {hits.map((hit, i) => (
              <li key={hit.chunk_id} className="rounded-md border border-line p-3 text-sm">
                <p className="mb-1 flex flex-wrap items-center gap-x-3 text-xs text-muted">
                  <span className="font-medium text-foreground">
                    [{i + 1}] {hit.document_title}
                  </span>
                  <span>part {hit.position + 1}</span>
                  <span>score {hit.score.toFixed(4)}</span>
                  <span>
                    {hit.vector_similarity !== null ? `meaning ${hit.vector_similarity.toFixed(2)}` : "no meaning match"}
                  </span>
                  <span>{hit.keyword_score !== null ? "keyword match" : "no keyword match"}</span>
                </p>
                <p className="line-clamp-4 whitespace-pre-wrap">{hit.content}</p>
              </li>
            ))}
          </ol>
        ))}
    </section>
  );
}

const STATUS_STYLES: Record<DocumentStatus, string> = {
  pending: "bg-surface text-muted",
  processing: "animate-pulse bg-accent/15 text-accent",
  ready: "bg-success/15 text-success",
  failed: "bg-danger/15 text-danger",
};

function StatusBadge({ status }: { status: DocumentStatus }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}>
      {status[0].toUpperCase() + status.slice(1)}
    </span>
  );
}

const TYPE_LABELS: Record<string, string> = {
  "text/plain": "Text",
  "text/markdown": "Markdown",
  "application/pdf": "PDF",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
  "text/html": "HTML",
};

function typeLabel(mimeType: string): string {
  return TYPE_LABELS[mimeType] ?? mimeType;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

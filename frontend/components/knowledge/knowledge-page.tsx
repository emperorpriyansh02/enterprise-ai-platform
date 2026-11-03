"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { errorMessage } from "@/components/prompts/messages";
import RolePicker from "@/components/role-picker";
import { type CollectionSummary, createCollection, listCollections, type RoleName } from "@/lib/api";

import CollectionPanel from "./collection-panel";
import { ALERT, BUTTON, INPUT, PRIMARY } from "./styles";

export default function KnowledgePage() {
  const { can } = useAuth();
  const canWrite = can("knowledge:write");
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // Load the list on first view, when "Show archived" changes, and after each change (reload).
  useEffect(() => {
    let cancelled = false;
    listCollections({ includeArchived: showArchived })
      .then((list) => {
        if (cancelled) return;
        setCollections(list.data);
        setLoadError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showArchived, refreshKey]);

  const reload = useCallback(() => setRefreshKey((k) => k + 1), []);

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col px-4">
      <header className="flex flex-wrap items-center gap-3 border-b border-line py-3">
        <div className="mr-auto">
          <h1 className="text-base font-semibold tracking-tight">Knowledge</h1>
          <p className="text-xs text-muted">
            Upload documents into collections. Chat can search them and cite its sources.
          </p>
        </div>
        {canWrite && (
          <button
            type="button"
            className={PRIMARY}
            onClick={() => {
              setCreating(true);
              setSelected(null);
            }}
          >
            New collection
          </button>
        )}
      </header>

      {loadError && (
        <div role="alert" className={`mt-3 ${ALERT}`}>
          {loadError}
        </div>
      )}

      <div className="grid flex-1 grid-cols-[minmax(0,1fr)] gap-6 overflow-y-auto py-4 md:grid-cols-[17rem_minmax(0,1fr)]">
        <aside aria-label="Collection list">
          <label className="mb-2 flex items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            Show archived
          </label>
          {loading ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : collections.length === 0 ? (
            <p className="text-sm text-muted">No collections yet. Create one with “New collection”.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {collections.map((c) => {
                const total =
                  c.documents.pending + c.documents.processing + c.documents.ready + c.documents.failed;
                return (
                  <li key={c.name}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelected(c.name);
                        setCreating(false);
                      }}
                      aria-current={selected === c.name ? "true" : undefined}
                      className={`w-full rounded-md px-3 py-2 text-left ${
                        selected === c.name ? "bg-surface" : "hover:bg-surface"
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate font-mono text-sm">{c.name}</span>
                        <span className="shrink-0 text-xs text-muted">
                          {c.archived ? "archived" : `${c.documents.ready}/${total} ready`}
                        </span>
                      </span>
                      {c.description && (
                        <span className="block truncate text-xs text-muted">{c.description}</span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <section aria-label="Collection details" className="min-w-0">
          {creating ? (
            <NewCollectionForm
              onCancel={() => setCreating(false)}
              onCreated={(name) => {
                setCreating(false);
                setSelected(name);
                reload();
              }}
            />
          ) : selected ? (
            <CollectionPanel key={selected} name={selected} canWrite={canWrite} onChanged={reload} />
          ) : (
            <p className="text-sm text-muted">Select a collection to see and add its documents.</p>
          )}
        </section>
      </div>
    </div>
  );
}

function NewCollectionForm({
  onCreated,
  onCancel,
}: {
  onCreated: (name: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [roles, setRoles] = useState<RoleName[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await createCollection({ name, description, allowed_roles: roles });
      onCreated(created.name);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex max-w-xl flex-col gap-3">
      <h2 className="text-base font-semibold">New collection</h2>
      <label className="text-sm">
        <span className="mb-1 block text-xs font-medium text-muted">Name (lowercase words with hyphens)</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          pattern="[a-z0-9]+(-[a-z0-9]+)*"
          placeholder="hr-policies"
          className={`${INPUT} font-mono`}
        />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs font-medium text-muted">Description</span>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What's in it, who it's for"
          className={INPUT}
        />
      </label>
      <RolePicker value={roles} onChange={setRoles} disabled={saving} />
      {error && (
        <p role="alert" className={ALERT}>
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={saving} className={PRIMARY}>
          {saving ? "Creating…" : "Create collection"}
        </button>
        <button type="button" onClick={onCancel} className={BUTTON}>
          Cancel
        </button>
      </div>
    </form>
  );
}

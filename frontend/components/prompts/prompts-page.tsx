"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { createPrompt, listPrompts, type PromptSummary } from "@/lib/api";
import { extractVariables } from "@/lib/template";

import { AdminKeyInput, useAdminKey } from "./admin-key";
import { errorMessage } from "./messages";
import PromptDetailPanel, { VariableChips } from "./prompt-detail";

const INPUT = "w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm";
const BUTTON = "rounded-md border border-line px-2.5 py-1.5 text-sm hover:bg-surface disabled:opacity-40";
const PRIMARY = "rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:opacity-40";

export default function PromptsPage() {
  const [adminKey, setAdminKey] = useAdminKey();
  const [prompts, setPrompts] = useState<PromptSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [refreshKey, setRefreshKey] = useState(0);

  // Load the list on first view, when "Show archived" changes, and after each change (reload).
  useEffect(() => {
    let cancelled = false;
    listPrompts({ includeArchived: showArchived })
      .then((list) => {
        if (cancelled) return;
        setPrompts(list.data);
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
          <h1 className="text-base font-semibold tracking-tight">Prompts</h1>
          <p className="text-xs text-muted">
            Versioned system prompts. Every change is a new version; activate any version to roll back.
          </p>
        </div>
        <AdminKeyInput value={adminKey} onChange={setAdminKey} />
        <button
          type="button"
          className={PRIMARY}
          onClick={() => {
            setCreating(true);
            setSelected(null);
          }}
        >
          New prompt
        </button>
      </header>

      {loadError && (
        <div role="alert" className="mt-3 rounded-md border border-danger/40 bg-danger/10 p-3 text-sm">
          {loadError}
        </div>
      )}

      <div className="grid flex-1 grid-cols-[minmax(0,1fr)] gap-6 overflow-y-auto py-4 md:grid-cols-[17rem_minmax(0,1fr)]">
        <aside aria-label="Prompt list">
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
          ) : prompts.length === 0 ? (
            <p className="text-sm text-muted">No prompts yet. Create one with “New prompt”.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {prompts.map((p) => (
                <li key={p.name}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelected(p.name);
                      setCreating(false);
                    }}
                    aria-current={selected === p.name ? "true" : undefined}
                    className={`w-full rounded-md px-3 py-2 text-left ${
                      selected === p.name ? "bg-surface" : "hover:bg-surface"
                    }`}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-sm">{p.name}</span>
                      <span className="shrink-0 text-xs text-muted">
                        {p.archived ? "archived" : `v${p.active_version} of ${p.latest_version}`}
                      </span>
                    </span>
                    {p.description && (
                      <span className="block truncate text-xs text-muted">{p.description}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section aria-label="Prompt details" className="min-w-0">
          {creating ? (
            <NewPromptForm
              adminKey={adminKey}
              onCancel={() => setCreating(false)}
              onCreated={(name) => {
                setCreating(false);
                setSelected(name);
                reload();
              }}
            />
          ) : selected ? (
            <PromptDetailPanel key={selected} name={selected} adminKey={adminKey} onChanged={reload} />
          ) : (
            <p className="text-sm text-muted">Select a prompt to see its versions.</p>
          )}
        </section>
      </div>
    </div>
  );
}

function NewPromptForm({
  adminKey,
  onCreated,
  onCancel,
}: {
  adminKey: string;
  onCreated: (name: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [template, setTemplate] = useState("");
  const [note, setNote] = useState("Initial version");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await createPrompt({ name, description, template, note }, adminKey);
      onCreated(created.name);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex max-w-2xl flex-col gap-3">
      <h2 className="text-base font-semibold">New prompt</h2>
      <label className="text-sm">
        <span className="mb-1 block text-xs font-medium text-muted">Name (lowercase words with hyphens)</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          pattern="[a-z0-9]+(-[a-z0-9]+)*"
          placeholder="support-agent"
          className={`${INPUT} font-mono`}
        />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs font-medium text-muted">Description</span>
        <input value={description} onChange={(e) => setDescription(e.target.value)} className={INPUT} />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs font-medium text-muted">
          Template. Use {"{{ variable }}"} for values filled in per request.
        </span>
        <textarea
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
          required
          rows={8}
          placeholder="You are a support agent for {{ company_name }}…"
          className={`${INPUT} font-mono`}
        />
      </label>
      <VariableChips names={extractVariables(template)} />
      <label className="text-sm">
        <span className="mb-1 block text-xs font-medium text-muted">Version note</span>
        <input value={note} onChange={(e) => setNote(e.target.value)} className={INPUT} />
      </label>
      {error && (
        <p role="alert" className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={saving} className={PRIMARY}>
          {saving ? "Creating…" : "Create prompt"}
        </button>
        <button type="button" onClick={onCancel} className={BUTTON}>
          Cancel
        </button>
      </div>
    </form>
  );
}

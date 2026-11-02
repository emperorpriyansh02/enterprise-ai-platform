"use client";

import { useEffect, useState } from "react";

import {
  activatePromptVersion,
  addPromptVersion,
  archivePrompt,
  getPrompt,
  type PromptDetail,
  type PromptVersion,
  renderPrompt,
  updatePromptDescription,
  type VariableValues,
} from "@/lib/api";
import { extractVariables } from "@/lib/template";

import { errorMessage } from "./messages";

const INPUT = "w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm";
const BUTTON = "rounded-md border border-line px-2.5 py-1 text-sm hover:bg-surface disabled:opacity-40";
const PRIMARY = "rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:opacity-40";

export default function PromptDetailPanel({
  name,
  adminKey,
  onChanged,
}: {
  name: string;
  adminKey: string;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<PromptDetail | null>(null);
  const [viewing, setViewing] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editingDescription, setEditingDescription] = useState(false);
  const [description, setDescription] = useState("");

  const [refreshKey, setRefreshKey] = useState(0);

  // Load the prompt on first view and after each successful change.
  useEffect(() => {
    let cancelled = false;
    getPrompt(name)
      .then((fresh) => {
        if (cancelled) return;
        setDetail(fresh);
        setViewing((current) => current ?? fresh.active_version);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [name, refreshKey]);

  // Run a change, then refresh this panel and the list. Gives back the change's result,
  // or undefined if it failed (the error is shown in the panel).
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

  const saveVersion = async (template: string, note: string, activate: boolean): Promise<boolean> => {
    const created = await run(() => addPromptVersion(name, { template, note, activate }, adminKey));
    if (created) setViewing(created.version);
    return created !== undefined;
  };

  if (!detail) {
    return error ? (
      <p role="alert" className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm">
        {error}
      </p>
    ) : (
      <p className="text-sm text-muted">Loading…</p>
    );
  }

  const shown = detail.versions.find((v) => v.version === viewing) ?? detail.versions[0];

  return (
    <article className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="mr-auto font-mono text-lg font-semibold">{detail.name}</h2>
          {detail.archived ? (
            <span className="rounded-full bg-surface px-2 py-0.5 text-xs text-muted">Archived</span>
          ) : (
            <button
              type="button"
              disabled={busy}
              className={BUTTON}
              onClick={() => {
                if (window.confirm(`Archive "${detail.name}"? Chat requests will no longer be able to use it.`)) {
                  void run(() => archivePrompt(detail.name, adminKey));
                }
              }}
            >
              Archive
            </button>
          )}
        </div>
        {editingDescription ? (
          <div className="flex gap-2">
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              aria-label="Description"
              className={INPUT}
            />
            <button
              type="button"
              disabled={busy}
              className={PRIMARY}
              onClick={async () => {
                if (await run(() => updatePromptDescription(detail.name, description, adminKey))) {
                  setEditingDescription(false);
                }
              }}
            >
              Save
            </button>
            <button type="button" className={BUTTON} onClick={() => setEditingDescription(false)}>
              Cancel
            </button>
          </div>
        ) : (
          <p className="text-sm text-muted">
            {detail.description || "No description."}{" "}
            {!detail.archived && (
              <button
                type="button"
                className="text-accent underline-offset-2 hover:underline"
                onClick={() => {
                  setDescription(detail.description);
                  setEditingDescription(true);
                }}
              >
                Edit
              </button>
            )}
          </p>
        )}
        <p className="text-xs text-muted">
          Active: <strong className="text-foreground">v{detail.active_version}</strong> · Latest: v
          {detail.latest_version} · Use in chat as{" "}
          <code className="rounded bg-surface px-1">{`"prompt": {"name": "${detail.name}"}`}</code>
        </p>
        {error && (
          <p role="alert" className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm">
            {error}
          </p>
        )}
      </header>

      <VersionList
        versions={detail.versions}
        activeVersion={detail.active_version}
        viewing={shown?.version ?? null}
        canChange={!detail.archived && !busy}
        onView={setViewing}
        onActivate={(version) => void run(() => activatePromptVersion(detail.name, version, adminKey))}
      />

      {shown && (
        <section aria-label={`Version ${shown.version}`} className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">
            Version {shown.version}
            {shown.is_active && <span className="ml-2 text-xs font-normal text-success">active</span>}
          </h3>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface p-3 font-mono text-xs leading-relaxed">
            {shown.template}
          </pre>
          <VariableChips names={shown.variables} />
          <RenderPreview key={`${detail.name}-${shown.version}`} name={detail.name} version={shown} />
        </section>
      )}

      {!detail.archived && shown && (
        <VersionEditor
          key={`editor-${shown.version}`}
          startFrom={shown}
          busy={busy}
          onSave={saveVersion}
        />
      )}
    </article>
  );
}

function VersionList({
  versions,
  activeVersion,
  viewing,
  canChange,
  onView,
  onActivate,
}: {
  versions: PromptVersion[];
  activeVersion: number | null;
  viewing: number | null;
  canChange: boolean;
  onView: (version: number) => void;
  onActivate: (version: number) => void;
}) {
  return (
    <section aria-label="Versions">
      <h3 className="mb-2 text-sm font-semibold">Versions</h3>
      <ul className="divide-y divide-line rounded-md border border-line">
        {versions.map((v) => (
          <li
            key={v.version}
            className={`flex flex-wrap items-center gap-3 px-3 py-2 text-sm ${
              viewing === v.version ? "bg-surface" : ""
            }`}
          >
            <span className="w-8 font-mono font-medium">v{v.version}</span>
            <span className="min-w-0 flex-1 truncate text-muted">{v.note || "—"}</span>
            <span className="text-xs text-muted">{new Date(v.created_at).toLocaleString()}</span>
            {v.is_active ? (
              <span className="rounded-full bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
                Active
              </span>
            ) : (
              <button type="button" disabled={!canChange} className={BUTTON} onClick={() => onActivate(v.version)}>
                {activeVersion !== null && v.version < activeVersion ? `Roll back to v${v.version}` : "Make active"}
              </button>
            )}
            <button
              type="button"
              className={BUTTON}
              onClick={() => onView(v.version)}
              aria-pressed={viewing === v.version}
            >
              View
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RenderPreview({ name, version }: { name: string; version: PromptVersion }) {
  const [values, setValues] = useState<VariableValues>({});
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const preview = async () => {
    setError(null);
    try {
      const result = await renderPrompt(name, { version: version.version, variables: values });
      setText(result.text);
    } catch (err) {
      setText(null);
      setError(errorMessage(err));
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-md border border-line p-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">Preview</h4>
      {version.variables.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {version.variables.map((variable) => (
            <label key={variable} className="flex items-center gap-2 text-sm">
              <span className="font-mono text-xs text-muted">{variable}</span>
              <input
                value={values[variable] ?? ""}
                onChange={(e) => setValues((prev) => ({ ...prev, [variable]: e.target.value }))}
                aria-label={`Preview value for ${variable}`}
                className="w-44 rounded-md border border-line bg-surface px-2 py-1 text-sm"
              />
            </label>
          ))}
        </div>
      )}
      <div>
        <button type="button" className={BUTTON} onClick={() => void preview()}>
          Render v{version.version}
        </button>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      {text !== null && (
        <pre aria-label="Rendered prompt" className="whitespace-pre-wrap rounded-md bg-surface p-3 text-sm">
          {text}
        </pre>
      )}
    </div>
  );
}

function VersionEditor({
  startFrom,
  busy,
  onSave,
}: {
  startFrom: PromptVersion;
  busy: boolean;
  onSave: (template: string, note: string, activate: boolean) => Promise<boolean>;
}) {
  const [template, setTemplate] = useState(startFrom.template);
  const [note, setNote] = useState("");
  const [activate, setActivate] = useState(true);
  const unchanged = template === startFrom.template;

  return (
    <section aria-label="New version" className="flex flex-col gap-3 border-t border-line pt-5">
      <h3 className="text-sm font-semibold">New version (starting from v{startFrom.version})</h3>
      <textarea
        value={template}
        onChange={(e) => setTemplate(e.target.value)}
        rows={8}
        aria-label="New version template"
        className={`${INPUT} font-mono`}
      />
      <VariableChips names={extractVariables(template)} />
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="What changed? (shown in the version list)"
        aria-label="Version note"
        className={INPUT}
      />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={activate} onChange={(e) => setActivate(e.target.checked)} />
        Make it active right away
      </label>
      <div>
        <button
          type="button"
          disabled={busy || unchanged || !template.trim()}
          className={PRIMARY}
          onClick={() => void onSave(template, note, activate)}
        >
          Save as new version
        </button>
        {unchanged && <span className="ml-3 text-xs text-muted">Edit the template to save a new version.</span>}
      </div>
    </section>
  );
}

export function VariableChips({ names }: { names: string[] }) {
  if (names.length === 0) return <p className="text-xs text-muted">No variables.</p>;
  return (
    <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
      Variables:
      {names.map((n) => (
        <code key={n} className="rounded bg-surface px-1.5 py-0.5 text-foreground">
          {n}
        </code>
      ))}
    </p>
  );
}

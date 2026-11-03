"use client";

import { useCallback, useEffect, useState } from "react";

import { ALERT, PRIMARY } from "@/components/knowledge/styles";
import { AdminKeyInput, useAdminKey } from "@/components/prompts/admin-key";
import { listMcpServers, type McpServer } from "@/lib/api";

import { toolsError } from "./messages";
import ServerForm from "./server-form";
import ServerPanel from "./server-panel";

export default function ToolsPage() {
  const [adminKey, setAdminKey] = useAdminKey();
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // Load on first view, when the admin key changes (the list needs it), and after each change.
  useEffect(() => {
    let cancelled = false;
    listMcpServers(adminKey)
      .then((list) => {
        if (cancelled) return;
        setServers(list.data);
        setLoadError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setServers([]);
        setLoadError(toolsError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [adminKey, refreshKey]);

  const reload = useCallback(() => setRefreshKey((k) => k + 1), []);
  const current = servers.find((s) => s.name === selected);

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col px-4">
      <header className="flex flex-wrap items-center gap-3 border-b border-line py-3">
        <div className="mr-auto">
          <h1 className="text-base font-semibold tracking-tight">Tools</h1>
          <p className="text-xs text-muted">
            Connect MCP servers and choose which of their tools chat models may use. New tools start switched
            off.
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
          Add server
        </button>
      </header>

      {loadError && (
        <div role="alert" className={`mt-3 ${ALERT}`}>
          {loadError}
        </div>
      )}

      <div className="grid flex-1 grid-cols-[minmax(0,1fr)] gap-6 overflow-y-auto py-4 md:grid-cols-[17rem_minmax(0,1fr)]">
        <aside aria-label="MCP servers">
          {loading ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : servers.length === 0 ? (
            <p className="text-sm text-muted">No MCP servers yet. Add one with “Add server”.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {servers.map((s) => {
                const on = s.tools.filter((t) => t.enabled).length;
                return (
                  <li key={s.name}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelected(s.name);
                        setCreating(false);
                      }}
                      aria-current={selected === s.name ? "true" : undefined}
                      className={`w-full rounded-md px-3 py-2 text-left ${
                        selected === s.name ? "bg-surface" : "hover:bg-surface"
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate font-mono text-sm">{s.name}</span>
                        <span className={`shrink-0 text-xs ${s.last_error ? "text-danger" : "text-muted"}`}>
                          {!s.enabled
                            ? "disabled"
                            : s.last_error
                              ? "error"
                              : `${on}/${s.tools.length} tools on`}
                        </span>
                      </span>
                      <span className="block truncate text-xs text-muted">
                        {s.transport === "stdio" ? "local command" : "remote URL"}
                        {s.description && ` · ${s.description}`}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <section aria-label="Server details" className="min-w-0">
          {creating ? (
            <ServerForm
              adminKey={adminKey}
              onCancel={() => setCreating(false)}
              onCreated={(name) => {
                setCreating(false);
                setSelected(name);
                reload();
              }}
            />
          ) : current ? (
            <ServerPanel
              key={current.name}
              server={current}
              adminKey={adminKey}
              onChanged={reload}
              onDeleted={() => {
                setSelected(null);
                reload();
              }}
            />
          ) : (
            <p className="text-sm text-muted">Select a server to see its tools.</p>
          )}
        </section>
      </div>
    </div>
  );
}

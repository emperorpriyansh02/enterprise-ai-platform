"use client";

import { useState } from "react";

import { ALERT, BUTTON, INPUT, PRIMARY } from "@/components/knowledge/styles";
import {
  callMcpTool,
  deleteMcpServer,
  type McpServer,
  type McpTool,
  refreshMcpServer,
  setMcpToolEnabled,
  type ToolCallOutcome,
  updateMcpServer,
} from "@/lib/api";

import { toolsError } from "./messages";

const BADGE = "rounded px-1.5 py-0.5 text-[11px] font-medium";

export default function ServerPanel({
  server,
  adminKey,
  onChanged,
  onDeleted,
}: {
  server: McpServer;
  adminKey: string;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null); // what's running: "refresh", a tool name...
  const [error, setError] = useState<string | null>(null);
  const [trying, setTrying] = useState<string | null>(null);
  // Switches flipped here, shown at once while the change is saved (the list reload follows).
  const [flipped, setFlipped] = useState<Record<string, boolean>>({});

  /** Run one change, show its error if any, then reload the list (which re-renders this panel). */
  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(toolsError(err));
    } finally {
      setBusy(null);
    }
  };

  const toggleTool = (tool: McpTool, enabled: boolean) => {
    if (
      enabled &&
      tool.destructive &&
      !window.confirm(
        `"${tool.name}" can change or delete data, and models run tools without asking first. Switch it on?`,
      )
    ) {
      return;
    }
    setFlipped((f) => ({ ...f, [tool.name]: enabled }));
    void run(tool.name, async () => {
      try {
        await setMcpToolEnabled(server.name, tool.name, enabled, adminKey);
      } catch (err) {
        setFlipped((f) => {
          const rest = { ...f };
          delete rest[tool.name]; // not saved: show the stored value again
          return rest;
        });
        throw err;
      }
    });
  };

  const remove = async () => {
    if (!window.confirm(`Delete the MCP server "${server.name}" and its tool settings?`)) return;
    setBusy("delete");
    setError(null);
    try {
      await deleteMcpServer(server.name, adminKey);
      onDeleted();
    } catch (err) {
      setError(toolsError(err));
      setBusy(null);
    }
  };

  const enabledCount = server.tools.filter((t) => t.enabled).length;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex flex-wrap items-center gap-2 text-base font-semibold">
            <span className="font-mono">{server.name}</span>
            <span className={`${BADGE} bg-surface text-muted`}>
              {server.transport === "stdio" ? "local command" : "remote URL"}
            </span>
            {!server.enabled && <span className={`${BADGE} bg-warning/15 text-warning`}>disabled</span>}
          </h2>
          {server.description && <p className="text-sm text-muted">{server.description}</p>}
          <p className="mt-1 break-all font-mono text-xs text-muted">
            {server.transport === "stdio" ? [server.command, ...server.args].join(" ") : server.url}
          </p>
          {(server.env_names.length > 0 || server.header_names.length > 0) && (
            <p className="mt-1 text-xs text-muted">
              Secrets (encrypted): {[...server.env_names, ...server.header_names].join(", ")}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={BUTTON}
            disabled={busy !== null}
            onClick={() => {
              setFlipped({}); // a refresh can switch tools off: show what the server says
              void run("refresh", () => refreshMcpServer(server.name, adminKey));
            }}
          >
            {busy === "refresh" ? "Connecting…" : "Refresh tools"}
          </button>
          <button
            type="button"
            className={BUTTON}
            disabled={busy !== null}
            onClick={() =>
              void run("enable", () => updateMcpServer(server.name, { enabled: !server.enabled }, adminKey))
            }
          >
            {server.enabled ? "Disable server" : "Enable server"}
          </button>
          <button type="button" className={`${BUTTON} text-danger`} disabled={busy !== null} onClick={remove}>
            Delete
          </button>
        </div>
      </div>

      {server.last_error ? (
        <p role="alert" className={ALERT}>
          Last connection failed: {server.last_error}
        </p>
      ) : (
        server.last_connected_at && (
          <p className="text-xs text-muted">
            Tools last read {new Date(server.last_connected_at).toLocaleString()} · {enabledCount} of{" "}
            {server.tools.length} switched on
          </p>
        )
      )}
      {error && (
        <p role="alert" className={ALERT}>
          {error}
        </p>
      )}

      {server.tools.length === 0 ? (
        <p className="text-sm text-muted">No tools listed. Fix the connection, then “Refresh tools”.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line rounded-md border border-line" aria-label="Tools">
          {server.tools.map((tool) => (
            <li key={tool.name} className="px-3 py-2.5">
              <div className="flex flex-wrap items-start gap-3">
                <label className="flex min-w-0 flex-1 items-start gap-2.5">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={flipped[tool.name] ?? tool.enabled}
                    disabled={busy !== null}
                    onChange={(e) => toggleTool(tool, e.target.checked)}
                    aria-label={`Allow ${tool.name}`}
                  />
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-sm">{tool.name}</span>
                      {tool.read_only && <span className={`${BADGE} bg-surface text-muted`}>read-only</span>}
                      {tool.destructive && (
                        <span className={`${BADGE} bg-danger/10 text-danger`}>changes data</span>
                      )}
                    </span>
                    {tool.description && <span className="block text-xs text-muted">{tool.description}</span>}
                  </span>
                </label>
                <button
                  type="button"
                  className={BUTTON}
                  aria-expanded={trying === tool.name}
                  onClick={() => setTrying((t) => (t === tool.name ? null : tool.name))}
                >
                  Try
                </button>
              </div>
              {trying === tool.name && <TryTool server={server.name} tool={tool} adminKey={adminKey} />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Run a tool directly with JSON arguments, to check it works before switching it on. */
function TryTool({ server, tool, adminKey }: { server: string; tool: McpTool; adminKey: string }) {
  const [args, setArgs] = useState(() => exampleArguments(tool));
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<ToolCallOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runTool = async () => {
    setError(null);
    setOutcome(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(args || "{}");
    } catch {
      setError('Arguments must be valid JSON, e.g. {"timezone": "UTC"}.');
      return;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setError("Arguments must be a JSON object.");
      return;
    }
    setRunning(true);
    try {
      setOutcome(await callMcpTool(server, tool.name, parsed as Record<string, unknown>, adminKey));
    } catch (err) {
      setError(toolsError(err));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="mt-2 flex flex-col gap-2 rounded-md bg-surface p-3">
      <label className="text-xs font-medium text-muted" htmlFor={`args-${tool.name}`}>
        Arguments (JSON)
        {tool.destructive && " · this tool can change data, even in a test"}
      </label>
      <textarea
        id={`args-${tool.name}`}
        value={args}
        onChange={(e) => setArgs(e.target.value)}
        rows={3}
        spellCheck={false}
        className={`${INPUT} font-mono`}
      />
      <div>
        <button type="button" className={PRIMARY} disabled={running} onClick={() => void runTool()}>
          {running ? "Running…" : `Run ${tool.name}`}
        </button>
      </div>
      {error && (
        <p role="alert" className={ALERT}>
          {error}
        </p>
      )}
      {outcome && (
        <div aria-label="Tool result">
          <p className={`text-xs ${outcome.is_error ? "text-danger" : "text-muted"}`}>
            {outcome.is_error ? "The tool reported an error" : "Result"} · {outcome.duration_ms} ms
          </p>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-background p-2 text-xs">
            {outcome.content}
          </pre>
        </div>
      )}
    </div>
  );
}

/** `{"timezone": ""}`-style starting arguments from the tool's input schema. */
function exampleArguments(tool: McpTool): string {
  const properties = tool.input_schema.properties ?? {};
  const example = Object.fromEntries(
    Object.entries(properties).map(([name, spec]) => [
      name,
      spec.type === "number" || spec.type === "integer" ? 0 : spec.type === "boolean" ? false : "",
    ]),
  );
  return JSON.stringify(example, null, 2);
}

"use client";

import { type FormEvent, useState } from "react";

import { ALERT, BUTTON, INPUT, PRIMARY } from "@/components/knowledge/styles";
import { createMcpServer, listMcpPresets, type Transport } from "@/lib/api";

import { parsePairs, toolsError } from "./messages";

const LABEL = "mb-1 block text-xs font-medium text-muted";

export default function ServerForm({
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
  const [transport, setTransport] = useState<Transport>("stdio");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [env, setEnv] = useState("");
  const [url, setUrl] = useState("");
  const [headers, setHeaders] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** Fill the form with the built-in demo server, as this backend says to run it. */
  const fillDemo = async () => {
    setError(null);
    try {
      const demo = (await listMcpPresets(adminKey)).data.find((p) => p.name === "demo");
      if (!demo) return;
      setName(demo.name);
      setDescription(demo.description);
      setTransport("stdio");
      setCommand(demo.command ?? "");
      setArgs((demo.args ?? []).join("\n"));
    } catch (err) {
      setError(toolsError(err));
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await createMcpServer(
        transport === "stdio"
          ? {
              name,
              description,
              transport,
              command,
              args: args
                .split("\n")
                .map((a) => a.trim())
                .filter(Boolean),
              env: parsePairs(env, "="),
            }
          : {
              name,
              description,
              transport,
              url,
              headers: parsePairs(headers, ":"),
            },
        adminKey,
      );
      onCreated(created.name);
    } catch (err) {
      setError(toolsError(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex max-w-xl flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">Add MCP server</h2>
        <button type="button" onClick={() => void fillDemo()} className={BUTTON}>
          Use demo server
        </button>
      </div>
      <label className="text-sm">
        <span className={LABEL}>Name (lowercase words with hyphens)</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          pattern="[a-z0-9]+(-[a-z0-9]+)*"
          maxLength={40}
          placeholder="orders"
          className={`${INPUT} font-mono`}
        />
      </label>
      <label className="text-sm">
        <span className={LABEL}>Description</span>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What its tools do"
          className={INPUT}
        />
      </label>

      <fieldset className="text-sm">
        <legend className={LABEL}>How to reach it</legend>
        <div className="flex flex-wrap gap-4">
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="transport"
              checked={transport === "stdio"}
              onChange={() => setTransport("stdio")}
            />
            Local command (stdio)
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="transport"
              checked={transport === "http"}
              onChange={() => setTransport("http")}
            />
            Remote URL (HTTP)
          </label>
        </div>
      </fieldset>

      {transport === "stdio" ? (
        <>
          <label className="text-sm">
            <span className={LABEL}>Command (run on the backend server)</span>
            <input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              required
              placeholder="npx"
              className={`${INPUT} font-mono`}
            />
          </label>
          <label className="text-sm">
            <span className={LABEL}>Arguments (one per line)</span>
            <textarea
              value={args}
              onChange={(e) => setArgs(e.target.value)}
              rows={3}
              placeholder={"-y\n@modelcontextprotocol/server-everything"}
              className={`${INPUT} font-mono`}
            />
          </label>
          <label className="text-sm">
            <span className={LABEL}>
              Environment variables (KEY=value per line; stored encrypted, never shown again)
            </span>
            <textarea
              value={env}
              onChange={(e) => setEnv(e.target.value)}
              rows={2}
              placeholder="API_TOKEN=..."
              autoComplete="off"
              spellCheck={false}
              className={`${INPUT} font-mono`}
            />
          </label>
        </>
      ) : (
        <>
          <label className="text-sm">
            <span className={LABEL}>URL (streamable HTTP endpoint)</span>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              type="url"
              placeholder="https://mcp.example.com/mcp"
              className={`${INPUT} font-mono`}
            />
          </label>
          <label className="text-sm">
            <span className={LABEL}>Headers (Name: value per line; stored encrypted, never shown again)</span>
            <textarea
              value={headers}
              onChange={(e) => setHeaders(e.target.value)}
              rows={2}
              placeholder="Authorization: Bearer ..."
              autoComplete="off"
              spellCheck={false}
              className={`${INPUT} font-mono`}
            />
          </label>
        </>
      )}

      {error && (
        <p role="alert" className={ALERT}>
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={saving} className={PRIMARY}>
          {saving ? "Connecting…" : "Add and connect"}
        </button>
        <button type="button" onClick={onCancel} className={BUTTON}>
          Cancel
        </button>
      </div>
    </form>
  );
}

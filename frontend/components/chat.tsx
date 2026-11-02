"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  type FinishReason,
  getModels,
  listPrompts,
  type Message,
  type ModelInfo,
  type PromptSummary,
  type PromptUsed,
  streamChat,
  type Usage,
  type VariableValues,
} from "@/lib/api";

interface Turn {
  key: string;
  role: Message["role"];
  content: string;
  streaming?: boolean;
  stopped?: boolean;
  error?: string;
  meta?: {
    provider?: string;
    model?: string;
    usage?: Usage;
    latencyMs?: number;
    finishReason?: FinishReason;
    prompt?: PromptUsed;
  };
}

const FINISH_NOTES: Partial<Record<FinishReason, string>> = {
  length: "Stopped early: reached the max token limit.",
  refusal: "The model declined this request.",
  content_filter: "Blocked by the provider's safety filter.",
};

let keyCounter = 0;
const nextKey = () => `t${++keyCounter}`;

export default function Chat() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [system, setSystem] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [prompts, setPrompts] = useState<PromptSummary[]>([]);
  const [promptsAvailable, setPromptsAvailable] = useState(true);
  const [promptName, setPromptName] = useState("");
  const [promptVars, setPromptVars] = useState<VariableValues>({});
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    getModels(controller.signal)
      .then((list) => {
        setModels(list.data);
        // This also re-runs when you come back to Chat from another page: keep your choice if it still exists.
        setModel((current) =>
          list.data.some((m) => m.id === current) ? current : (list.default ?? list.data[0]?.id ?? ""),
        );
      })
      .catch((err: unknown) => {
        if ((err as Error).name === "AbortError") return;
        setLoadError(
          err instanceof ApiError ? err.message : "Can't reach the backend. Is it running on port 8000?",
        );
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listPrompts({ signal: controller.signal })
      .then((list) => {
        setPrompts(list.data);
        setPromptsAvailable(true);
        setPromptName((current) => (list.data.some((p) => p.name === current) ? current : ""));
      })
      .catch((err: unknown) => {
        if ((err as Error).name === "AbortError") return;
        setPromptsAvailable(false); // no database configured: chat works without managed prompts
      });
    return () => controller.abort();
  }, []);

  const selectedPrompt = prompts.find((p) => p.name === promptName);
  const missingVars = (selectedPrompt?.active_variables ?? []).filter((v) => !promptVars[v]?.trim());

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const updateTurn = useCallback((key: string, update: (turn: Turn) => Turn) => {
    setTurns((prev) => prev.map((t) => (t.key === key ? update(t) : t)));
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || busy || !model || missingVars.length > 0) return;

    const history: Message[] = turns
      .filter((t) => !t.error && t.content)
      .map(({ role, content }) => ({ role, content }));
    history.push({ role: "user", content: text });

    const assistantKey = nextKey();
    setTurns((prev) => [
      ...prev,
      { key: nextKey(), role: "user", content: text },
      { key: assistantKey, role: "assistant", content: "", streaming: true },
    ]);
    setInput("");
    setBusy(true);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChat(
        {
          model,
          messages: history,
          system: system.trim() || undefined,
          prompt: selectedPrompt
            ? {
                name: selectedPrompt.name,
                variables: Object.fromEntries(selectedPrompt.active_variables.map((v) => [v, promptVars[v]])),
              }
            : undefined,
        },
        (event) => {
          switch (event.type) {
            case "start":
              updateTurn(assistantKey, (t) => ({
                ...t,
                meta: { provider: event.provider, model: event.model, prompt: event.prompt },
              }));
              break;
            case "delta":
              updateTurn(assistantKey, (t) => ({ ...t, content: t.content + event.delta }));
              break;
            case "done":
              updateTurn(assistantKey, (t) => ({
                ...t,
                streaming: false,
                meta: {
                  provider: event.provider,
                  model: event.model,
                  usage: event.usage,
                  latencyMs: event.latency_ms,
                  finishReason: event.finish_reason,
                  prompt: event.prompt,
                },
              }));
              break;
            case "error":
              updateTurn(assistantKey, (t) => ({ ...t, streaming: false, error: event.error.message }));
              break;
          }
        },
        controller.signal,
      );
    } catch (err) {
      const stopped = (err as Error).name === "AbortError";
      updateTurn(assistantKey, (t) => ({
        ...t,
        streaming: false,
        stopped,
        error: stopped ? undefined : err instanceof ApiError ? err.message : "Network error. Please retry.",
      }));
    } finally {
      updateTurn(assistantKey, (t) => ({ ...t, streaming: false }));
      abortRef.current = null;
      setBusy(false);
    }
  }, [busy, input, missingVars.length, model, promptVars, selectedPrompt, system, turns, updateTurn]);

  const stop = () => abortRef.current?.abort();
  const newChat = () => {
    stop();
    setTurns([]);
  };

  const providers = Array.from(new Set(models.map((m) => m.provider)));

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col px-4">
      {/* Header */}
      <header className="flex flex-wrap items-center gap-3 border-b border-line py-3">
        <h1 className="mr-auto text-base font-semibold tracking-tight">Chat</h1>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted">Prompt</span>
          <select
            aria-label="Prompt"
            value={promptName}
            onChange={(e) => setPromptName(e.target.value)}
            disabled={busy || !promptsAvailable}
            title={promptsAvailable ? undefined : "Prompt store not configured (DATABASE_URL)"}
            className="max-w-[12rem] rounded-md border border-line bg-surface px-2 py-1.5 text-sm"
          >
            <option value="">{promptsAvailable ? "None" : "Unavailable"}</option>
            {prompts.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name} (v{p.active_version})
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted">Model</span>
          <select
            aria-label="Model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={busy || models.length === 0}
            className="max-w-[16rem] rounded-md border border-line bg-surface px-2 py-1.5 text-sm"
          >
            {providers.map((provider) => (
              <optgroup key={provider} label={provider}>
                {models
                  .filter((m) => m.provider === provider)
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.model}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => setShowSettings((s) => !s)}
          aria-expanded={showSettings}
          className="rounded-md border border-line px-2.5 py-1.5 text-sm hover:bg-surface"
        >
          {selectedPrompt ? "Extra instructions" : "System prompt"}
        </button>
        <button
          type="button"
          onClick={newChat}
          className="rounded-md border border-line px-2.5 py-1.5 text-sm hover:bg-surface"
        >
          New chat
        </button>
      </header>

      {selectedPrompt && selectedPrompt.active_variables.length > 0 && (
        <fieldset className="flex flex-wrap items-center gap-3 border-b border-line py-3">
          <legend className="sr-only">Prompt variables</legend>
          {selectedPrompt.active_variables.map((name) => (
            <label key={name} className="flex items-center gap-2 text-sm">
              <span className="font-mono text-xs text-muted">{name}</span>
              <input
                value={promptVars[name] ?? ""}
                onChange={(e) => setPromptVars((prev) => ({ ...prev, [name]: e.target.value }))}
                aria-label={`Prompt variable ${name}`}
                className="w-44 rounded-md border border-line bg-surface px-2 py-1 text-sm"
              />
            </label>
          ))}
        </fieldset>
      )}

      {showSettings && (
        <div className="border-b border-line py-3">
          <label htmlFor="system" className="mb-1 block text-xs font-medium text-muted">
            {selectedPrompt
              ? `Extra instructions (added after the "${selectedPrompt.name}" prompt)`
              : "System prompt (sent with every message)"}
          </label>
          <textarea
            id="system"
            value={system}
            onChange={(e) => setSystem(e.target.value)}
            rows={3}
            placeholder="You are a helpful assistant for Acme Corp…"
            className="w-full resize-y rounded-md border border-line bg-surface p-2 text-sm"
          />
        </div>
      )}

      {loadError && (
        <div role="alert" className="mt-3 rounded-md border border-danger/40 bg-danger/10 p-3 text-sm">
          {loadError}
        </div>
      )}

      {/* Messages */}
      <main className="flex-1 overflow-y-auto py-4" aria-live="polite">
        {turns.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted">
            <p className="text-base text-foreground">Send a message to start.</p>
            <p className="mt-1">
              {models.length > 0
                ? `${models.length} model${models.length === 1 ? "" : "s"} available from ${providers.join(", ")}.`
                : loadError
                  ? "No models loaded."
                  : "Loading models…"}
            </p>
            {providers.length === 1 && providers[0] === "mock" && (
              <p className="mt-1">Add provider API keys to the backend .env to enable real models.</p>
            )}
          </div>
        ) : (
          <ol className="flex flex-col gap-4">
            {turns.map((turn) => (
              <li
                key={turn.key}
                data-role={turn.role}
                className={turn.role === "user" ? "flex justify-end" : "flex justify-start"}
              >
                <div
                  className={
                    turn.role === "user"
                      ? "max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-4 py-2.5 text-sm text-accent-fg"
                      : "max-w-[85%] text-sm"
                  }
                >
                  {turn.content && <p className="whitespace-pre-wrap leading-relaxed">{turn.content}</p>}
                  {turn.streaming && !turn.content && <p className="animate-pulse text-muted">Thinking…</p>}
                  {turn.streaming && turn.content && (
                    <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-muted align-middle" />
                  )}
                  {turn.error && (
                    <p role="alert" className="mt-1 rounded-md border border-danger/40 bg-danger/10 px-3 py-2">
                      {turn.error}
                    </p>
                  )}
                  {turn.stopped && <p className="mt-1 text-xs text-muted">Stopped.</p>}
                  {turn.meta?.finishReason && FINISH_NOTES[turn.meta.finishReason] && (
                    <p className="mt-1 text-xs text-warning">{FINISH_NOTES[turn.meta.finishReason]}</p>
                  )}
                  {turn.role === "assistant" && !turn.streaming && turn.meta?.model && (
                    <p className="mt-1.5 font-mono text-[11px] text-muted">
                      {turn.meta.provider}/{turn.meta.model}
                      {turn.meta.prompt && ` · prompt ${turn.meta.prompt.name} v${turn.meta.prompt.version}`}
                      {turn.meta.usage &&
                        ` · ${turn.meta.usage.input_tokens} in / ${turn.meta.usage.output_tokens} out`}
                      {turn.meta.latencyMs !== undefined && ` · ${(turn.meta.latencyMs / 1000).toFixed(2)}s`}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
        <div ref={bottomRef} />
      </main>

      {/* Composer */}
      <form
        className="border-t border-line py-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <div className="flex items-end gap-2 rounded-xl border border-line bg-surface p-2 focus-within:border-accent">
          <textarea
            aria-label="Message"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send();
              }
            }}
            rows={1}
            placeholder={model ? `Message ${model}` : "Waiting for models…"}
            className="max-h-48 min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none"
          />
          {busy ? (
            <button
              type="button"
              onClick={stop}
              className="rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-background"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim() || !model || missingVars.length > 0}
              className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:opacity-40"
            >
              Send
            </button>
          )}
        </div>
        <p className="mt-1.5 text-center text-[11px] text-muted">
          {missingVars.length > 0
            ? `Fill in the prompt variables first: ${missingVars.join(", ")}`
            : "Enter to send · Shift+Enter for a new line"}
        </p>
      </form>
    </div>
  );
}

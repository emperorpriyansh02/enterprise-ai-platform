// Typed client for the backend API: models + chat streaming (Layer 1), prompts (Layer 2).

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Role = "user" | "assistant";
export type FinishReason = "stop" | "length" | "refusal" | "content_filter" | "other";

export interface Message {
  role: Role;
  content: string;
}

export interface ChatRequest {
  model?: string;
  messages: Message[];
  prompt?: PromptRef;
  system?: string;
  temperature?: number;
  max_tokens?: number;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  provider?: string;
}

export type StreamEvent =
  | { type: "start"; id: string; provider: string; model: string; prompt?: PromptUsed }
  | { type: "delta"; id: string; delta: string }
  | {
      type: "done";
      id: string;
      provider: string;
      model: string;
      finish_reason: FinishReason;
      usage?: Usage;
      latency_ms: number;
      prompt?: PromptUsed;
    }
  | { type: "error"; id: string; error: ApiErrorBody };

export interface ModelInfo {
  id: string;
  provider: string;
  model: string;
}

export interface ModelList {
  default: string | null;
  data: ModelInfo[];
}

// --- Prompts (Layer 2) ---

export type VariableValues = Record<string, string>;

export interface PromptRef {
  name: string;
  version?: number;
  variables?: VariableValues;
}

export interface PromptUsed {
  name: string;
  version: number;
}

export interface PromptVersion {
  version: number;
  template: string;
  variables: string[];
  note: string;
  created_by: string | null;
  created_at: string;
  is_active: boolean;
}

export interface PromptSummary {
  name: string;
  description: string;
  active_version: number | null;
  latest_version: number;
  active_variables: string[];
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface PromptDetail extends PromptSummary {
  versions: PromptVersion[]; // newest first
}

export interface RenderResult {
  name: string;
  version: number;
  text: string;
  variables: string[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = await response.json();
    if (body?.error?.message) {
      return new ApiError(body.error.message, response.status, body.error.code);
    }
    if (Array.isArray(body?.detail)) {
      // FastAPI validation error
      const msg = body.detail.map((d: { msg: string }) => d.msg).join("; ");
      return new ApiError(msg, response.status, "validation_error");
    }
  } catch {
    // fall through to a generic message
  }
  return new ApiError(`Request failed with status ${response.status}`, response.status);
}

/** Send a JSON request and parse the JSON reply. Throws ApiError on HTTP errors. */
async function apiRequest<T>(
  path: string,
  options: { method?: string; body?: unknown; adminKey?: string; signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.adminKey) headers["X-Admin-Key"] = options.adminKey;
  const response = await fetch(`${API_URL}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });
  if (!response.ok) throw await toApiError(response);
  return response.json() as Promise<T>;
}

export function getModels(signal?: AbortSignal): Promise<ModelList> {
  return apiRequest<ModelList>("/v1/models", { signal });
}

export function listPrompts(
  options: { includeArchived?: boolean; signal?: AbortSignal } = {},
): Promise<{ data: PromptSummary[] }> {
  const query = options.includeArchived ? "?include_archived=true" : "";
  return apiRequest(`/v1/prompts${query}`, { signal: options.signal });
}

export function getPrompt(name: string): Promise<PromptDetail> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}`);
}

export function createPrompt(
  body: { name: string; description: string; template: string; note: string },
  adminKey?: string,
): Promise<PromptDetail> {
  return apiRequest("/v1/prompts", { method: "POST", body, adminKey });
}

export function updatePromptDescription(
  name: string,
  description: string,
  adminKey?: string,
): Promise<PromptDetail> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: { description },
    adminKey,
  });
}

export function addPromptVersion(
  name: string,
  body: { template: string; note: string; activate: boolean },
  adminKey?: string,
): Promise<PromptVersion> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}/versions`, {
    method: "POST",
    body,
    adminKey,
  });
}

export function activatePromptVersion(
  name: string,
  version: number,
  adminKey?: string,
): Promise<PromptDetail> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}/activate`, {
    method: "POST",
    body: { version },
    adminKey,
  });
}

export function archivePrompt(name: string, adminKey?: string): Promise<PromptDetail> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}`, { method: "DELETE", adminKey });
}

export function renderPrompt(
  name: string,
  body: { version?: number; variables: VariableValues },
): Promise<RenderResult> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}/render`, { method: "POST", body });
}

/**
 * POST /v1/chat/stream and call `onEvent` for each server-sent event.
 * Resolves when the stream ends; rejects on HTTP errors (before any event) or network failure.
 */
export async function streamChat(
  request: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) throw await toApiError(response);
  if (!response.body) throw new ApiError("Response has no body", response.status);

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    // Events are separated by a blank line; the server may use \r\n line endings.
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const event = parseEvent(part);
      if (event) onEvent(event);
    }
  }
  const tail = parseEvent(buffer);
  if (tail) onEvent(tail);
}

function parseEvent(block: string): StreamEvent | null {
  const data = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n");
  if (!data) return null; // keep-alive comments (": ping") carry no data
  return JSON.parse(data) as StreamEvent;
}

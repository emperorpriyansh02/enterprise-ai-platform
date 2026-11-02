// Typed client for the backend API (Layer 1: models + chat streaming).

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
  | { type: "start"; id: string; provider: string; model: string }
  | { type: "delta"; id: string; delta: string }
  | {
      type: "done";
      id: string;
      provider: string;
      model: string;
      finish_reason: FinishReason;
      usage?: Usage;
      latency_ms: number;
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

export async function getModels(signal?: AbortSignal): Promise<ModelList> {
  const response = await fetch(`${API_URL}/v1/models`, { signal });
  if (!response.ok) throw await toApiError(response);
  return response.json();
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

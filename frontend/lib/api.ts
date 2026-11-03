// Typed client for the backend API: models + chat streaming (Layer 1), prompts (Layer 2),
// knowledge bases (Layer 3), MCP tools (Layer 4), the signed-in user (Layer 5).
//
// Every request carries `Authorization: Bearer <token>` when someone is signed in: the sign-in code
// (components/auth-provider.tsx) registers how to get the token with `setAccessTokenProvider`.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Role = "user" | "assistant";
export type FinishReason = "stop" | "length" | "refusal" | "content_filter" | "tool_calls" | "other";

export interface Message {
  role: Role;
  content: string;
}

export interface ChatRequest {
  model?: string;
  messages: Message[];
  prompt?: PromptRef;
  rag?: RagRef;
  tools?: ToolsRef;
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
  | {
      type: "start";
      id: string;
      provider: string;
      model: string;
      prompt?: PromptUsed;
      citations?: Citation[];
    }
  | { type: "delta"; id: string; delta: string }
  | { type: "tool_call"; id: string; tool: ToolCallTrace } // about to run (no result yet)
  | { type: "tool_result"; id: string; tool: ToolCallTrace }
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

// --- Knowledge bases (Layer 3) ---

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface RagRef {
  collections: string[];
  top_k?: number;
}

export interface Citation {
  index: number;
  collection: string;
  document_id: string;
  document_title: string;
  chunk_id: string;
  position: number;
  snippet: string;
  score: number;
}

export interface DocumentCounts {
  pending: number;
  processing: number;
  ready: number;
  failed: number;
}

export interface CollectionSummary {
  name: string;
  description: string;
  embedding_model: string;
  documents: DocumentCounts;
  archived: boolean;
  allowed_roles: RoleName[]; // empty = everyone
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface CollectionDetail extends CollectionSummary {
  server_embedding_model: string | null;
  needs_reindex: boolean;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  filename: string | null;
  mime_type: string;
  size_bytes: number;
  status: DocumentStatus;
  error: string | null;
  chunk_count: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface UploadResult {
  data: KnowledgeDocument[];
  errors: { filename: string; code: string; message: string }[];
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  document_title: string;
  collection: string;
  position: number;
  content: string;
  score: number;
  vector_similarity: number | null;
  keyword_score: number | null;
}

// --- MCP tools (Layer 4) ---

export type Transport = "stdio" | "http";

export interface ToolsRef {
  servers: string[];
}

export interface ToolCallTrace {
  id: string;
  server: string;
  tool: string;
  arguments: Record<string, unknown>;
  result?: string; // first 500 characters
  is_error?: boolean;
  duration_ms?: number;
}

export interface McpTool {
  name: string;
  description: string;
  input_schema: {
    properties?: Record<string, { type?: string; description?: string }>;
    required?: string[];
  };
  read_only: boolean;
  destructive: boolean;
  enabled: boolean;
}

export interface McpServer {
  name: string;
  description: string;
  transport: Transport;
  command: string | null;
  args: string[];
  env_names: string[];
  url: string | null;
  header_names: string[];
  enabled: boolean;
  allowed_roles: RoleName[]; // empty = everyone
  created_by: string | null;
  last_error: string | null;
  last_connected_at: string | null;
  tools: McpTool[];
  created_at: string;
  updated_at: string;
}

export interface McpServerCreate {
  name: string;
  description: string;
  transport: Transport;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
  allowed_roles?: RoleName[];
}

export interface AvailableServer {
  name: string;
  description: string;
  tools: { name: string; description: string; destructive: boolean }[];
}

export interface ToolCallOutcome {
  content: string;
  is_error: boolean;
  duration_ms: number;
}

// --- Users and roles (Layer 5) ---

export const ROLES = ["eap-admin", "eap-editor", "eap-user"] as const;
export type RoleName = (typeof ROLES)[number];

export const ROLE_LABELS: Record<RoleName, string> = {
  "eap-admin": "Admin",
  "eap-editor": "Editor",
  "eap-user": "User",
};

export interface Me {
  subject: string;
  username: string;
  name: string | null;
  email: string | null;
  roles: string[];
  permissions: string[]; // e.g. "prompts:write"
  auth_enabled: boolean;
  is_dev: boolean;
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

let accessTokenProvider: (() => Promise<string | null>) | null = null;

/** Called once by the sign-in code: how to get the current access token (null = none). */
export function setAccessTokenProvider(provider: (() => Promise<string | null>) | null): void {
  accessTokenProvider = provider;
}

/** `{Authorization: "Bearer …"}` when someone is signed in, else `{}`. */
async function authHeaders(): Promise<Record<string, string>> {
  const token = accessTokenProvider ? await accessTokenProvider() : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
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

/**
 * Send a request and parse the JSON reply. `body` is sent as JSON; `form` as multipart (file uploads,
 * where the browser sets the content type). Throws ApiError on HTTP errors. 204 replies give undefined.
 */
async function apiRequest<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    form?: FormData;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = await authHeaders();
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API_URL}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.form ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
    signal: options.signal,
  });
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function getModels(signal?: AbortSignal): Promise<ModelList> {
  return apiRequest<ModelList>("/v1/models", { signal });
}

/** Who is signed in (or the dev user), their roles and permissions. */
export function getMe(): Promise<Me> {
  return apiRequest<Me>("/v1/me");
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

export function createPrompt(body: {
  name: string;
  description: string;
  template: string;
  note: string;
}): Promise<PromptDetail> {
  return apiRequest("/v1/prompts", { method: "POST", body });
}

export function updatePromptDescription(name: string, description: string): Promise<PromptDetail> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: { description },
  });
}

export function addPromptVersion(
  name: string,
  body: { template: string; note: string; activate: boolean },
): Promise<PromptVersion> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}/versions`, {
    method: "POST",
    body,
  });
}

export function activatePromptVersion(name: string, version: number): Promise<PromptDetail> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}/activate`, {
    method: "POST",
    body: { version },
  });
}

export function archivePrompt(name: string): Promise<PromptDetail> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

export function renderPrompt(
  name: string,
  body: { version?: number; variables: VariableValues },
): Promise<RenderResult> {
  return apiRequest(`/v1/prompts/${encodeURIComponent(name)}/render`, {
    method: "POST",
    body,
  });
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
    headers: {
      ...(await authHeaders()),
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
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

const collectionPath = (name: string) => `/v1/collections/${encodeURIComponent(name)}`;

export function listCollections(
  options: { includeArchived?: boolean; signal?: AbortSignal } = {},
): Promise<{ data: CollectionSummary[] }> {
  const query = options.includeArchived ? "?include_archived=true" : "";
  return apiRequest(`/v1/collections${query}`, { signal: options.signal });
}

export function getCollection(name: string): Promise<CollectionDetail> {
  return apiRequest(collectionPath(name));
}

export function createCollection(body: {
  name: string;
  description: string;
  allowed_roles?: RoleName[];
}): Promise<CollectionDetail> {
  return apiRequest("/v1/collections", { method: "POST", body });
}

export function updateCollection(
  name: string,
  body: { description?: string; allowed_roles?: RoleName[] },
): Promise<CollectionDetail> {
  return apiRequest(collectionPath(name), { method: "PATCH", body });
}

export function archiveCollection(name: string): Promise<CollectionDetail> {
  return apiRequest(collectionPath(name), { method: "DELETE" });
}

export function reindexCollection(name: string): Promise<CollectionDetail> {
  return apiRequest(`${collectionPath(name)}/reindex`, {
    method: "POST",
  });
}

export function listDocuments(name: string): Promise<{ data: KnowledgeDocument[] }> {
  return apiRequest(`${collectionPath(name)}/documents`);
}

export function uploadDocuments(name: string, files: File[]): Promise<UploadResult> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  return apiRequest(`${collectionPath(name)}/documents`, {
    method: "POST",
    form,
  });
}

export function addTextDocument(
  name: string,
  body: { title: string; text: string },
): Promise<KnowledgeDocument> {
  return apiRequest(`${collectionPath(name)}/documents/text`, {
    method: "POST",
    body,
  });
}

export function deleteDocument(name: string, documentId: string): Promise<void> {
  return apiRequest(`${collectionPath(name)}/documents/${documentId}`, {
    method: "DELETE",
  });
}

export function reindexDocument(name: string, documentId: string): Promise<KnowledgeDocument> {
  return apiRequest(`${collectionPath(name)}/documents/${documentId}/reindex`, {
    method: "POST",
  });
}

export function searchCollection(
  name: string,
  body: { query: string; top_k?: number },
): Promise<{
  query: string;
  collections: string[];
  embedding_model: string;
  hits: SearchHit[];
}> {
  return apiRequest(`${collectionPath(name)}/search`, { method: "POST", body });
}

const serverPath = (name: string) => `/v1/tools/servers/${encodeURIComponent(name)}`;

export function listAvailableTools(signal?: AbortSignal): Promise<{ data: AvailableServer[] }> {
  return apiRequest("/v1/tools", { signal });
}

/** Ready-made server definitions (the built-in demo server, with the backend's own Python). */
export function listMcpPresets(): Promise<{ data: McpServerCreate[] }> {
  return apiRequest("/v1/tools/presets", {});
}

export function listMcpServers(): Promise<{ data: McpServer[] }> {
  return apiRequest("/v1/tools/servers", {});
}

export function getMcpServer(name: string): Promise<McpServer> {
  return apiRequest(serverPath(name), {});
}

export function createMcpServer(body: McpServerCreate): Promise<McpServer> {
  return apiRequest("/v1/tools/servers", { method: "POST", body });
}

export function updateMcpServer(
  name: string,
  body: { description?: string; enabled?: boolean; allowed_roles?: RoleName[] },
): Promise<McpServer> {
  return apiRequest(serverPath(name), { method: "PATCH", body });
}

export function deleteMcpServer(name: string): Promise<void> {
  return apiRequest(serverPath(name), { method: "DELETE" });
}

export function refreshMcpServer(name: string): Promise<McpServer> {
  return apiRequest(`${serverPath(name)}/refresh`, {
    method: "POST",
  });
}

export function setMcpToolEnabled(server: string, tool: string, enabled: boolean): Promise<McpTool> {
  return apiRequest(`${serverPath(server)}/tools/${encodeURIComponent(tool)}`, {
    method: "PATCH",
    body: { enabled },
  });
}

export function callMcpTool(
  server: string,
  tool: string,
  args: Record<string, unknown>,
): Promise<ToolCallOutcome> {
  return apiRequest(`${serverPath(server)}/tools/${encodeURIComponent(tool)}/call`, {
    method: "POST",
    body: { arguments: args },
  });
}

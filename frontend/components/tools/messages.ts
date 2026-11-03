import { errorMessage } from "@/components/prompts/messages";
import { ApiError } from "@/lib/api";

/** A short, actionable message for any error from the tools API. */
export function toolsError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "forbidden") {
      return "Managing MCP servers needs the Admin role.";
    }
    if (err.code === "tools_unavailable") {
      return "MCP tools need a database. Set DATABASE_URL in backend/.env and restart the backend.";
    }
  }
  return errorMessage(err);
}

/** Lines of `KEY=value` (env) or `Name: value` (headers) → an object. Blank lines are skipped. */
export function parsePairs(text: string, separator: "=" | ":"): Record<string, string> {
  const pairs: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const at = line.indexOf(separator);
    if (at <= 0) continue;
    const key = line.slice(0, at).trim();
    if (key) pairs[key] = line.slice(at + 1).trim();
  }
  return pairs;
}

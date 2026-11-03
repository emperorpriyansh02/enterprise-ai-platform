import { ApiError } from "@/lib/api";

/** A short, actionable message for any error from the prompts API. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "prompt_store_unavailable") {
      return "The prompt store isn't configured. Set DATABASE_URL in backend/.env and restart the backend.";
    }
    if (err.code === "forbidden") {
      return `Your role can't do this. ${err.message}`;
    }
    if (err.code === "auth_required" || err.code === "invalid_token") {
      return "Your sign-in has expired. Reload the page to sign in again.";
    }
    return err.message;
  }
  return "Can't reach the backend. Is it running on port 8000?";
}

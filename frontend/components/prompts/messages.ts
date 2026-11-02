import { ApiError } from "@/lib/api";

/** A short, actionable message for any error from the prompts API. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "prompt_store_unavailable") {
      return "The prompt store isn't configured. Set DATABASE_URL in backend/.env and restart the backend.";
    }
    if (err.code === "admin_auth_required") {
      return "This change needs the admin key. Enter it at the top of the page.";
    }
    return err.message;
  }
  return "Can't reach the backend. Is it running on port 8000?";
}

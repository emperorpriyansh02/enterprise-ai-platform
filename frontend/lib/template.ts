// Same rule as the backend (backend/app/prompts/template.py): {{ name }} placeholders.
const VARIABLE = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;

/** Variable names in the order they first appear, without duplicates. */
export function extractVariables(template: string): string[] {
  return [...new Set(Array.from(template.matchAll(VARIABLE), (match) => match[1]))];
}

import type { Metadata } from "next";

import PromptsPage from "@/components/prompts/prompts-page";

export const metadata: Metadata = { title: "Prompts · Enterprise AI Platform" };

export default function Prompts() {
  return <PromptsPage />;
}

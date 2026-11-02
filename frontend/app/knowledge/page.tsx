import type { Metadata } from "next";

import KnowledgePage from "@/components/knowledge/knowledge-page";

export const metadata: Metadata = { title: "Knowledge · Enterprise AI Platform" };

export default function Knowledge() {
  return <KnowledgePage />;
}

import type { Metadata } from "next";

import ToolsPage from "@/components/tools/tools-page";

export const metadata: Metadata = { title: "Tools · Enterprise AI Platform" };

export default function Tools() {
  return <ToolsPage />;
}

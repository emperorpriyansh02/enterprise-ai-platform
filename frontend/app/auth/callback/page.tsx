import type { Metadata } from "next";

import AuthCallback from "@/components/auth-callback";

export const metadata: Metadata = { title: "Signing in · Enterprise AI Platform" };

export default function AuthCallbackPage() {
  return <AuthCallback />;
}

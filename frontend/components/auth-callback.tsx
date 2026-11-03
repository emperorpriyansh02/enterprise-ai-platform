"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { completeSignIn } from "@/lib/auth";

/** Where the sign-in page sends the browser back: swaps the one-time code for tokens, then moves on. */
export default function AuthCallback() {
  const router = useRouter();
  const started = useRef(false); // the code can be used only once, even if this renders twice
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    completeSignIn()
      .then((returnTo) => router.replace(returnTo))
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Sign-in failed."));
  }, [router]);

  if (error) {
    return (
      <div
        role="alert"
        className="m-auto max-w-md rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
      >
        <p className="font-medium">Sign-in didn&apos;t complete</p>
        <p className="mt-1">{error}</p>
        <Link href="/" className="mt-2 inline-block underline">
          Start again
        </Link>
      </div>
    );
  }
  return <p className="m-auto text-sm text-muted">Signing you in…</p>;
}

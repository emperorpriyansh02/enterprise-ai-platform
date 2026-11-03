"use client";

import { usePathname } from "next/navigation";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { ApiError, getMe, type Me, setAccessTokenProvider } from "@/lib/api";
import { currentAccessToken, getAuthConfig, getUserManager, signIn, signOut } from "@/lib/auth";

type Status = "loading" | "signed-out" | "ready" | "error";

interface AuthState {
  status: Status;
  me: Me | null;
  error: string | null;
  /** True if the user's roles give `permission` (e.g. "prompts:write"). */
  can: (permission: string) => boolean;
  signIn: () => void;
  signOut: () => void;
}

const AuthContext = createContext<AuthState>({
  status: "loading",
  me: null,
  error: null,
  can: () => false,
  signIn: () => {},
  signOut: () => {},
});

/** The signed-in user and what they may do. Use inside `AuthProvider`. */
export function useAuth(): AuthState {
  return useContext(AuthContext);
}

/**
 * Works out who is using the app, once per page load (and again after signing in):
 * sign-in off → the backend's dev user; on → the user from the stored token, or "signed-out".
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const onCallback = pathname.startsWith("/auth/");
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (onCallback) return; // /auth/callback finishes the sign-in first, then comes back here
    let cancelled = false;
    const load = async () => {
      try {
        const config = await getAuthConfig();
        setAccessTokenProvider(config.enabled ? currentAccessToken : null);
        if (config.enabled && !(await currentAccessToken())) {
          if (!cancelled) setStatus("signed-out");
          return;
        }
        const who = await getMe();
        if (cancelled) return;
        setMe(who);
        setError(null);
        setStatus("ready");
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          setStatus("signed-out"); // the stored token was rejected (expired, revoked...)
          return;
        }
        setError(
          err instanceof ApiError ? err.message : "Can't reach the backend. Is it running on port 8000?",
        );
        setStatus("error");
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [onCallback]);

  // If the token can't be renewed (session ended at the provider), ask to sign in again.
  useEffect(() => {
    let unsubscribe = () => {};
    void getUserManager()
      .then((manager) => {
        if (!manager) return;
        const signedOut = () => setStatus("signed-out");
        manager.events.addSilentRenewError(signedOut);
        manager.events.addUserSignedOut(signedOut);
        unsubscribe = () => {
          manager.events.removeSilentRenewError(signedOut);
          manager.events.removeUserSignedOut(signedOut);
        };
      })
      .catch(() => {}); // no config yet: the load effect shows the error
    return () => unsubscribe();
  }, []);

  const can = useCallback((permission: string) => me?.permissions.includes(permission) ?? false, [me]);
  const value = useMemo<AuthState>(
    () => ({
      status,
      me,
      error,
      can,
      signIn: () => void signIn(window.location.pathname),
      signOut: () => void signOut(),
    }),
    [status, me, error, can],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Shows the page only once the user is known; otherwise a sign-in screen, or the error. */
export function AuthGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { status, error, signIn: startSignIn } = useAuth();
  if (pathname.startsWith("/auth/") || status === "ready") return <>{children}</>;
  if (status === "loading") {
    return <p className="m-auto text-sm text-muted">Loading…</p>;
  }
  if (status === "error") {
    return (
      <div
        role="alert"
        className="m-auto max-w-md rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
      >
        {error}
      </div>
    );
  }
  return (
    <div className="m-auto flex max-w-sm flex-col items-center gap-3 px-4 text-center">
      <h1 className="text-lg font-semibold">Sign in to continue</h1>
      <p className="text-sm text-muted">
        You&apos;ll go to your organization&apos;s sign-in page and come straight back here.
      </p>
      <button
        type="button"
        onClick={startSignIn}
        className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-fg"
      >
        Sign in
      </button>
    </div>
  );
}

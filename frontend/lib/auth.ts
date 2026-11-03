// Sign-in (Layer 5): OpenID Connect authorization code flow with PKCE, via oidc-client-ts.
//
// The backend tells us at runtime whether sign-in is on and which provider to use
// (GET /v1/auth/config), so the same build works with and without Keycloak.
// Tokens live in sessionStorage: they survive reloads but not closing the tab.

import { type User, UserManager, WebStorageStateStore } from "oidc-client-ts";

import { API_URL } from "./api";

export interface AuthConfig {
  enabled: boolean;
  issuer: string | null;
  client_id: string;
  scope: string;
}

let configPromise: Promise<AuthConfig> | null = null;
let manager: UserManager | null = null;

/** The backend's sign-in settings (fetched once). */
export function getAuthConfig(): Promise<AuthConfig> {
  configPromise ??= fetch(`${API_URL}/v1/auth/config`).then((response) => {
    if (!response.ok) throw new Error(`auth config: HTTP ${response.status}`);
    return response.json() as Promise<AuthConfig>;
  });
  configPromise.catch(() => {
    configPromise = null; // let the next call try again
  });
  return configPromise;
}

/** The OIDC client for this browser tab, or null when sign-in is off. */
export async function getUserManager(): Promise<UserManager | null> {
  const config = await getAuthConfig();
  if (!config.enabled || !config.issuer) return null;
  manager ??= new UserManager({
    authority: config.issuer,
    client_id: config.client_id,
    redirect_uri: `${window.location.origin}/auth/callback`,
    post_logout_redirect_uri: `${window.location.origin}/`,
    response_type: "code", // + PKCE (S256), always on in oidc-client-ts
    scope: config.scope,
    automaticSilentRenew: true, // refreshes the access token shortly before it expires
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
  return manager;
}

/** The current access token, or null (signed out, expired, or sign-in off). */
export async function currentAccessToken(): Promise<string | null> {
  const userManager = await getUserManager();
  if (!userManager) return null;
  const user: User | null = await userManager.getUser();
  return user && !user.expired ? user.access_token : null;
}

/** Go to the provider's sign-in page; come back to `returnTo` afterwards. */
export async function signIn(returnTo: string): Promise<void> {
  const userManager = await getUserManager();
  await userManager?.signinRedirect({ state: { returnTo } });
}

/** Finish sign-in on /auth/callback. Gives back where to go next. */
export async function completeSignIn(): Promise<string> {
  const userManager = await getUserManager();
  if (!userManager) return "/";
  const user = await userManager.signinRedirectCallback();
  const state = user.state as { returnTo?: string } | undefined;
  return state?.returnTo && state.returnTo.startsWith("/") ? state.returnTo : "/";
}

/** Sign out here and at the provider. */
export async function signOut(): Promise<void> {
  const userManager = await getUserManager();
  if (!userManager) return;
  await userManager.signoutRedirect();
}

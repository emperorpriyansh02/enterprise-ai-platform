"use client";

import { useCallback, useSyncExternalStore } from "react";

// The admin key lives in sessionStorage: it survives page reloads but is forgotten when the tab
// closes. If storage is blocked, it's kept in memory for this page only.
const STORAGE_KEY = "eap.adminKey";
const CHANGE_EVENT = "eap-admin-key-change";
let memoryKey = "";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

function readKey(): string {
  try {
    return sessionStorage.getItem(STORAGE_KEY) ?? memoryKey;
  } catch {
    return memoryKey;
  }
}

/** The admin key (sent as X-Admin-Key on prompt changes) and a setter that remembers it. */
export function useAdminKey(): [string, (value: string) => void] {
  const key = useSyncExternalStore(subscribe, readKey, () => "");
  const setKey = useCallback((value: string) => {
    memoryKey = value;
    try {
      if (value) sessionStorage.setItem(STORAGE_KEY, value);
      else sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      // storage blocked: memoryKey still holds it
    }
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);
  return [key, setKey];
}

export function AdminKeyInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted">Admin key</span>
      <input
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="only if ADMIN_API_KEY is set"
        autoComplete="off"
        className="w-52 rounded-md border border-line bg-surface px-2 py-1.5 text-sm"
      />
    </label>
  );
}

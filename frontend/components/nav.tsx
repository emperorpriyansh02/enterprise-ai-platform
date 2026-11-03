"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import { ROLE_LABELS, type RoleName } from "@/lib/api";

// Each link shows only to users whose roles give its permission.
const LINKS = [
  { href: "/", label: "Chat", permission: "chat:use" },
  { href: "/prompts", label: "Prompts", permission: "prompts:read" },
  { href: "/knowledge", label: "Knowledge", permission: "knowledge:read" },
  { href: "/tools", label: "Tools", permission: "tools:admin" },
];

export default function NavBar() {
  const pathname = usePathname();
  const { status, me, can, signIn, signOut } = useAuth();
  const roles = (me?.roles ?? []).filter((r): r is RoleName => r in ROLE_LABELS);
  return (
    <header className="border-b border-line">
      <nav
        className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5"
        aria-label="Main"
      >
        <span className="text-sm font-semibold tracking-tight">Enterprise AI Platform</span>
        <ul className="flex gap-1">
          {LINKS.filter((link) => can(link.permission)).map((link) => {
            const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <li key={link.href}>
                <Link
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded-md px-2.5 py-1 text-sm ${
                    active ? "bg-surface font-medium text-foreground" : "text-muted hover:text-foreground"
                  }`}
                >
                  {link.label}
                </Link>
              </li>
            );
          })}
        </ul>
        <div className="ml-auto flex items-center gap-2 text-sm" aria-label="Account">
          {me && (
            <>
              <span className="font-medium">{me.name || me.username}</span>
              {roles.map((role) => (
                <span key={role} className="rounded bg-surface px-1.5 py-0.5 text-[11px] text-muted">
                  {ROLE_LABELS[role]}
                </span>
              ))}
              {me.is_dev ? (
                <span
                  className="rounded bg-warning/15 px-1.5 py-0.5 text-[11px] font-medium text-warning"
                  title="AUTH_ENABLED=false: no sign-in; every request acts as this dev user"
                >
                  sign-in off
                </span>
              ) : (
                <button
                  type="button"
                  onClick={signOut}
                  className="rounded-md px-2 py-1 text-muted hover:text-foreground"
                >
                  Sign out
                </button>
              )}
            </>
          )}
          {status === "signed-out" && (
            <button
              type="button"
              onClick={signIn}
              className="rounded-md px-2 py-1 text-muted hover:text-foreground"
            >
              Sign in
            </button>
          )}
        </div>
      </nav>
    </header>
  );
}

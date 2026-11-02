"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/prompts", label: "Prompts" },
  { href: "/knowledge", label: "Knowledge" },
];

export default function NavBar() {
  const pathname = usePathname();
  return (
    <header className="border-b border-line">
      <nav className="mx-auto flex w-full max-w-6xl items-center gap-5 px-4 py-2.5" aria-label="Main">
        <span className="text-sm font-semibold tracking-tight">Enterprise AI Platform</span>
        <ul className="flex gap-1">
          {LINKS.map((link) => {
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
      </nav>
    </header>
  );
}

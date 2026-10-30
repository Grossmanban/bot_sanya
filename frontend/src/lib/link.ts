"use client";

import { readSession, writeSession } from "./scroll";

/**
 * A button in the bot opens a place in the app: `…/?open=events&entry=4` (30.10 — event entries are reviewed in
 * «Админ → События»). The place is opened once per link: a reload («Обновить страницу») keeps where the person went.
 */
export type LinkTarget = { open: string; entry: number | null };

function parse(): LinkTarget | null {
  if (typeof window === "undefined") return null;
  const q = new URLSearchParams(window.location.search);
  const open = q.get("open");
  return open ? { open, entry: Number(q.get("entry")) || null } : null;
}

/** The link's target the first time this launch sees it, then null. */
export function takeLink(): LinkTarget | null {
  const target = parse();
  if (!target) return null;
  const seen = window.location.search;
  if (readSession("link") === seen) return null;
  writeSession("link", seen);
  return target;
}

/** The entry the link points at (to highlight it), while the app stays on that link. */
export function linkedEntry(open: string): number | null {
  const target = parse();
  return target?.open === open ? target.entry : null;
}

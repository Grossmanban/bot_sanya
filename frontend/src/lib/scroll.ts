"use client";

import { useEffect } from "react";

/**
 * Where the person was: the open tab / admin section and how far down each one was scrolled.
 * Kept in sessionStorage, so it survives a reload of the Mini App («Обновить страницу» in Telegram)
 * and coming back to a minimized app, but a fresh launch starts from the schedule.
 */

const SCROLL_KEY = "scroll-v1";

export function readSession(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeSession(key: string, value: string) {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* storage blocked: nothing is remembered */
  }
}

function positions(): Record<string, number> {
  try {
    return JSON.parse(readSession(SCROLL_KEY) ?? "{}") as Record<string, number>;
  } catch {
    return {};
  }
}

let active: string | null = null; // the screen whose position is being tracked
let restoring = false; // our own scrolling while the content loads is not the person's position

/** Call right before switching screens: the last bit of scrolling is kept even within the debounce. */
export function rememberScroll() {
  if (active) save(active);
}

function save(key: string) {
  if (restoring) return;
  writeSession(SCROLL_KEY, JSON.stringify({ ...positions(), [key]: Math.round(window.scrollY) }));
}

/** Content arrives after the switch (skeleton → data): hold the position while the page keeps changing
 *  its height (up to ~5 s), and let go at once when the person scrolls themselves. Returns a cancel function. */
function restore(key: string): () => void {
  const y = positions()[key] ?? 0;
  const apply = () => window.scrollTo({ top: Math.min(y, Math.max(0, document.documentElement.scrollHeight - window.innerHeight)) });
  apply();
  if (y === 0) return () => {};
  restoring = true;
  const observer = new ResizeObserver(apply);
  observer.observe(document.body);
  const events = ["wheel", "touchstart", "keydown"] as const;
  const stop = () => {
    restoring = false;
    observer.disconnect();
    clearTimeout(timer);
    events.forEach((e) => window.removeEventListener(e, stop));
  };
  events.forEach((e) => window.addEventListener(e, stop, { passive: true }));
  const timer = setTimeout(stop, 5000);
  return stop;
}

/** Remember and bring back the scroll position of one screen (`key`: a tab, or an admin section). */
export function useScrollMemory(key: string, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    active = key;
    const cancel = restore(key);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onScroll = () => {
      clearTimeout(timer);
      timer = setTimeout(() => save(key), 120);
    };
    // the app may be closed or minimized at any moment: write the position right away then
    const onHide = () => document.visibilityState === "hidden" && save(key);
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onHide);
    return () => {
      if (active === key) active = null;
      cancel();
      clearTimeout(timer);
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onHide);
    };
  }, [key, enabled]);
}

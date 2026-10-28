"use client";

import { useEffect, useState } from "react";
import { api, type ReasonKind } from "./api";

/** ТЗ 28.10 §2 (В2): the reason templates from «Настройки», loaded once and shared by every form.
 * Until they arrive (or for people who can't read them) the form shows the built-in defaults. */
let cache: ReasonKind[] | null = null;
let loading: Promise<ReasonKind[] | null> | null = null;
const listeners = new Set<(k: ReasonKind[]) => void>();

function load(): Promise<ReasonKind[] | null> {
  loading ??= api.admin.reasons().then((r) => {
    cache = r.kinds;
    listeners.forEach((fn) => fn(r.kinds));
    return r.kinds;
  }).catch(() => null);
  return loading;
}

/** Called by the settings editor after a change, so open forms show the new list. */
export function setReasons(kinds: ReasonKind[]) {
  cache = kinds;
  loading = Promise.resolve(kinds);
  listeners.forEach((fn) => fn(kinds));
}

export function useReasons(kind: string, fallback: string[] = []): string[] {
  const [kinds, setKinds] = useState<ReasonKind[] | null>(cache);
  useEffect(() => {
    listeners.add(setKinds);
    if (!cache) void load();
    return () => {
      listeners.delete(setKinds);
    };
  }, []);
  const found = kinds?.find((k) => k.kind === kind);
  return found ? found.items.map((i) => i.text) : fallback;
}

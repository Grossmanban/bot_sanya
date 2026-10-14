"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError, newKey } from "./api";
import { haptic } from "./telegram";

/** Load data on mount and whenever deps change; keeps previous data while reloading. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fnRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "error", String(e)));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload, setData };
}

/**
 * Runs one user action at a time with a stable idempotency key per action name:
 * a double tap or a retry after a network error reuses the key, so the server applies it once.
 */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const keys = useRef<Record<string, string>>({});

  const run = useCallback(
    async <T,>(name: string, fn: (key: string) => Promise<T>, success?: string): Promise<T | undefined> => {
      if (busy) return undefined;
      const key = (keys.current[name] ??= newKey());
      setBusy(name);
      try {
        const result = await fn(key);
        keys.current = {};
        haptic("success");
        if (success) toast.success(success);
        return result;
      } catch (e) {
        // keep the key only for network failures (request may have reached the server)
        if (!(e instanceof ApiError) || e.status !== 0) delete keys.current[name];
        haptic("error");
        if (!(e instanceof ApiError && e.code === "final_confirm_required")) {
          toast.error(e instanceof ApiError ? e.message : "Что-то пошло не так");
        }
        throw e;
      } finally {
        setBusy(null);
      }
    },
    [busy],
  );

  return { busy, run };
}

export function moscowToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(new Date());
}

export function readLocal(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeLocal(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode */
  }
}

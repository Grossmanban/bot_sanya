"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import hello from "@/assets/hello.webp";
import { webApp } from "@/lib/telegram";

const SHOW_MS = 200;
const GIVE_UP_MS = 4000; // a very slow network: better skip than flash in the middle of work

/**
 * Пасхалка: при каждом открытии приложения фото ровно на 0,2 секунды.
 * The countdown starts only once the picture is decoded and painted, so it is never cut short by a slow
 * image and never stretched by a slow app start (before hydration it is invisible). The file sits in
 * _next/static with a hashed name, cached for good after the first open. Telegram keeps a minimized
 * app alive, so coming back to it (activated / visibilitychange) plays it again.
 */
export function EasterEgg() {
  const img = useRef<HTMLImageElement>(null);
  const [run, setRun] = useState(0);
  const [shown, setShown] = useState(false);

  const replay = useCallback(() => setRun((n) => n + 1), []);

  useEffect(() => {
    const onVisible = () => document.visibilityState === "visible" && replay();
    document.addEventListener("visibilitychange", onVisible);
    const tg = webApp();
    tg?.onEvent("activated", replay);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      tg?.offEvent("activated", replay);
    };
  }, [replay]);

  useEffect(() => {
    const el = img.current;
    if (!el) return;
    let cancelled = false;
    let frame = 0;
    let hide: ReturnType<typeof setTimeout> | undefined;
    const giveUp = setTimeout(() => { cancelled = true; }, GIVE_UP_MS);
    const ready = el.complete && el.naturalWidth > 0 ? Promise.resolve() : new Promise<void>((resolve, reject) => {
      el.addEventListener("load", () => resolve(), { once: true });
      el.addEventListener("error", () => reject(new Error("no image")), { once: true });
    });
    ready
      .then(() => el.decode().catch(() => undefined))
      .then(() => {
        if (cancelled) return;
        clearTimeout(giveUp);
        setShown(true);
        // the next frame is the one that paints it: count from there
        frame = requestAnimationFrame(() => { hide = setTimeout(() => setShown(false), SHOW_MS); });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      clearTimeout(giveUp);
      clearTimeout(hide);
      cancelAnimationFrame(frame);
      setShown(false);
    };
  }, [run]);

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img ref={img} src={hello.src} alt="" aria-hidden decoding="sync"
         className="pointer-events-none fixed inset-0 z-[100] size-full bg-background object-cover"
         style={{ visibility: shown ? "visible" : "hidden" }} />
  );
}

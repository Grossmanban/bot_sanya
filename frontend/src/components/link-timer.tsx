"use client";

import { useEffect, useRef, useState } from "react";
import { Clock3 } from "lucide-react";
import { cn } from "@/lib/utils";

/** Seconds left from the server's own count (no clock skew); `onExpire` once at zero. */
export function useCountdown(seconds: number | null | undefined, onExpire?: () => void) {
  const [end, setEnd] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const fired = useRef(false);
  const expire = useRef(onExpire);
  expire.current = onExpire;

  useEffect(() => {
    if (seconds == null) return;
    setEnd(Date.now() + seconds * 1000);
    fired.current = false;
  }, [seconds]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const left = end == null ? null : Math.max(0, Math.round((end - now) / 1000));
  useEffect(() => {
    if (left === 0 && !fired.current) {
      fired.current = true;
      expire.current?.();
    }
  }, [left]);
  return left;
}

export function clock(left: number) {
  const h = Math.floor(left / 3600);
  const m = Math.floor((left % 3600) / 60);
  const s = left % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0");
  return h ? `${h}:${mm}:${String(s).padStart(2, "0")}` : `${mm}:${String(s).padStart(2, "0")}`;
}

/** ТЗ 27.10 §8: «сменится через …» — the old link and its QR stop working then. */
export function LinkTimer({ left, className }: { left: number | null; className?: string }) {
  if (left == null) return null;
  const soon = left < 300;
  return (
    <p className={cn("flex items-start gap-2 rounded-2xl px-4 py-3 text-sm", soon ? "bg-wait-soft text-wait" : "bg-muted/60", className)}>
      <Clock3 className="mt-0.5 size-4 shrink-0" />
      <span>
        {left > 0 ? <>Ссылка сменится через <b className="tabular">{clock(left)}</b>. </> : <>Ссылка меняется… </>}
        После этого старая ссылка и её QR-код перестанут работать — отправляйте свежую.
      </span>
    </p>
  );
}

/** A QR code rendered by the server (an SVG made from our own link). */
export function QrImage({ svg, className }: { svg: string; className?: string }) {
  return (
    <div className={cn("mx-auto w-full max-w-64 overflow-hidden rounded-2xl bg-white p-2 [&>svg]:h-auto [&>svg]:w-full", className)}
         dangerouslySetInnerHTML={{ __html: svg }} />
  );
}

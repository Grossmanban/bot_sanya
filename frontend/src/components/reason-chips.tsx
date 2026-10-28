"use client";

import { useReasons } from "@/lib/reasons";
import { cn } from "@/lib/utils";

/** ТЗ 28.10 §2 (В2): pick a reason template, then edit it in the field below before sending. */
export function ReasonChips({ kind, value, onPick, fallback = [] }: { kind: string; value: string; onPick: (r: string) => void;
  fallback?: string[] }) {
  const list = useReasons(kind, fallback);
  if (list.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {list.map((r) => (
        <button key={r} type="button" onClick={() => onPick(r)}
                className={cn("press max-w-full rounded-full px-3 py-1.5 text-left text-xs font-semibold break-words",
                  value === r ? "bg-primary text-primary-foreground" : "bg-muted")}>
          {r}
        </button>
      ))}
    </div>
  );
}

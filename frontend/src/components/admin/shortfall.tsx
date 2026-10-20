"use client";

import { Trophy } from "lucide-react";
import { cn } from "@/lib/utils";

/** Plan as the full track, fact as the fill: red below the plan, neutral on it, green with a trophy above it (ТЗ №17). */
export function PlanFactBar({ plan, fact, className }: { plan: number; fact: number; className?: string }) {
  const pct = Math.round((fact / Math.max(plan, 1)) * 100);
  const done = Math.min(100, pct);
  const short = fact < plan;
  const over = fact > plan;
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className={cn("relative h-3 overflow-hidden rounded-full", short ? "bg-cancel-soft" : "bg-muted")}>
        <span className={cn("bar-draw absolute inset-y-0 left-0 rounded-full", short ? "bg-cancel" : over ? "bg-go" : "bg-muted-foreground/60")}
              style={{ width: `${done}%` }} />
      </div>
      <div className="flex justify-between text-xs tabular">
        <span className={cn(short ? "font-semibold text-cancel" : over ? "font-semibold text-go" : "text-muted-foreground")}>
          факт {fact} шт. ({pct}%)
          {over && <> · <Trophy className="mb-0.5 inline size-3.5" /> перевыполнение на {pct - 100}%</>}
        </span>
        <span className="text-muted-foreground">план {plan} шт.</span>
      </div>
    </div>
  );
}

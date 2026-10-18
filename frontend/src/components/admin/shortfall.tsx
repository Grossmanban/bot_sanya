"use client";

import { cn } from "@/lib/utils";

/** Plan as the full track, fact as the fill; the missing part is shown in red. */
export function PlanFactBar({ plan, fact, className }: { plan: number; fact: number; className?: string }) {
  const done = Math.min(100, (fact / Math.max(plan, 1)) * 100);
  const short = fact < plan;
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="relative h-3 overflow-hidden rounded-full bg-cancel-soft">
        <span className={cn("bar-draw absolute inset-y-0 left-0 rounded-full", short ? "bg-cancel" : "bg-go")} style={{ width: `${done}%` }} />
      </div>
      <div className="flex justify-between text-xs tabular">
        <span className={short ? "font-semibold text-cancel" : "text-go"}>
          факт {fact} шт. ({Math.round((fact / Math.max(plan, 1)) * 100)}%)
        </span>
        <span className="text-muted-foreground">план {plan} шт.</span>
      </div>
    </div>
  );
}

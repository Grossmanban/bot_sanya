"use client";

import type { CSSProperties } from "react";
import { STATUS_META, type DayState } from "@/lib/format";
import { cn } from "@/lib/utils";

// The day line: 08:00–24:00 drawn as a track; shifts are bars on it.
const START = 8 * 60;
const END = 24 * 60;
export const HOURS = [8, 12, 16, 20, 24];

const minutes = (t: string) => {
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
};

export function span(start: string | null, end: string | null) {
  if (!start || !end) return null;
  const a = Math.min(Math.max(minutes(start), START), END);
  const b = Math.min(Math.max(minutes(end), START), END);
  if (b <= a) return null;
  return { left: ((a - START) / (END - START)) * 100, width: ((b - a) / (END - START)) * 100 };
}

interface Range {
  start_time: string | null;
  end_time: string | null;
}

function Ticks() {
  return (
    <>
      {HOURS.slice(1, -1).map((h) => (
        <span
          key={h}
          className="absolute inset-y-0 w-px bg-background/80"
          style={{ left: `${((h * 60 - START) / (END - START)) * 100}%` }}
        />
      ))}
    </>
  );
}

/** Compact line for the week list: my bar on top, colleagues as faint marks. */
export function DayTrack({
  state,
  mine,
  others,
  index = 0,
  className,
}: {
  state: DayState;
  mine: Range | null;
  others: Range[];
  index?: number;
  className?: string;
}) {
  const my = mine ? span(mine.start_time, mine.end_time) : null;
  const meta = STATUS_META[state];
  return (
    <div className={cn("relative h-2.5 overflow-hidden rounded-full bg-track", state === "pre_declared_off" && "hatch", className)}>
      <Ticks />
      {others.map((o, i) => {
        const s = span(o.start_time, o.end_time);
        return s ? (
          <span
            key={i}
            className="absolute inset-y-[3px] rounded-full bg-foreground/15"
            style={{ left: `${s.left}%`, width: `${s.width}%` }}
          />
        ) : null;
      })}
      {my && meta.bar && (
        <span
          className={cn("bar-draw absolute inset-y-0 rounded-full", meta.bar)}
          style={{ left: `${my.left}%`, width: `${my.width}%`, "--i": index } as CSSProperties}
        />
      )}
    </div>
  );
}

export function HourAxis({ className }: { className?: string }) {
  return (
    <div className={cn("relative h-4 text-[11px] text-muted-foreground tabular", className)}>
      {HOURS.map((h, i) => (
        <span
          key={h}
          className={cn("absolute top-0", i === 0 ? "" : i === HOURS.length - 1 ? "-translate-x-full" : "-translate-x-1/2")}
          style={{ left: `${((h * 60 - START) / (END - START)) * 100}%` }}
        >
          {h === 24 ? "24" : h}
        </span>
      ))}
    </div>
  );
}

export interface Lane {
  key: string;
  name: string;
  start_time: string | null;
  end_time: string | null;
  state: DayState;
  highlight?: boolean;
  caption?: string;
}

/** Detailed view of a day: one lane per person, labels above full-width bars. */
export function DayLanes({ lanes, className }: { lanes: Lane[]; className?: string }) {
  return (
    <div className={cn("space-y-3", className)}>
      <HourAxis />
      {lanes.map((lane, i) => {
        const s = span(lane.start_time, lane.end_time);
        const meta = STATUS_META[lane.state];
        const off = lane.state === "pre_declared_off";
        const range = lane.start_time && lane.end_time ? `${lane.start_time}–${lane.end_time}` : "";
        const detail = off ? "выходной" : [range, lane.caption].filter(Boolean).join(", ");
        return (
          <div key={lane.key} className="space-y-1.5">
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className={cn("truncate", lane.highlight ? "font-semibold" : "text-muted-foreground")}>{lane.name}</span>
              <span className="shrink-0 text-xs text-muted-foreground tabular">{detail}</span>
            </div>
            <div className={cn("relative h-3 overflow-hidden rounded-full bg-track", off && "hatch")}>
              <Ticks />
              {s && !off && (
                <span
                  className={cn(
                    "absolute inset-y-0 rounded-full transition-[left,width] duration-300 ease-out",
                    !lane.highlight ? "bg-foreground/20" : meta.bar || "bg-primary",
                    i < 8 && "bar-draw",
                  )}
                  style={{ left: `${s.left}%`, width: `${s.width}%`, "--i": i } as CSSProperties}
                />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

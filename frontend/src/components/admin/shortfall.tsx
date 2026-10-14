"use client";

import { useState } from "react";
import { LoaderCircle, TrendingDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Shortfall } from "@/lib/api";
import { dayMonth, plural, rub } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const PRESETS = [500, 1000, 2000];

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

/** One-tap fine for a missed plan. */
export function ShortfallDecision({
  shiftId,
  current,
  onDone,
}: {
  shiftId: number;
  current?: { status: "fined" | "waived" | null; penalty: number | null };
  onDone: () => void;
}) {
  const [custom, setCustom] = useState("");
  const [showCustom, setShowCustom] = useState(false);
  const { busy, run } = useAction();

  async function decide(amount: number | null) {
    try {
      await run(String(amount), () => api.admin.decideShortfall(shiftId, amount), amount ? `Штраф ${rub(amount)} назначен` : "Решено без штрафа");
      onDone();
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="space-y-2">
      {current?.status && (
        <p className="text-sm text-muted-foreground">
          Сейчас: {current.status === "fined" ? `штраф ${rub(current.penalty ?? 0)}` : "без штрафа"}. Можно изменить.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((a) => (
          <Button key={a} size="sm" disabled={!!busy}
                  className={cn("press h-10 rounded-xl bg-cancel px-4 font-semibold text-white hover:bg-cancel/90",
                    current?.status === "fined" && current.penalty === a && "ring-2 ring-cancel/40 ring-offset-2 ring-offset-card")}
                  onClick={() => decide(a)}>
            {busy === String(a) && <LoaderCircle className="animate-spin" />} {a} ₽
          </Button>
        ))}
        <Button size="sm" variant="secondary" className="press h-10 rounded-xl px-4 font-semibold" disabled={!!busy}
                onClick={() => setShowCustom((v) => !v)}>
          Своя сумма
        </Button>
        <Button size="sm" variant="ghost" className="press h-10 rounded-xl px-4 font-semibold" disabled={!!busy} onClick={() => decide(null)}>
          {busy === "null" && <LoaderCircle className="animate-spin" />} Без штрафа
        </Button>
      </div>
      {showCustom && (
        <div className="flex gap-2">
          <input inputMode="numeric" autoFocus placeholder="Сумма, ₽" value={custom}
                 onChange={(e) => setCustom(e.target.value.replace(/\D/g, "").slice(0, 7))}
                 className="h-10 min-w-0 flex-1 rounded-xl border-0 bg-muted px-3 tabular outline-none focus:ring-2 focus:ring-ring/25" />
          <Button size="sm" className="press h-10 rounded-xl px-4" disabled={!!busy || !Number(custom)} onClick={() => decide(Number(custom))}>
            Назначить
          </Button>
        </div>
      )}
    </div>
  );
}

/** Shortfalls waiting for an admin decision. */
export function ShortfallInbox({ version, onChanged }: { version: number; onChanged: () => void }) {
  const { data, reload } = useLoad(() => api.admin.shortfalls(), [version]);
  if (!data) return <Skeleton className="h-32 rounded-3xl" />;
  if (data.items.length === 0) {
    return (
      <section className="flex items-center gap-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <span className="grid size-10 place-items-center rounded-xl bg-go-soft text-go">
          <TrendingDown className="size-5 rotate-180" />
        </span>
        <p className="text-sm text-muted-foreground">Недоборов, ждущих решения, нет. Как только кто-то внесёт факт меньше плана, он появится здесь.</p>
      </section>
    );
  }
  return (
    <section className="overflow-hidden rounded-3xl bg-card shadow-lift ring-1 ring-cancel/25">
      <div className="flex items-center gap-3 px-5 pt-5 pb-3 md:px-6">
        <span className="grid size-10 place-items-center rounded-xl bg-cancel-soft text-cancel">
          <TrendingDown className="size-5" />
        </span>
        <div>
          <h2 className="font-semibold">Не по норме</h2>
          <p className="text-sm text-muted-foreground">
            {data.items.length} {plural(data.items.length, "смена ждёт", "смены ждут", "смен ждут")} решения
          </p>
        </div>
      </div>
      <ul className="divide-y border-t">
        {data.items.map((s) => (
          <ShortfallCard key={s.shift_id} s={s} onDone={async () => { await reload(); onChanged(); }} />
        ))}
      </ul>
    </section>
  );
}

function ShortfallCard({ s, onDone }: { s: Shortfall; onDone: () => void }) {
  return (
    <li className="row-in space-y-3 px-5 py-4 md:px-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold">{s.name}</p>
          <p className="text-sm text-muted-foreground">
            {dayMonth(s.date)}, {s.start_time}–{s.end_time}, {s.location}
          </p>
        </div>
        <span className="shrink-0 rounded-full bg-cancel-soft px-3 py-1 font-display text-sm font-semibold text-cancel tabular">−{s.shortfall}</span>
      </div>
      <PlanFactBar plan={s.volume} fact={s.fact_volume} />
      <ShortfallDecision shiftId={s.shift_id} onDone={onDone} />
    </li>
  );
}

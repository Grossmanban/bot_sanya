"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { Bell, ChevronLeft, ChevronRight, History, LoaderCircle, Wallet } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Pricing, type User } from "@/lib/api";
import { STATUS_META, WEEKDAYS_SHORT, dayMonth, parseDay, rub, timeRange, weekRange } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { bindBackButton } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { DayTrack } from "../day-line";
import { Avatar } from "../avatar";

type View = "main" | "history" | "penalties";

export function ProfileScreen({
  user,
  pricing,
  onUserChange,
}: {
  user: User;
  pricing: Pricing;
  onUserChange: (u: User) => void;
}) {
  const [view, setView] = useState<View>("main");
  const { busy, run } = useAction();
  const penalties = useLoad(() => api.myPenalties(), []);

  useEffect(() => bindBackButton(view !== "main", () => setView("main")), [view]);

  if (view === "history") return <HistoryView onBack={() => setView("main")} />;
  if (view === "penalties") return <PenaltiesView onBack={() => setView("main")} />;

  const working = user.work_status === "working";
  const penaltyTotal = penalties.data?.items.reduce((s, p) => s + (p.penalty_amount ?? 0), 0) ?? 0;

  async function setStatus(next: User["work_status"]) {
    if (next === user.work_status || busy) return;
    try {
      const res = await run("work", () => api.setWorkStatus(next));
      if (res) onUserChange(res.user);
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-6">
      <div className="space-y-4">
        <section className="flex items-center gap-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
          <Avatar user={user} className="size-16 rounded-2xl text-xl" />
          <div className="min-w-0">
            <h1 className="truncate font-display text-xl font-semibold">
              {user.first_name} {user.last_name}
            </h1>
            <p className="text-sm text-muted-foreground">
              {user.username ? `@${user.username}` : "без username"}
              <span className="text-muted-foreground/60">, ID {user.telegram_id}</span>
            </p>
          </div>
        </section>

        <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">Статус</h2>
            {busy === "work" && <LoaderCircle className="size-4 animate-spin text-muted-foreground" />}
          </div>
          <div role="radiogroup" className="relative mt-3 grid grid-cols-2 rounded-2xl bg-muted p-1">
            <span
              aria-hidden
              className={cn(
                "absolute inset-y-1 left-1 w-[calc(50%-0.25rem)] rounded-xl shadow-lift transition-all duration-300 ease-[cubic-bezier(.2,.8,.2,1)]",
                working ? "translate-x-0 bg-go" : "translate-x-full bg-card",
              )}
            />
            {(["working", "not_working"] as const).map((s) => (
              <button
                key={s}
                role="radio"
                aria-checked={user.work_status === s}
                onClick={() => setStatus(s)}
                className={cn(
                  "relative z-10 h-12 rounded-xl text-[15px] font-semibold transition-colors duration-300",
                  user.work_status === s ? (s === "working" ? "text-white" : "text-foreground") : "text-muted-foreground",
                )}
              >
                {s === "working" ? "Работаю" : "Не работаю"}
              </button>
            ))}
          </div>
          <p className="mt-3 text-sm text-muted-foreground">
            {working ? "Можно записываться на выходы." : "Запись в график закрыта, пока не включишь «Работаю»."}
          </p>
        </section>
      </div>

      <div className="space-y-4">
        <label className="flex cursor-pointer items-center gap-4 rounded-3xl bg-card p-5 shadow-lift md:px-6">
          <span className="grid size-10 place-items-center rounded-xl bg-muted">
            <Bell className="size-5" strokeWidth={1.8} />
          </span>
          <span className="flex-1">
            <span className="block font-medium">Напоминания в боте</span>
            <span className="block text-sm text-muted-foreground">Накануне смены и про график на следующую неделю</span>
          </span>
          <Switch
            checked={user.notify_reminders !== false}
            disabled={busy === "notify"}
            onCheckedChange={async (on) => {
              try {
                const res = await run("notify", () => api.setNotify(on), on ? "Напоминания включены" : "Напоминания выключены");
                if (res) onUserChange(res.user);
              } catch {
                /* toast shown */
              }
            }}
          />
        </label>

        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <MenuRow icon={History} label="История выходов" onClick={() => setView("history")} />
          <MenuRow
            icon={Wallet}
            label="Мои штрафы"
            value={penalties.data ? (penaltyTotal ? rub(penaltyTotal) : "нет") : undefined}
            onClick={() => setView("penalties")}
          />
        </section>

        <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
          <h2 className="font-semibold">Ставки</h2>
          <dl className="mt-3 space-y-3 text-[15px]">
            <div className="flex items-baseline justify-between gap-4">
              <dt className="text-muted-foreground">Обычная</dt>
              <dd className="font-display font-semibold">{pricing.base_rate} ₽ за шт.</dd>
            </div>
            <div className="flex items-baseline justify-between gap-4">
              <dt className="text-muted-foreground">Меньше {pricing.lazy_threshold} шт. за день</dt>
              <dd className="font-display font-semibold">{pricing.lazy_rate} ₽ за шт.</dd>
            </div>
          </dl>
        </section>
      </div>
    </div>
  );
}

function MenuRow({
  icon: Icon,
  label,
  value,
  onClick,
}: {
  icon: typeof History;
  label: string;
  value?: string;
  onClick: () => void;
}) {
  return (
    <button onClick={onClick} className="press flex w-full items-center gap-4 border-b px-5 py-4 text-left last:border-b-0 hover:bg-muted/60 md:px-6">
      <span className="grid size-10 place-items-center rounded-xl bg-muted">
        <Icon className="size-5" strokeWidth={1.8} />
      </span>
      <span className="flex-1 font-medium">{label}</span>
      {value && <span className="text-sm text-muted-foreground">{value}</span>}
      <ChevronRight className="size-4 text-muted-foreground" />
    </button>
  );
}

function SubHeader({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <div className="mb-5 flex items-center gap-2">
      <button onClick={onBack} className="press -ml-2 grid size-10 place-items-center rounded-full hover:bg-muted" aria-label="Назад">
        <ChevronLeft className="size-5" />
      </button>
      <h1 className="font-display text-xl font-semibold">{title}</h1>
    </div>
  );
}

function HistoryView({ onBack }: { onBack: () => void }) {
  const { data, loading, error } = useLoad(() => api.history(12), []);
  return (
    <div className="mx-auto max-w-2xl">
      <SubHeader title="История выходов" onBack={onBack} />
      {loading && !data && <Skeleton className="h-48 rounded-3xl" />}
      {error && <p className="text-sm text-destructive">{error.message}</p>}
      {data && data.weeks.length === 0 && (
        <p className="rounded-3xl bg-card p-6 text-muted-foreground shadow-lift">За прошлые недели записей нет.</p>
      )}
      <div className="space-y-6">
        {data?.weeks.map((w) => (
          <section key={w.start}>
            <h2 className="mb-2 px-1 text-sm font-semibold text-muted-foreground">{weekRange(w.start, w.end)}</h2>
            <div className="divide-y overflow-hidden rounded-3xl bg-card shadow-lift">
              {w.shifts.map((s, i) => {
                const meta = STATUS_META[s.status];
                const { d, weekday } = parseDay(s.date);
                return (
                  <div key={s.id} className="row-in grid grid-cols-[3rem_1fr] gap-4 px-4 py-3.5 md:px-5" style={{ "--i": i } as CSSProperties}>
                    <span className="flex flex-col items-center">
                      <span className="text-xs text-muted-foreground">{WEEKDAYS_SHORT[weekday]}</span>
                      <span className="font-display text-lg font-semibold">{d}</span>
                    </span>
                    <span className="min-w-0">
                      <span className="flex items-baseline justify-between gap-3">
                        <span className={cn("flex items-center gap-2 text-sm font-semibold", meta.text)}>
                          <span className={cn("size-2 rounded-full", meta.dot)} />
                          {meta.label}
                        </span>
                        {s.start_time && <span className="text-sm font-semibold tabular">{timeRange(s)}</span>}
                      </span>
                      <DayTrack className="mt-2.5" state={s.status} mine={s.start_time ? s : null} others={[]} index={i} />
                      <span className="mt-2 block text-xs text-muted-foreground">
                        {s.location ? `${s.location}, план ${s.volume} шт.` : ""}
                        {s.fact_volume != null ? `, факт ${s.fact_volume} шт.` : ""}
                        {s.shortfall > 0 && <span className="font-semibold text-cancel">, не по норме −{s.shortfall}</span>}
                        {s.shortfall_status === "fined" && <span className="text-final">, штраф {rub(s.shortfall_penalty ?? 0)}</span>}
                        {s.lazy ? ", налог на лень" : ""}
                        {s.status === "finally_cancelled" &&
                          (s.penalty_amount != null ? `, штраф ${rub(s.penalty_amount)}` : ", штраф ещё не назначен")}
                      </span>
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function PenaltiesView({ onBack }: { onBack: () => void }) {
  const { data, loading } = useLoad(() => api.myPenalties(), []);
  const total = data?.items.reduce((sum, p) => sum + (p.penalty_amount ?? 0), 0) ?? 0;
  return (
    <div className="mx-auto max-w-2xl">
      <SubHeader title="Мои штрафы" onBack={onBack} />
      {loading && !data && <Skeleton className="h-28 rounded-3xl" />}
      {data && data.items.length === 0 && (
        <div className="rounded-3xl bg-card p-6 shadow-lift">
          <p className="font-semibold">Штрафов нет</p>
          <p className="mt-1 text-sm text-muted-foreground">Штраф бывает за повторную отмену смены и за невыполненную норму.</p>
        </div>
      )}
      {data && data.items.length > 0 && (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <div className="px-5 pt-5 pb-4 md:px-6">
            <p className="text-sm text-muted-foreground">Всего</p>
            <p className="font-display text-3xl font-semibold text-final">{rub(total)}</p>
          </div>
          <ul className="divide-y border-t">
            {data.items.map((p) => (
              <li key={`${p.kind}-${p.id}`} className="flex items-center justify-between px-5 py-3.5 md:px-6">
                <div>
                  <p className="font-medium">{dayMonth(p.shift_date)}</p>
                  <p className="text-xs text-muted-foreground">{p.reason}</p>
                </div>
                <span className="font-semibold tabular">{p.penalty_amount != null ? rub(p.penalty_amount) : "ждёт решения"}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

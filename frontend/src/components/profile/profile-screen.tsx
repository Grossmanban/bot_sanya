"use client";

import { useEffect, useState } from "react";
import { ChevronRight, History, LoaderCircle, Wallet } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Pricing, type User } from "@/lib/api";
import { STATUS_META, ddmm, rub, shortDate, timeRange } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { bindBackButton } from "@/lib/telegram";
import { cn } from "@/lib/utils";

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

  useEffect(() => bindBackButton(view !== "main", () => setView("main")), [view]);

  if (view === "history") return <HistoryView onBack={() => setView("main")} />;
  if (view === "penalties") return <PenaltiesView onBack={() => setView("main")} />;

  const working = user.work_status === "working";
  const initials = `${user.first_name[0] ?? ""}${user.last_name[0] ?? ""}`.toUpperCase();

  async function toggle(next: boolean) {
    try {
      const res = await run("work", () => api.setWorkStatus(next ? "working" : "not_working"));
      if (res) onUserChange(res.user);
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="space-y-4">
      <section className="flex items-center gap-4 rounded-2xl border bg-card p-5 shadow-sm">
        <div className="flex size-16 shrink-0 items-center justify-center rounded-full bg-primary text-xl font-bold text-primary-foreground">
          {initials}
        </div>
        <div className="min-w-0">
          <h1 className="truncate text-xl font-bold">
            {user.first_name} {user.last_name}
          </h1>
          {user.username && <p className="text-sm text-muted-foreground">@{user.username}</p>}
          <p className="text-xs text-muted-foreground/70">ID {user.telegram_id}</p>
        </div>
      </section>

      <label
        className={cn(
          "flex cursor-pointer items-center justify-between gap-3 rounded-2xl border bg-card p-5 shadow-sm transition-colors",
          working && "border-go/40 bg-go-soft",
        )}
      >
        <div>
          <p className="text-sm text-muted-foreground">Я сейчас:</p>
          <p className={cn("text-lg font-bold", working ? "text-go" : "text-muted-foreground")}>
            {working ? "Работаю ✅" : "Не работаю ❌"}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {working ? "Можно вставать в график" : "Запись в график закрыта"}
          </p>
        </div>
        {busy ? (
          <LoaderCircle className="size-5 animate-spin text-primary" />
        ) : (
          <Switch checked={working} onCheckedChange={toggle} className="scale-125" aria-label="Рабочий статус" />
        )}
      </label>

      <section className="overflow-hidden rounded-2xl border bg-card shadow-sm">
        <MenuRow icon={History} label="Моя история смен" onClick={() => setView("history")} />
        <div className="border-t" />
        <MenuRow icon={Wallet} label="Мои штрафы" onClick={() => setView("penalties")} />
      </section>

      <section className="rounded-2xl border bg-card p-5 text-sm shadow-sm">
        <p className="mb-1 font-semibold">Ставки</p>
        <p className="text-muted-foreground">
          Обычная ставка: <b className="text-foreground">{pricing.base_rate} ₽/шт.</b>
          <br />
          При объёме меньше {pricing.lazy_threshold} шт. за день: <b className="text-foreground">{pricing.lazy_rate} ₽/шт.</b>{" "}
          («налог на лень»)
        </p>
      </section>
    </div>
  );
}

function MenuRow({ icon: Icon, label, onClick }: { icon: typeof History; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex w-full items-center gap-3 px-5 py-4 text-left active:bg-muted">
      <Icon className="size-5 text-primary" />
      <span className="flex-1 font-medium">{label}</span>
      <ChevronRight className="size-4 text-muted-foreground" />
    </button>
  );
}

function SubHeader({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <div className="mb-4 flex items-center gap-2">
      <button onClick={onBack} className="rounded-full p-1.5 text-muted-foreground hover:bg-muted" aria-label="Назад">
        <ChevronRight className="size-5 rotate-180" />
      </button>
      <h1 className="text-lg font-bold">{title}</h1>
    </div>
  );
}

function HistoryView({ onBack }: { onBack: () => void }) {
  const { data, loading, error } = useLoad(() => api.history(12), []);
  return (
    <div>
      <SubHeader title="История смен" onBack={onBack} />
      {loading && !data && <Skeleton className="h-40 rounded-2xl bg-card" />}
      {error && <p className="text-sm text-destructive">{error.message}</p>}
      {data && data.weeks.length === 0 && (
        <p className="rounded-2xl border bg-card p-5 text-sm text-muted-foreground">За прошлые недели записей нет.</p>
      )}
      <div className="space-y-4">
        {data?.weeks.map((w) => (
          <section key={w.start} className="rounded-2xl border bg-card p-4 shadow-sm">
            <p className="mb-2 text-sm font-semibold text-muted-foreground">
              {ddmm(w.start)} – {ddmm(w.end)}
            </p>
            <ul className="divide-y">
              {w.shifts.map((s) => {
                const meta = STATUS_META[s.status];
                return (
                  <li key={s.id} className="py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{shortDate(s.date)}</span>
                      <span className={cn("text-sm font-semibold", meta.className)}>
                        {meta.emoji} {meta.label}
                      </span>
                    </div>
                    {s.start_time && (
                      <p className="text-sm text-muted-foreground">
                        {timeRange(s)} · {s.location} · {s.volume} шт.
                        {s.lazy ? " · налог на лень" : ""}
                      </p>
                    )}
                    {s.status === "finally_cancelled" && (
                      <p className="text-sm text-final">
                        Штраф: {s.penalty_amount != null ? rub(s.penalty_amount) : "не назначен"}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
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
    <div>
      <SubHeader title="Мои штрафы" onBack={onBack} />
      {loading && !data && <Skeleton className="h-24 rounded-2xl bg-card" />}
      {data && data.items.length === 0 && (
        <p className="rounded-2xl border bg-card p-5 text-sm text-muted-foreground">Штрафов нет 🎉</p>
      )}
      {data && data.items.length > 0 && (
        <section className="rounded-2xl border bg-card p-4 shadow-sm">
          <ul className="divide-y">
            {data.items.map((p) => (
              <li key={p.id} className="flex items-center justify-between py-2.5">
                <div>
                  <p className="font-medium">{shortDate(p.shift_date)}</p>
                  <p className="text-xs text-muted-foreground">Окончательная отмена смены</p>
                </div>
                <span className="font-semibold text-final">
                  {p.penalty_amount != null ? rub(p.penalty_amount) : "ожидает решения"}
                </span>
              </li>
            ))}
          </ul>
          <div className="mt-2 flex justify-between border-t pt-3 font-semibold">
            <span>Итого</span>
            <span>{rub(total)}</span>
          </div>
        </section>
      )}
    </div>
  );
}

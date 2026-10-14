"use client";

import { useEffect, useMemo, useState } from "react";
import { CalendarClock, ChevronLeft, ChevronRight, TriangleAlert, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Day, type Pricing, type User } from "@/lib/api";
import { STATUS_META, dayState, ddmm, parseDay, shortDate, timeRange, WEEKDAYS } from "@/lib/format";
import { readLocal, useLoad, writeLocal } from "@/lib/hooks";
import { tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { DaySheet } from "./day-sheet";

const ONBOARDING_KEY = "onboarding-dismissed";

export function ScheduleScreen({
  user,
  pricing,
  onOpenProfile,
}: {
  user: User;
  pricing: Pricing;
  onOpenProfile: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const { data: week, loading, error, reload } = useLoad(() => api.week(offset), [offset]);
  const [openDate, setOpenDate] = useState<string | null>(null);
  const [showOnboarding, setShowOnboarding] = useState(false);

  useEffect(() => setShowOnboarding(readLocal(ONBOARDING_KEY) !== "1"), []);

  // urgent requests wait for an admin decision in the bot: poll while one is pending
  const waiting = week?.days.some((d) => d.my_shift?.status === "awaiting_approval");
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(reload, 15000);
    return () => clearInterval(t);
  }, [waiting, reload]);

  useEffect(() => {
    const onVisible = () => document.visibilityState === "visible" && reload();
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [reload]);

  const locations = useMemo(() => {
    const set = new Set<string>();
    const last = readLocal("last-location");
    if (last) set.add(last);
    week?.days.forEach((d) => {
      if (d.my_shift?.location) set.add(d.my_shift.location);
      d.colleagues.forEach((c) => c.location && set.add(c.location));
    });
    return [...set].slice(0, 6);
  }, [week]);

  const openDay = week?.days.find((d) => d.date === openDate) ?? null;

  return (
    <div className="space-y-4">
      <section className="flex items-center gap-3 rounded-2xl border bg-card p-4 shadow-sm">
        <div className="flex size-11 items-center justify-center rounded-xl bg-accent text-accent-foreground">
          <CalendarClock className="size-5" />
        </div>
        <div className="flex-1">
          <h1 className="text-lg leading-tight font-bold">{offset === 0 ? "Моя неделя" : "Следующая неделя"}</h1>
          <p className="text-sm text-muted-foreground">
            {week ? `${ddmm(week.start)} – ${ddmm(week.end)}` : " "}
          </p>
        </div>
        <div className="flex gap-1">
          <Button size="icon-sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(0)} aria-label="Текущая неделя">
            <ChevronLeft />
          </Button>
          <Button size="icon-sm" variant="outline" disabled={offset === 1} onClick={() => setOffset(1)} aria-label="Следующая неделя">
            <ChevronRight />
          </Button>
        </div>
      </section>

      {user.work_status !== "working" && (
        <section className="flex items-start gap-3 rounded-2xl border border-wait/30 bg-card p-4 text-sm shadow-sm">
          <TriangleAlert className="mt-0.5 size-5 shrink-0 text-wait" />
          <div className="flex-1">
            <p className="font-semibold">Сейчас стоит «Не работаю»</p>
            <p className="text-muted-foreground">Чтобы встать в график, включите статус «Работаю» в профиле.</p>
            <Button size="sm" variant="outline" className="mt-2" onClick={onOpenProfile}>
              Открыть профиль
            </Button>
          </div>
        </section>
      )}

      {showOnboarding && (
        <section className="relative rounded-2xl border bg-card p-5 shadow-sm">
          <button
            className="absolute top-3 right-3 rounded-full p-1 text-muted-foreground hover:bg-muted"
            onClick={() => {
              writeLocal(ONBOARDING_KEY, "1");
              setShowOnboarding(false);
            }}
            aria-label="Скрыть"
          >
            <X className="size-4" />
          </button>
          <h2 className="mb-3 font-semibold">Как поставить график</h2>
          <ol className="space-y-3">
            {[
              ["Нажми день", "Открой нужный день недели"],
              ["Выбери время, точку и объём", "Со скольки до скольки, куда идёшь и сколько штук"],
              ["Или нажми «Не выхожу»", "Так всем видно, что ты отметил выходной"],
            ].map(([title, text], i) => (
              <li key={title} className="flex gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-bold text-accent-foreground">
                  {i + 1}
                </span>
                <div>
                  <p className="leading-tight font-semibold">{title}</p>
                  <p className="text-sm text-muted-foreground">{text}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}

      {error && !week && (
        <div className="rounded-2xl border bg-card p-4 text-sm">
          {error.message}{" "}
          <Button size="sm" variant="link" onClick={reload}>
            Повторить
          </Button>
        </div>
      )}

      <div className="space-y-3">
        {!week && loading
          ? Array.from({ length: 7 }, (_, i) => <Skeleton key={i} className="h-20 rounded-2xl bg-card" />)
          : week?.days.map((day) => (
              <DayCard
                key={day.date}
                day={day}
                onOpen={() => {
                  tapHaptic();
                  setOpenDate(day.date);
                }}
              />
            ))}
      </div>

      <DaySheet
        day={openDay}
        user={user}
        pricing={pricing}
        locations={locations}
        onClose={() => setOpenDate(null)}
        onChanged={reload}
        onOpenProfile={() => {
          setOpenDate(null);
          onOpenProfile();
        }}
      />
    </div>
  );
}

function DayCard({ day, onOpen }: { day: Day; onOpen: () => void }) {
  const state = dayState(day.my_shift);
  const meta = STATUS_META[state];
  const mine = day.my_shift;
  const going = mine && (mine.status === "active" || mine.status === "restored" || mine.status === "awaiting_approval");
  return (
    <button
      type="button"
      disabled={day.is_past}
      onClick={onOpen}
      className={cn(
        "w-full rounded-2xl border bg-card px-4 py-3.5 text-left shadow-sm transition active:scale-[0.99]",
        state === "none" && !day.is_past && "border-dashed border-primary/30 bg-card/70 shadow-none",
        day.is_past && "opacity-55",
        day.is_today && "ring-2 ring-primary/30",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="flex items-center gap-2 text-[17px] leading-tight font-bold">
            {WEEKDAYS[parseDay(day.date).weekday]}
            {day.is_today && (
              <span className="rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold tracking-wide text-primary-foreground uppercase">
                сегодня
              </span>
            )}
          </p>
          <p className="text-sm text-muted-foreground">{shortDate(day.date)}</p>
        </div>
        <span className={cn("shrink-0 text-sm font-semibold", meta.className)}>
          {meta.emoji} {meta.label}
        </span>
      </div>
      {going && mine && (
        <p className="mt-1.5 text-sm font-medium">
          {timeRange(mine)} · {mine.location} · {mine.volume} шт.
        </p>
      )}
      {day.colleagues.length > 0 && (
        <p className="mt-1.5 text-sm text-muted-foreground">
          Ещё выходят:{" "}
          {day.colleagues.map((c, i) => (
            <span key={i}>
              {i > 0 && " · "}
              {c.first_name} {timeRange(c)}
            </span>
          ))}
        </p>
      )}
      {day.colleagues_off.length > 0 && (
        <p className="mt-0.5 text-xs text-muted-foreground/80">
          Не выходят: {day.colleagues_off.map((c) => c.first_name).join(", ")}
        </p>
      )}
    </button>
  );
}

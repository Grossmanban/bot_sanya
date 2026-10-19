"use client";

import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { ChevronLeft, ChevronRight, Pause, X } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, type Day, type Pricing, type User, type Week } from "@/lib/api";
import { STATUS_META, WEEKDAYS_SHORT, dayState, isGoing, parseDay, plural, rub, timeRange, weekRange } from "@/lib/format";
import { moscowTime, readLocal, useAction, useLoad, writeLocal } from "@/lib/hooks";
import { tapHaptic } from "@/lib/telegram";
import { useWide } from "@/lib/use-media";
import { cn } from "@/lib/utils";
import { MyGoals } from "../goals/goals";
import { DayTrack, HourAxis } from "../day-line";
import { DayDetail, DaySheet } from "./day-sheet";

const HINT_KEY = "hint-dismissed";

export function ScheduleScreen({
  user,
  pricing,
  onOpenProfile,
  onUserChange,
}: {
  user: User;
  pricing: Pricing;
  onOpenProfile: () => void;
  onUserChange: (u: User) => void;
}) {
  const wide = useWide();
  const [offset, setOffset] = useState(0);
  const { data: week, loading, error, reload } = useLoad(() => api.week(offset), [offset]);
  const pointsList = useLoad(() => api.points(), []);
  const [openDate, setOpenDate] = useState<string | null>(null);
  const [showHint, setShowHint] = useState(false);

  useEffect(() => setShowHint(readLocal(HINT_KEY) !== "1"), []);

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

  // on wide screens a day is always selected in the side panel
  const firstOpen = week?.days.find((d) => !d.is_past || d.my_shift?.report?.can_submit)?.date ?? null;
  const selected = week?.days.find((d) => d.date === openDate) ?? null;
  useEffect(() => {
    if (wide && week && (!openDate || !week.days.some((d) => d.date === openDate))) setOpenDate(firstOpen);
  }, [wide, week, openDate, firstOpen]);

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

  const open = (day: Day) => {
    if (day.is_past && !day.my_shift) return;
    tapHaptic();
    setOpenDate(day.date);
  };

  const detailProps = {
    user,
    pricing,
    locations,
    points: pointsList.data?.items ?? [],
    onChanged: reload,
    onOpenProfile,
  };

  return (
    <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_26rem] lg:items-start lg:gap-8 xl:grid-cols-[minmax(0,1fr)_28rem]">
      <div className="space-y-4">
        <WeekHero week={week} offset={offset} setOffset={setOffset} selected={wide ? openDate : null} onPick={open} />

        {user.account_state === "paused" ? (
          <div className="row-in flex items-start gap-3 rounded-3xl bg-wait-soft p-5 text-wait">
            <Pause className="mt-0.5 size-5 shrink-0" />
            <div>
              <p className="font-semibold">Аккаунт на паузе</p>
              <p className="mt-0.5 text-sm text-foreground/75">
                {user.state_reason ? `${user.state_reason}. ` : ""}Новые смены пока недоступны — снимет администратор.
              </p>
            </div>
          </div>
        ) : (
          user.work_status !== "working" && <WorkToggle onUserChange={onUserChange} />
        )}

        <MyGoals />

        {showHint && user.account_state !== "paused" && (
          <div className="flex items-start gap-3 px-1 text-sm text-muted-foreground">
            <p className="flex-1">Нажми на день, чтобы выбрать время, точку и объём. Если не выходишь — отметь выходной.</p>
            <button
              className="rounded-full p-1 hover:bg-muted"
              aria-label="Скрыть подсказку"
              onClick={() => {
                writeLocal(HINT_KEY, "1");
                setShowHint(false);
              }}
            >
              <X className="size-4" />
            </button>
          </div>
        )}

        {error && !week && (
          <div className="rounded-3xl bg-card p-5 text-sm shadow-lift">
            {error.message}{" "}
            <button className="font-semibold underline" onClick={reload}>
              Повторить
            </button>
          </div>
        )}

        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <div className="grid grid-cols-[3rem_1fr] gap-4 px-4 pt-4 pb-1 md:px-5">
            <span />
            <HourAxis />
          </div>
          <div className="divide-y divide-border/70">
            {!week && loading
              ? Array.from({ length: 7 }, (_, i) => (
                  <div key={i} className="px-4 py-4">
                    <Skeleton className="h-12 rounded-xl" />
                  </div>
                ))
              : week?.days.map((day, i) => (
                  <DayRow key={`${week.start}-${day.date}`} day={day} index={i} selected={wide && openDate === day.date} onOpen={() => open(day)} />
                ))}
          </div>
        </section>
      </div>

      {wide ? (
        <aside className="sticky top-10 hidden max-h-[calc(100dvh-5rem)] overflow-y-auto rounded-3xl bg-card p-6 shadow-lift lg:block">
          {selected ? (
            <DayDetail key={selected.date} day={selected} {...detailProps} />
          ) : (
            <p className="text-sm text-muted-foreground">Выберите день слева.</p>
          )}
        </aside>
      ) : (
        <DaySheet day={selected} onClose={() => setOpenDate(null)} {...detailProps} />
      )}
    </div>
  );
}

function WeekHero({
  week,
  offset,
  setOffset,
  selected,
  onPick,
}: {
  week: Week | null;
  offset: number;
  setOffset: (n: number) => void;
  selected: string | null;
  onPick: (d: Day) => void;
}) {
  const going = week?.days.filter((d) => isGoing(d.my_shift)) ?? [];
  const volume = going.reduce((s, d) => s + (d.my_shift?.volume ?? 0), 0);
  const pay = going.reduce((s, d) => s + (d.my_shift?.expected_pay ?? 0), 0);
  const summary = !week
    ? " "
    : going.length
      ? `Выходишь ${going.length} ${plural(going.length, "день", "дня", "дней")}, план ${volume} шт., около ${rub(pay)}`
      : offset === 0
        ? "На этой неделе выходов пока нет"
        : "На следующей неделе выходов пока нет";

  return (
    <section className="relative overflow-hidden rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm text-hero-muted">{offset === 0 ? "Эта неделя" : "Следующая неделя"}</p>
          <h1 className="mt-1 font-display text-[1.75rem] leading-tight font-semibold md:text-[2rem]">
            {week ? weekRange(week.start, week.end) : " "}
          </h1>
        </div>
        <div className="flex gap-1.5">
          <HeroButton label="Эта неделя" disabled={offset === 0} onClick={() => setOffset(0)}>
            <ChevronLeft className="size-5" />
          </HeroButton>
          <HeroButton label="Следующая неделя" disabled={offset === 1} onClick={() => setOffset(1)}>
            <ChevronRight className="size-5" />
          </HeroButton>
        </div>
      </div>
      <p className="mt-2 text-[15px] text-hero-muted">{summary}</p>

      <div className="mt-5 grid grid-cols-7 gap-1.5">
        {(week?.days ?? Array.from({ length: 7 }, () => null)).map((day, i) => {
          if (!day) return <span key={i} className="h-[4.25rem] rounded-2xl bg-white/5" />;
          const meta = STATUS_META[dayState(day.my_shift)];
          const { d } = parseDay(day.date);
          return (
            <button
              key={day.date}
              disabled={day.is_past}
              onClick={() => onPick(day)}
              className={cn(
                "press flex h-[4.25rem] flex-col items-center justify-center gap-0.5 rounded-2xl text-xs",
                day.is_today ? "bg-white text-hero" : "bg-white/[0.07] hover:bg-white/[0.12]",
                selected === day.date && !day.is_today && "ring-2 ring-white/50",
                selected === day.date && day.is_today && "ring-2 ring-white/50 ring-offset-2 ring-offset-hero",
                day.is_past && "opacity-35",
              )}
            >
              <span className={day.is_today ? "text-hero/60" : "text-hero-muted"}>{WEEKDAYS_SHORT[i]}</span>
              <span className="font-display text-lg leading-none font-semibold">{d}</span>
              <span className={cn("mt-1 size-1.5 rounded-full", dayState(day.my_shift) === "none" ? "bg-current opacity-25" : meta.dot)} />
            </button>
          );
        })}
      </div>
    </section>
  );
}

function HeroButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="press grid size-10 place-items-center rounded-full bg-white/10 hover:bg-white/15 disabled:opacity-30"
    >
      {children}
    </button>
  );
}

function WorkToggle({ onUserChange }: { onUserChange: (u: User) => void }) {
  const { busy, run } = useAction();
  return (
    <label className="flex cursor-pointer items-center gap-4 rounded-3xl bg-card p-4 shadow-lift md:px-5">
      <span className="flex-1">
        <span className="block font-semibold">Сейчас стоит «Не работаю»</span>
        <span className="block text-sm text-muted-foreground">Включи, чтобы записываться на выходы</span>
      </span>
      <Switch
        checked={false}
        disabled={!!busy}
        aria-label="Работаю"
        onCheckedChange={async () => {
          try {
            const res = await run("work", () => api.setWorkStatus("working"), "Статус: работаю");
            if (res) onUserChange(res.user);
          } catch {
            /* toast shown */
          }
        }}
      />
    </label>
  );
}

const REPORT_FLAG: Record<string, { label: string; tone: "wait" | "cancel" }> = {
  pending_review: { label: "Отчёт на проверке", tone: "wait" },
  needs_fix: { label: "Исправить отчёт", tone: "wait" },
  rejected: { label: "Отчёт отклонён", tone: "cancel" },
  no_show: { label: "Неявка", tone: "cancel" },
};

const FLAG_TONE = {
  wait: { text: "text-wait", dot: "bg-wait" },
  cancel: { text: "text-cancel", dot: "bg-cancel" },
};

function DayRow({ day, index, selected, onOpen }: { day: Day; index: number; selected: boolean; onOpen: () => void }) {
  const state = dayState(day.my_shift);
  const meta = STATUS_META[state];
  const mine = day.my_shift;
  const { d, weekday } = parseDay(day.date);
  const withTime = mine?.start_time && state !== "pre_declared_off";
  const names = day.colleagues.map((c) => c.first_name);
  const report = mine?.report;
  const needsReport =
    !!report?.can_submit && (report.state === "needs_fix" ||
      (report.state === null && (day.is_past || (day.is_today && !!mine?.end_time && mine.end_time <= moscowTime()))));
  const flag = REPORT_FLAG[report?.state ?? ""];
  const short = (mine?.shortfall ?? 0) > 0 || flag?.tone === "cancel";

  return (
    <button
      type="button"
      disabled={day.is_past && !mine}
      onClick={onOpen}
      style={{ "--i": index } as CSSProperties}
      className={cn(
        "row-in grid w-full grid-cols-[3rem_1fr] gap-4 px-4 py-3.5 text-left transition-colors md:px-5",
        (!day.is_past || mine) && "hover:bg-muted/60 active:bg-muted",
        selected && "bg-muted",
        day.is_past && !needsReport && !short && !flag && "opacity-45",
      )}
    >
      <span className="flex flex-col items-center gap-0.5 pt-0.5">
        <span className="text-xs text-muted-foreground">{WEEKDAYS_SHORT[weekday]}</span>
        <span
          className={cn(
            "grid size-9 place-items-center rounded-xl font-display text-lg leading-none font-semibold",
            day.is_today && "bg-primary text-primary-foreground",
          )}
        >
          {d}
        </span>
      </span>

      <span className="min-w-0">
        <span className="flex items-baseline justify-between gap-3">
          <span className={cn("flex items-center gap-2 text-sm font-semibold",
            flag ? FLAG_TONE[flag.tone].text : short ? "text-cancel" : meta.text)}>
            <span className={cn("size-2 rounded-full", flag ? FLAG_TONE[flag.tone].dot : short ? "bg-cancel" : meta.dot)} />
            {flag ? flag.label : short ? "Не по норме" : meta.label}
          </span>
          {withTime && (
            <span className={cn("text-sm font-semibold tabular", state === "cancelled" || state === "finally_cancelled" ? "text-muted-foreground line-through" : "")}>
              {timeRange(mine)}
            </span>
          )}
        </span>
        <DayTrack
          className="mt-2.5"
          state={state}
          mine={withTime ? mine : null}
          others={day.colleagues}
          index={index}
        />
        <span className="mt-2 flex justify-between gap-3 text-xs text-muted-foreground">
          <span className="truncate">
            {needsReport ? (
              <span className="font-semibold text-wait">Сдай отчёт: факт и скриншот</span>
            ) : mine?.fact_volume != null ? (
              mine.shortfall > 0 ? (
                <span className="font-semibold text-cancel">
                  {mine.fact_volume} из {mine.volume} шт. (−{mine.shortfall})
                  {mine.shortfall_status === "fined" ? `, штраф ${rub(mine.shortfall_penalty ?? 0)}` : ""}
                </span>
              ) : (
                `${mine.location}, план ${mine.volume}, факт ${mine.fact_volume} шт.`
              )
            ) : isGoing(mine) || state === "awaiting_approval"
              ? `${mine!.location}, ${mine!.volume} шт.`
              : names.length
                ? `Выходят: ${names.join(", ")}`
                : day.is_past
                  ? ""
                  : "Пока никто не выходит"}
          </span>
          {(isGoing(mine) || state === "awaiting_approval") && !short && names.length > 0 && (
            <span className="shrink-0">
              ещё {names.length} {plural(names.length, "человек", "человека", "человек")}
            </span>
          )}
        </span>
      </span>
    </button>
  );
}

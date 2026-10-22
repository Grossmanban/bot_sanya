"use client";

import { useState } from "react";
import { ChevronDown, CircleCheck, Crown, Flag, Target } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Goal, type LiveEvent } from "@/lib/api";
import { dayMonth } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { Leaderboard, RULE } from "./events/live";
import { PrizeThumb, prizeText } from "./lifecycle";

/** ТЗ №22: «Архив» — goals and events that left the feed, with their results and winners. */
export function ArchiveList() {
  const { data, error } = useLoad(() => api.archive(), []);
  if (error) return <p className="text-sm text-destructive">{error.message}</p>;
  if (!data) return <Skeleton className="h-48 rounded-3xl" />;
  if (!data.events.length && !data.goals.length) {
    return <p className="rounded-3xl bg-card p-6 text-muted-foreground shadow-lift">В архиве пока пусто: прошедшие цели и события появятся здесь.</p>;
  }
  return (
    <div className="space-y-6">
      {data.events.length > 0 && (
        <section className="space-y-3">
          <h2 className="flex items-center gap-2 px-1 text-sm font-semibold text-muted-foreground"><Flag className="size-4" /> События</h2>
          {data.events.map((e) => <ArchivedEvent key={e.id} event={e} />)}
        </section>
      )}
      {data.goals.length > 0 && (
        <section className="space-y-3">
          <h2 className="flex items-center gap-2 px-1 text-sm font-semibold text-muted-foreground"><Target className="size-4" /> Цели</h2>
          {data.goals.map((g) => <ArchivedGoal key={g.id} goal={g} />)}
        </section>
      )}
    </div>
  );
}

const ddmm = (iso: string) => dayMonth(iso.slice(0, 10));

function ArchivedEvent({ event: e }: { event: LiveEvent }) {
  const [open, setOpen] = useState(false);
  const winners = e.leaderboard.filter((r) => r.winner);
  const when = e.status === "active" ? "скрыто администрацией" : `Завершено ${ddmm(e.finished_at ?? e.ends_at)}`;
  return (
    <div className="overflow-hidden rounded-3xl bg-card shadow-lift">
      <button className="press flex w-full items-start gap-3 p-5 text-left" aria-expanded={open}
              onClick={() => { tapHaptic(); setOpen((v) => !v); }}>
        <span className="min-w-0 flex-1">
          <span className="block font-semibold">{e.title}</span>
          <span className="block text-xs text-muted-foreground">{when} · {RULE(e)}</span>
          <span className="mt-1.5 block text-sm">
            {winners.length
              ? <><Crown className="mr-1 inline size-4 text-wait" />{winners.map((w) => w.name).join(", ")}</>
              : <span className="text-muted-foreground">Победителей нет</span>}
          </span>
          {e.my.score > 0 && <span className="block text-xs text-muted-foreground">Мой результат: {e.my.confirmed} шт.{e.my.winner ? " · 👑 победа" : ""}</span>}
        </span>
        {(e.prize || e.prize_type || e.prize_photo_url) && <PrizeThumb url={e.prize_photo_url} kind={e.prize_media_kind} className="size-12" />}
        <ChevronDown className={cn("mt-1 size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="row-in space-y-3 border-t px-5 pt-4 pb-5">
          {prizeText(e) && <p className="text-sm">🏆 {prizeText(e)}</p>}
          {e.leaderboard.length ? <Leaderboard event={e} /> : <p className="text-sm text-muted-foreground">Заявок не было.</p>}
        </div>
      )}
    </div>
  );
}

function ArchivedGoal({ goal: g }: { goal: Goal }) {
  const value = g.mode === "shared" ? g.total : g.my_volume;
  const pct = Math.min(100, Math.round((value / Math.max(g.target, 1)) * 100));
  return (
    <div className="space-y-2.5 rounded-3xl bg-card p-5 shadow-lift">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-semibold">{g.done && <CircleCheck className="mr-1 inline size-4 text-go" />}{g.title}</p>
          <p className="text-xs text-muted-foreground">
            {dayMonth(g.date_from)} – {dayMonth(g.date_to)} · {g.done ? `выполнена${g.completed_at ? ` ${ddmm(g.completed_at)}` : ""}` : "не выполнена"}
          </p>
          {prizeText(g) && <p className="mt-1 text-sm">🏆 {prizeText(g)}</p>}
        </div>
        {(g.prize || g.prize_type || g.prize_photo_url) && <PrizeThumb url={g.prize_photo_url} className="size-12" />}
      </div>
      <div className="relative h-2 overflow-hidden rounded-full bg-muted">
        <span className={cn("absolute inset-y-0 left-0 rounded-full", g.done ? "bg-go" : "bg-primary")} style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs tabular text-muted-foreground">
        {g.mode === "shared" ? "Команда" : "Мой результат"}: <b className="text-foreground">{value} из {g.target} шт.</b>
        {g.my_annulled ? ` · результат аннулирован: ${g.my_annulled}` : ""}
      </p>
    </div>
  );
}

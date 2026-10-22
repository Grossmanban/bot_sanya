"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronLeft, Crown, Flag, LoaderCircle, ScrollText, Target, Timer, TrendingUp, Trophy, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type LiveEvent } from "@/lib/api";
import { plural } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { PrizeThumb, prizeText } from "../lifecycle";
import { PhotoPicker } from "../report/photo";
import { ArchiveList } from "../archive";
import { displayHtml } from "../admin/rich-editor";

/** Countdown to a moment, ticking every second. */
export function useCountdown(iso: string) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const left = Math.max(0, Date.parse(iso) - now);
  const h = Math.floor(left / 3_600_000);
  const m = Math.floor((left % 3_600_000) / 60_000);
  const s = Math.floor((left % 60_000) / 1000);
  const d = Math.floor(h / 24);
  const hms = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return {
    left, days: d, hours: h % 24,
    text: d > 0 ? `${d} д ${h % 24} ч` : hms,
    /** for a badge: «2 д 5 ч», «3 ч 12 мин», «12 мин» */
    short: d > 0 ? `${d} д ${h % 24} ч` : h > 0 ? `${h} ч ${m} мин` : m > 0 ? `${m} мин` : `${s} с`,
  };
}

/** «Гонка до 30 шт., 1 победитель» — worded by the server, with the right case endings. */
export const RULE = (e: LiveEvent) => e.rule;

const PHASE: Record<LiveEvent["phase"], string> = {
  upcoming: "скоро",
  live: "идёт",
  review: "проверка последних заявок",
  finished: "завершено",
  cancelled: "отменено",
};

/** The event's kind as an icon and a short name. */
function ModeIcon({ mode, className }: { mode: LiveEvent["mode"]; className?: string }) {
  return mode === "first_to_reach" ? <Flag className={className} /> : <TrendingUp className={className} />;
}

/** The phase as a pill: a pulsing dot while it runs, the time left next to it. */
function PhaseBadge({ event, dark }: { event: LiveEvent; dark?: boolean }) {
  const until = useCountdown(event.phase === "upcoming" ? event.starts_at : event.ends_at);
  const live = event.phase === "live";
  const tail = event.phase === "upcoming" ? ` · через ${until.short}` : live ? ` · ещё ${until.short}` : "";
  return (
    <span className={cn("inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 text-xs font-semibold",
      dark ? "bg-white/10 text-hero-foreground" : live ? "bg-go-soft text-go" : "bg-muted text-muted-foreground")}>
      <span className={cn("size-1.5 rounded-full", live ? "animate-pulse bg-[#22c55e]" : dark ? "bg-white/50" : "bg-muted-foreground/60")} />
      {PHASE[event.phase]}{tail}
    </span>
  );
}

/** The employee's events: a list, or straight into the only one. Polls the board every 5 s. */
export function EventsScreen() {
  const { data, reload } = useLoad(() => api.events(), []);
  const [openId, setOpenId] = useState<number | null>(null);
  const [archive, setArchive] = useState(false);
  useEffect(() => {
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, [reload]);
  if (archive) {
    return (
      <div className="mx-auto max-w-2xl space-y-4">
        <Button variant="ghost" size="sm" className="h-9 rounded-xl" onClick={() => setArchive(false)}><ChevronLeft /> События</Button>
        <ArchiveList />
      </div>
    );
  }
  if (!data) return <Skeleton className="h-96 rounded-3xl" />;
  const items = data.items;
  const current = items.find((e) => e.id === openId) ?? (items.length === 1 ? items[0] : null);
  const toArchive = (
    <button onClick={() => { tapHaptic(); setArchive(true); }} className="press mx-auto block text-sm font-medium text-muted-foreground underline-offset-4 hover:underline">
      Архив прошедших событий и целей
    </button>
  );
  if (current) {
    return (
      <div className="space-y-6">
        <EventView event={current} onBack={items.length > 1 ? () => setOpenId(null) : undefined} onChanged={reload} />
        {toArchive}
      </div>
    );
  }
  if (!items.length) {
    return <div className="mx-auto max-w-2xl space-y-4"><p className="rounded-3xl bg-card p-6 text-muted-foreground shadow-lift">Сейчас событий нет.</p>{toArchive}</div>;
  }
  return (
    <div className="mx-auto max-w-2xl space-y-6">
    <ul className="space-y-3">
      {items.map((e) => (
        <li key={e.id}>
          <button onClick={() => { tapHaptic(); setOpenId(e.id); }}
                  className="press flex w-full items-start gap-4 rounded-3xl bg-card p-5 text-left shadow-lift">
            <span className={cn("grid size-12 shrink-0 place-items-center rounded-2xl",
              e.phase === "live" ? "bg-hero text-hero-foreground" : "bg-muted text-muted-foreground")}>
              <ModeIcon mode={e.mode} className="size-5" />
            </span>
            <span className="min-w-0 flex-1 space-y-1">
              <span className="block font-semibold">{e.title}</span>
              <span className="block text-sm text-muted-foreground">{e.rule}</span>
              {prizeText(e) && <span className="block truncate text-sm">🏆 {prizeText(e)}</span>}
              <PhaseBadge event={e} />
            </span>
            {e.prize_photo_url && <PrizeThumb url={e.prize_photo_url} kind={e.prize_media_kind} className="size-14" />}
          </button>
        </li>
      ))}
    </ul>
    {toArchive}
    </div>
  );
}

/** HH:MM:SS as three tiles; days when it's far. */
function Clock({ iso, stopped }: { iso: string; stopped?: boolean }) {
  const c = useCountdown(iso);
  if (!stopped && c.days > 0) {
    return <p className="font-display text-3xl font-semibold tabular">{c.days} {plural(c.days, "день", "дня", "дней")} {c.hours} ч</p>;
  }
  const parts = stopped ? ["00", "00", "00"] : c.text.split(":");
  return (
    <div className="flex items-start gap-1.5">
      {parts.map((p, i) => (
        <div key={i} className="flex items-start gap-1.5">
          {i > 0 && <span className="pt-1.5 font-display text-2xl font-semibold text-hero-muted">:</span>}
          <span className="grid min-w-14 place-items-center rounded-2xl bg-white/10 px-2 py-1.5">
            <span className="font-display text-3xl leading-none font-semibold tabular">{p}</span>
            <span className="mt-1 text-[10px] tracking-wide text-hero-muted uppercase">{["час", "мин", "сек"][i]}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

function EventView({ event, onBack, onChanged }: { event: LiveEvent; onBack?: () => void; onChanged: () => void }) {
  const leader = event.leaderboard[0]?.score ?? 0;
  const goal = event.mode === "first_to_reach" ? event.target ?? 1 : Math.max(leader, 1);
  const pct = Math.min(100, Math.round((event.my.score / goal) * 100));
  const lastRejected = event.my.entries.find((e) => e.status === "rejected");
  const prize = prizeText(event);
  const showRejected = lastRejected && !event.my.pending && event.my.entries[0]?.status === "rejected";
  const chip = "inline-flex h-8 items-center gap-1.5 rounded-full bg-white/10 px-3 text-sm font-medium";

  return (
    <div className="mx-auto max-w-2xl space-y-4 lg:grid lg:max-w-6xl lg:grid-cols-[minmax(0,1fr)_24rem] lg:items-start lg:gap-6 lg:space-y-0">
      <div className="space-y-4">
        {onBack && <Button variant="ghost" size="sm" className="h-9 rounded-xl" onClick={onBack}><ChevronLeft /> Все события</Button>}
        <section className="relative overflow-hidden rounded-3xl bg-hero p-6 text-hero-foreground shadow-lift-strong">
          <span className="absolute -top-16 -right-16 size-56 rounded-full bg-white/5" aria-hidden />
          <span className="absolute -bottom-20 -left-10 size-44 rounded-full bg-white/[0.03]" aria-hidden />
          <div className="relative flex flex-wrap items-center gap-2">
            <PhaseBadge event={event} dark />
            {event.network && <span className="text-xs text-hero-muted">сеть «{event.network}»</span>}
          </div>
          <h1 className="relative mt-3 font-display text-[1.75rem] leading-tight font-semibold break-words">{event.title}</h1>
          {/* the conditions as chips: the kind with its target, winners, attempts */}
          <div className="relative mt-3 flex flex-wrap gap-1.5">
            <span className={chip}>
              <ModeIcon mode={event.mode} className="size-3.5" />
              {event.mode === "first_to_reach" ? `Гонка до ${event.target} шт.` : "Кто больше сделает"}
            </span>
            <span className={chip}><Crown className="size-3.5" />{event.max_winners} {plural(event.max_winners, "победитель", "победителя", "победителей")}</span>
            <span className={chip}><Target className="size-3.5" />{event.max_attempts} {plural(event.max_attempts, "попытка", "попытки", "попыток")}</span>
          </div>
          <p className="relative mt-3 text-[15px] text-hero-muted">{event.how_to_win}</p>

          <div className="relative mt-5">
            <p className="mb-1.5 flex items-center gap-1.5 text-xs text-hero-muted"><Timer className="size-3.5" />
              {event.phase === "upcoming" ? "до старта" : event.phase === "live" ? "до финиша" : "время вышло"}</p>
            <Clock iso={event.phase === "upcoming" ? event.starts_at : event.ends_at} stopped={event.phase !== "upcoming" && event.phase !== "live"} />
          </div>

          {(prize || event.prize_photo_url) && (
            <div className="relative mt-5 flex items-center gap-4 rounded-2xl bg-white/10 p-3">
              {/* ТЗ №20: the prize photo or video opens full screen; 🎁 when there is none */}
              <PrizeThumb url={event.prize_photo_url} kind={event.prize_media_kind} className="size-16 bg-white/10" />
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 text-xs text-hero-muted"><Trophy className="size-3.5" /> Приз</p>
                <p className="text-lg leading-snug font-semibold break-words">{prize || "Сюрприз"}</p>
              </div>
            </div>
          )}
        </section>

        {event.description_html && (
          <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
            <h2 className="mb-2 flex items-center gap-2 font-semibold"><ScrollText className="size-4" /> Правила</h2>
            <div className="tg-rich text-[15px] leading-relaxed break-words whitespace-pre-wrap"
                 dangerouslySetInnerHTML={{ __html: displayHtml(event.description_html) }} />
          </section>
        )}

        <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
          <div className="flex items-baseline justify-between">
            <h2 className="font-semibold">{event.my.winner ? "👑 Вы победитель" : "Мой результат"}</h2>
            <span className="text-sm text-muted-foreground">осталось попыток: {event.my.attempts_left} из {event.max_attempts}</span>
          </div>
          <div className="relative h-3 overflow-hidden rounded-full bg-muted">
            <span className={cn("bar-draw absolute inset-y-0 left-0 rounded-full", event.my.winner ? "bg-go" : "bg-primary")} style={{ width: `${pct}%` }} />
          </div>
          <p className="text-sm tabular">
            <b>{event.my.score} шт.</b>
            {event.mode === "first_to_reach" ? ` из ${event.target}` : leader ? ` · у лидера ${leader}` : ""}
            {event.my.pending && <span className="text-wait"> · заявка на проверке</span>}
          </p>
          {showRejected && (
            <div className="row-in flex items-start gap-3 rounded-2xl bg-cancel-soft p-4 text-sm">
              <XCircle className="mt-0.5 size-5 shrink-0 text-cancel" />
              <div>
                <p className="font-semibold text-cancel">
                  {lastRejected!.annulled ? `Результат отменён (${lastRejected!.value} шт.)` : `Заявка отклонена (попытка ${lastRejected!.attempt})`}
                </p>
                <p className="mt-0.5">{lastRejected!.reason}</p>
                <p className="mt-1 text-muted-foreground">
                  {lastRejected!.annulled && event.my.can_submit ? "Попытка не сгорела — можно отправить заявку снова."
                    : event.my.attempts_left ? `Исправьте и отправьте снова — осталось ${event.my.attempts_left}.` : "Попытки закончились, участие закрыто."}
                </p>
              </div>
            </div>
          )}
          {event.my.can_submit && <EntryForm event={event} onDone={onChanged} />}
        </section>
      </div>

      <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6 lg:sticky lg:top-10">
        <h2 className="mb-3 flex items-center gap-2 font-semibold"><Trophy className="size-4" /> Рейтинг</h2>
        {event.leaderboard.length ? <Leaderboard event={event} /> : <p className="text-sm text-muted-foreground">Пока никто не отправил результат.</p>}
        {event.mode === "accumulative" && <p className="mt-3 text-xs text-muted-foreground">Светлая часть — заявки на проверке; призы — по подтверждённым.</p>}
      </section>
    </div>
  );
}

/** Live board: rows slide to their new places (FLIP) when the order changes. */
export function Leaderboard({ event }: { event: LiveEvent }) {
  const rows = useRef(new Map<number, HTMLLIElement>());
  const last = useRef(new Map<number, number>());
  const top = Math.max(1, ...event.leaderboard.map((r) => r.score));
  useLayoutEffect(() => {
    rows.current.forEach((el, id) => {
      const y = el.getBoundingClientRect().top;
      const prev = last.current.get(id);
      if (prev !== undefined && prev !== y) {
        el.animate([{ transform: `translateY(${prev - y}px)` }, { transform: "translateY(0)" }], { duration: 450, easing: "cubic-bezier(.2,.8,.2,1)" });
      }
      last.current.set(id, y);
    });
  });
  return (
    <ol className="space-y-2">
      {event.leaderboard.map((r) => (
        <li key={r.user_id} ref={(el) => { if (el) rows.current.set(r.user_id, el); else rows.current.delete(r.user_id); }}
            className={cn("rounded-2xl px-3 py-2.5", r.winner ? "bg-wait-soft" : "bg-muted/50", r.out && "opacity-50")}>
          <div className="flex items-center gap-3">
            <span className={cn("grid size-7 shrink-0 place-items-center rounded-lg font-display text-sm font-semibold",
              r.place === 1 ? "bg-primary text-primary-foreground" : "bg-card")}>{r.place}</span>
            <span className="min-w-0 flex-1 truncate font-medium">{r.winner && <Crown className="mr-1 inline size-4 text-wait" />}{r.name}</span>
            <span className="font-display font-semibold tabular">{r.score}</span>
          </div>
          <div className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-card">
            <span className="h-full bg-primary transition-all duration-500" style={{ width: `${(r.confirmed / top) * 100}%` }} />
            <span className="h-full bg-primary/30 transition-all duration-500" style={{ width: `${(Math.max(0, r.score - r.confirmed) / top) * 100}%` }} />
          </div>
        </li>
      ))}
    </ol>
  );
}

function EntryForm({ event, onDone }: { event: LiveEvent; onDone: () => void }) {
  const [value, setValue] = useState(event.mode === "first_to_reach" ? String(event.target ?? "") : "");
  const [photo, setPhoto] = useState<Blob | null>(null);
  const { busy, run } = useAction();
  const n = Number(value);
  return (
    <div className="space-y-3 rounded-2xl bg-muted/60 p-4">
      <p className="font-semibold">{event.mode === "first_to_reach" ? "Дошли до цели? Отправьте результат" : "Добавить результат"}</p>
      <input inputMode="numeric" aria-label="Сколько штук" placeholder="Сколько штук" value={value}
             onChange={(e) => setValue(e.target.value.replace(/\D/g, "").slice(0, 7))}
             className="h-12 w-full rounded-2xl border-0 bg-card px-4 font-display text-xl font-semibold tabular outline-none placeholder:font-sans placeholder:text-[15px] placeholder:font-normal focus:ring-2 focus:ring-ring/25" />
      <PhotoPicker value={photo} onChange={setPhoto} />
      <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy || !n || !photo}
              onClick={async () => {
                try {
                  const res = await run("entry", (k) => api.submitEntry(event.id, n, photo!, k), "Заявка отправлена на проверку");
                  if (res) { setPhoto(null); onDone(); }
                } catch {
                  /* toast shown */
                }
              }}>
        {busy && <LoaderCircle className="animate-spin" />} Отправить
      </Button>
    </div>
  );
}

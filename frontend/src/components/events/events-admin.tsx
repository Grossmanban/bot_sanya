"use client";

import { useEffect, useState } from "react";
import { Check, LoaderCircle, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type LiveEvent, type PendingEntry, type User } from "@/lib/api";
import { useCan } from "@/lib/caps";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { PhotoThumb } from "../report/photo";
import { Leaderboard, RULE } from "./live";

const field = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

/** Live events (ТЗ 8.2): create, review entries (👑 / reason), watch the board. Polls while open. */
export function EventsAdmin({ me }: { me: User }) {
  const everyone = useCan("raspred");
  const { data, reload } = useLoad(() => api.admin.events(), []);
  useEffect(() => {
    const t = setInterval(reload, 7000);
    return () => clearInterval(t);
  }, [reload]);

  return (
    <div className="space-y-4">
      <CreateEvent everyone={everyone} myNetwork={me.network_id} onDone={reload} />
      {!data ? <Skeleton className="h-40 rounded-3xl" /> : (
        <>
          {data.pending.length > 0 && (
            <section className="overflow-hidden rounded-3xl bg-card shadow-lift ring-1 ring-wait/30">
              <h2 className="px-5 pt-5 pb-2 font-semibold md:px-6">Заявки на проверку <span className="text-muted-foreground">{data.pending.length}</span></h2>
              <ul className="divide-y">{data.pending.map((p) => <EntryRow key={p.id} entry={p} onDone={reload} />)}</ul>
            </section>
          )}
          {data.items.length === 0 ? (
            <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">Событий пока не было.</p>
          ) : data.items.map((e) => <EventCard key={e.id} event={e} onDone={reload} />)}
        </>
      )}
    </div>
  );
}

function EntryRow({ entry, onDone }: { entry: PendingEntry; onDone: () => void }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const { busy, run } = useAction();
  const decide = async (approve: boolean) => {
    try {
      await run(approve ? "ok" : "no", (k) => api.admin.reviewEntry(entry.id, approve, approve ? null : reason.trim(), k),
        approve ? (entry.mode === "first_to_reach" ? "👑 Победитель подтверждён" : "Засчитано") : "Отклонено");
      onDone();
    } catch {
      /* toast shown */
    }
  };
  return (
    <li className="space-y-3 px-5 py-4 md:px-6">
      <div className="flex items-start gap-3">
        <PhotoThumb src={entry.photo_url} className="size-16" />
        <div className="min-w-0 flex-1">
          <p className="font-semibold">{entry.name}</p>
          <p className="text-sm text-muted-foreground">{entry.event} · попытка {entry.attempt}</p>
        </div>
        <span className="font-display text-xl font-semibold tabular">{entry.value}</span>
      </div>
      {rejecting && (
        <input autoFocus className={field} placeholder="Причина — участник её увидит" value={reason} onChange={(e) => setReason(e.target.value.slice(0, 300))} />
      )}
      <div className="flex gap-2">
        <Button className="press h-10 flex-1 rounded-xl bg-go font-semibold text-white hover:bg-go/90" disabled={!!busy} onClick={() => decide(true)}>
          {busy === "ok" ? <LoaderCircle className="animate-spin" /> : <Check />} Подтвердить
        </Button>
        <Button variant="secondary" className="press h-10 flex-1 rounded-xl font-semibold" disabled={!!busy || (rejecting && !reason.trim())}
                onClick={() => (rejecting ? decide(false) : setRejecting(true))}>
          {busy === "no" ? <LoaderCircle className="animate-spin" /> : <X />} Отклонить
        </Button>
      </div>
    </li>
  );
}

function EventCard({ event, onDone }: { event: LiveEvent; onDone: () => void }) {
  const { busy, run } = useAction();
  return (
    <section className="row-in space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-lg font-semibold">{event.title}</h3>
          <p className="text-sm text-muted-foreground">
            {RULE(event)}{event.network ? ` · ${event.network}` : ""} · до {event.ends_at.slice(11, 16)} {event.ends_at.slice(8, 10)}.{event.ends_at.slice(5, 7)}
          </p>
          <p className={cn("mt-1 text-xs font-semibold", event.phase === "live" ? "text-go" : "text-muted-foreground")}>
            {{ upcoming: "скоро старт", live: "идёт", review: "проверка последних заявок", finished: "завершено", cancelled: "отменено" }[event.phase]}
            {event.prize ? ` · приз: ${event.prize}` : ""}
          </p>
        </div>
        {event.status === "active" && (
          <Button variant="ghost" size="sm" className="h-8 shrink-0 rounded-lg text-muted-foreground" disabled={!!busy}
                  onClick={async () => { try { await run("cancel", (k) => api.admin.cancelEvent(event.id, k), "Событие отменено"); onDone(); } catch { /* toast */ } }}>
            Отменить
          </Button>
        )}
      </div>
      {event.leaderboard.length > 0 ? <Leaderboard event={event} /> : <p className="text-sm text-muted-foreground">Заявок пока нет.</p>}
    </section>
  );
}

function CreateEvent({ everyone, myNetwork, onDone }: { everyone: boolean; myNetwork: number | null; onDone: () => void }) {
  const nets = useLoad(() => api.admin.networks(), []);
  const today = moscowToday();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<LiveEvent["mode"]>("accumulative");
  const [title, setTitle] = useState("");
  const [prize, setPrize] = useState("");
  const [description, setDescription] = useState("");
  const [target, setTarget] = useState("");
  const [winners, setWinners] = useState("3");
  const [attempts, setAttempts] = useState("3");
  const [starts, setStarts] = useState(`${today}T10:00`);
  const [ends, setEnds] = useState(`${today}T20:00`);
  const [network, setNetwork] = useState<number | null>(everyone ? null : myNetwork);
  const { busy, run } = useAction();

  async function create() {
    try {
      const res = await run("create", (k) => api.admin.createEvent({
        title: title.trim(), mode, starts_at: starts, ends_at: ends, prize: prize.trim() || undefined,
        description: description.trim() || undefined, target: mode === "first_to_reach" ? Number(target) : null,
        max_winners: Number(winners) || 1, max_attempts: Number(attempts) || 3, network_id: network,
      }, k));
      if (res) {
        toast.success("Событие запущено, участники получили уведомление");
        setOpen(false);
        setTitle("");
        onDone();
      }
    } catch {
      /* toast shown */
    }
  }

  return (
    <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <button className="flex w-full items-center gap-3 text-left" onClick={() => setOpen((v) => !v)}>
        <span className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground"><Plus className="size-5" /></span>
        <span className="flex-1">
          <span className="block font-semibold">Новое событие</span>
          <span className="block text-sm text-muted-foreground">Соревнование с таймером, призом и живым рейтингом</span>
        </span>
      </button>
      {open && (
        <div className="row-in mt-4 space-y-3">
          <div className="grid grid-cols-2 gap-1 rounded-2xl bg-muted p-1">
            {(["accumulative", "first_to_reach"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)} className={cn("press h-10 rounded-xl text-sm font-semibold", mode === m ? "bg-card shadow-lift" : "text-muted-foreground")}>
                {m === "accumulative" ? "Кто больше" : "Гонка до цели"}
              </button>
            ))}
          </div>
          <input className={field} placeholder="Название, например «Пятничный спринт»" value={title} onChange={(e) => setTitle(e.target.value.slice(0, 80))} />
          <input className={field} placeholder="Приз" value={prize} onChange={(e) => setPrize(e.target.value.slice(0, 200))} />
          <div className="grid grid-cols-3 gap-2">
            {mode === "first_to_reach" && (
              <label className="space-y-1"><span className="text-xs text-muted-foreground">Цель, шт.</span>
                <input inputMode="numeric" className={field} value={target} onChange={(e) => setTarget(e.target.value.replace(/\D/g, "").slice(0, 6))} /></label>
            )}
            <label className="space-y-1"><span className="text-xs text-muted-foreground">Победителей</span>
              <input inputMode="numeric" className={field} value={winners} onChange={(e) => setWinners(e.target.value.replace(/\D/g, "").slice(0, 3))} /></label>
            <label className="space-y-1"><span className="text-xs text-muted-foreground">Попыток</span>
              <input inputMode="numeric" className={field} value={attempts} onChange={(e) => setAttempts(e.target.value.replace(/\D/g, "").slice(0, 3))} /></label>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="space-y-1"><span className="text-xs text-muted-foreground">Старт</span>
              <input type="datetime-local" className={field} value={starts} onChange={(e) => setStarts(e.target.value)} /></label>
            <label className="space-y-1"><span className="text-xs text-muted-foreground">Финиш</span>
              <input type="datetime-local" className={field} value={ends} onChange={(e) => setEnds(e.target.value)} /></label>
          </div>
          {everyone && (nets.data?.items.length ?? 0) > 0 && (
            <select className={field} value={network ?? ""} onChange={(e) => setNetwork(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Для всех</option>
              {nets.data!.items.map((n) => <option key={n.id} value={n.id}>Сеть «{n.name}»</option>)}
            </select>
          )}
          <textarea rows={2} className="w-full resize-none rounded-xl border-0 bg-muted px-3 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-ring/25"
                    placeholder="Правила (необязательно)" value={description} onChange={(e) => setDescription(e.target.value.slice(0, 500))} />
          <p className="text-xs text-muted-foreground">После финиша ещё 15 минут на проверку последних заявок, затем итоги уходят всем участникам.</p>
          <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold"
                  disabled={!!busy || !title.trim() || (mode === "first_to_reach" && !Number(target))} onClick={create}>
            {busy && <LoaderCircle className="animate-spin" />} Запустить
          </Button>
        </div>
      )}
    </section>
  );
}

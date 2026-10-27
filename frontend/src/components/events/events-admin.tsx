"use client";

import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle, Plus, RotateCcw, Undo2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, PRODUCT_TITLES, type EventChanges, type LiveEvent, type PendingEntry, type Product, type User } from "@/lib/api";
import { useCan } from "@/lib/caps";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import {
  ANNUL_PRESETS, DeleteButton, EditButton, NotifyDialog, PrizeFields, PrizeThumb, ReasonInput, VisibilityButton,
  prizeMediaValue, prizeState, prizeText, prizeTouched,
} from "../lifecycle";
import { RichEditor, type RichEditorHandle } from "../admin/rich-editor";
import { PhotoThumb } from "../report/photo";
import { Leaderboard, RULE } from "./live";

const field = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

/** Live events (ТЗ 8.2): create, review entries (👑 / reason), watch the board. Polls while open. */
export function EventsAdmin({ me }: { me: User }) {
  const everyone = useCan("all_people");
  const { data, reload } = useLoad(() => api.admin.events(), []);
  useEffect(() => {
    const t = setInterval(reload, 7000);
    return () => clearInterval(t);
  }, [reload]);

  return (
    <div className="space-y-4">
      <CreateEvent everyone={everyone} myNetwork={me.network_id} types={data?.prize_types ?? []} onDone={reload} />
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
          ) : data.items.map((e) => <EventCard key={e.id} event={e} types={data.prize_types} days={data.hide_after_days} onDone={reload} />)}
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

const FEED: Record<string, string> = { hidden: "скрыто из ленты", shown: "закреплено в ленте" };

function feedNote(e: LiveEvent, days: number) {
  if (e.visibility !== "auto") return FEED[e.visibility];
  if (e.in_feed === false) return "в архиве";
  return e.status === "active" ? null : `уйдёт в архив через ${days} дн. после финиша`;
}

function EventCard({ event, types, days, onDone }: { event: LiveEvent; types: string[]; days: number; onDone: () => void }) {
  const { busy, run } = useAction();
  const [editing, setEditing] = useState<"edit" | "restart" | null>(null);
  const names = new Map(event.leaderboard.map((r) => [r.user_id, r.name]));
  const note = feedNote(event, days);
  const prize = prizeText(event);
  return (
    <section className={cn("row-in space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6", event.visibility === "hidden" && "opacity-80")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-lg font-semibold">{event.title}</h3>
          <p className="text-sm text-muted-foreground">
            {RULE(event)}{event.network ? ` · ${event.network}` : ""} · до {event.ends_at.slice(11, 16)} {event.ends_at.slice(8, 10)}.{event.ends_at.slice(5, 7)}
          </p>
          <p className={cn("mt-1 text-xs font-semibold", event.phase === "live" ? "text-go" : "text-muted-foreground")}>
            {{ upcoming: "скоро старт", live: "идёт", review: "проверка последних заявок", finished: "завершено", cancelled: "отменено" }[event.phase]}
            {prize ? ` · приз: ${prize}` : ""}
            {note && <span className="text-wait"> · {note}</span>}
          </p>
        </div>
        <PrizeThumb url={event.prize_photo_url} kind={event.prize_media_kind} className="size-14" />
      </div>
      {event.can_manage && (
        <div className="-mx-2 flex flex-wrap items-center gap-0.5">
          {event.status !== "cancelled" && <EditButton onClick={() => setEditing(editing ? null : "edit")} />}
          <VisibilityButton item={event} noun="событие" onToggle={async (hidden, k) => { await api.admin.eventVisibility(event.id, hidden, k); onDone(); }} />
          <DeleteButton noun="событие" onDelete={async (k) => { await api.admin.deleteEvent(event.id, k); onDone(); }} />
          <span className="flex-1" />
          {event.status === "finished" && (
            <Button variant="ghost" size="sm" className="h-8 rounded-lg text-muted-foreground" onClick={() => setEditing("restart")}>
              <RotateCcw /> Перезапустить
            </Button>
          )}
          {event.status === "active" && (
            <Button variant="ghost" size="sm" className="h-8 shrink-0 rounded-lg text-muted-foreground" disabled={!!busy}
                    onClick={async () => { try { await run("cancel", (k) => api.admin.cancelEvent(event.id, k), "Событие отменено"); onDone(); } catch { /* toast */ } }}>
              Отменить
            </Button>
          )}
        </div>
      )}
      {editing && (
        <div className="row-in rounded-2xl bg-muted/50 p-4">
          {editing === "restart" && (
            <p className="mb-3 text-sm text-muted-foreground">
              Поставьте новый финиш позже текущего времени — событие снова примет заявки, результаты сохранятся.
              {event.mode === "first_to_reach" && event.winners_count >= event.max_winners ? " Все победители уже есть — увеличьте их число." : ""}
            </p>
          )}
          <EventForm initial={event} types={types} everyone={false} myNetwork={null}
                     onDone={() => { setEditing(null); onDone(); }} onCancel={() => setEditing(null)} />
        </div>
      )}
      {event.leaderboard.length > 0 ? <Leaderboard event={event} /> : <p className="text-sm text-muted-foreground">Заявок пока нет.</p>}
      {event.can_manage && (event.entries?.length ?? 0) > 0 && (
        <details className="group rounded-2xl bg-muted/40 px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium">Подтверждённые результаты <span className="text-muted-foreground">{event.entries!.length}</span></summary>
          <ul className="mt-2 divide-y">
            {event.entries!.map((x) => <ConfirmedEntry key={x.id} id={x.id} name={names.get(x.user_id) ?? "Участник"} value={x.value} onDone={onDone} />)}
          </ul>
        </details>
      )}
    </section>
  );
}

/** ТЗ №20: take a confirmed result back — conditions changed or the work was dishonest. */
function ConfirmedEntry({ id, name, value, onDone }: { id: number; name: string; value: number; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const { busy, run } = useAction();
  return (
    <li className="space-y-2 py-2.5">
      <div className="flex items-center gap-3 text-sm">
        <span className="min-w-0 flex-1 truncate">{name}</span>
        <span className="font-semibold tabular">{value} шт.</span>
        <Button variant="ghost" size="sm" className="h-8 rounded-lg text-muted-foreground" onClick={() => setOpen((v) => !v)}>
          <Undo2 /> Отменить
        </Button>
      </div>
      {open && (
        <div className="row-in space-y-2">
          <ReasonInput value={reason} onChange={setReason} presets={ANNUL_PRESETS} />
          <Button className="press h-10 w-full rounded-xl" disabled={!!busy || !reason.trim()}
                  onClick={async () => {
                    try {
                      const res = await run("annul", (k) => api.admin.annulEntry(id, reason.trim(), k));
                      if (res) {
                        toast.success(res.reopened ? "Результат отменён, гонка продолжается" : "Результат отменён, участник получил уведомление");
                        onDone();
                      }
                    } catch {
                      /* toast shown */
                    }
                  }}>
            {busy && <LoaderCircle className="animate-spin" />} Отменить результат
          </Button>
        </div>
      )}
    </li>
  );
}

function CreateEvent({ everyone, myNetwork, types, onDone }: { everyone: boolean; myNetwork: number | null; types: string[]; onDone: () => void }) {
  const [open, setOpen] = useState(false);
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
        <div className="row-in mt-4">
          <EventForm types={types} everyone={everyone} myNetwork={myNetwork} onDone={() => { setOpen(false); onDone(); }} />
        </div>
      )}
    </section>
  );
}

const local = (iso: string) => iso.slice(0, 16);
const CRITICAL: (keyof EventChanges)[] = ["starts_at", "ends_at", "target", "max_winners", "max_attempts", "prize", "prize_type"];

/** One form to create and to edit (ТЗ №20: the same form, filled in). */
function EventForm({ initial, types, everyone, myNetwork, onDone, onCancel }: {
  initial?: LiveEvent; types: string[]; everyone: boolean; myNetwork: number | null; onDone: () => void; onCancel?: () => void;
}) {
  const nets = useLoad(() => (everyone && !initial ? api.admin.networks() : Promise.resolve({ items: [] })), [everyone, initial]);
  const today = moscowToday();
  const editing = !!initial;
  const [mode, setMode] = useState<LiveEvent["mode"]>(initial?.mode ?? "accumulative");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [prize, setPrize] = useState(prizeState(initial));
  // the description with formatting: what the editor holds, and what it held when opened (to tell a real change)
  const editor = useRef<RichEditorHandle>(null);
  const [description, setDescription] = useState("");
  const [descLength, setDescLength] = useState(0);
  const baseline = useRef<string | null>(null);
  const [target, setTarget] = useState(initial?.target ? String(initial.target) : "");
  const [winners, setWinners] = useState(String(initial?.max_winners ?? 3));
  const [attempts, setAttempts] = useState(String(initial?.max_attempts ?? 3));
  const [starts, setStarts] = useState(initial ? local(initial.starts_at) : `${today}T10:00`);
  const [ends, setEnds] = useState(initial ? local(initial.ends_at) : `${today}T20:00`);
  const [network, setNetwork] = useState<number | null>(everyone ? null : myNetwork);
  const [product, setProduct] = useState<Product | "">("");  // ТЗ 27.10 §29: only those with access take part
  const [asking, setAsking] = useState<string[] | null>(null);
  const { busy, run } = useAction();
  const started = !!initial && initial.phase !== "upcoming";
  const raceLocked = !!initial && initial.mode === "first_to_reach" && initial.winners_count > 0;

  useEffect(() => {
    if (initial?.description_html) editor.current?.setHtml(initial.description_html);
    else baseline.current = "";
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the form opens
  }, []);
  const onDescription = (length: number) => {
    const html = editor.current?.getHtml().html ?? "";
    if (baseline.current === null) baseline.current = html;
    setDescription(html);
    setDescLength(length);
  };

  function changes(): EventChanges {
    const next: EventChanges = {
      title: title.trim(), prize: prize.prize.trim() || null,
      prize_type: prize.type.trim() || null, starts_at: starts, ends_at: ends,
      max_winners: Number(winners) || 1, max_attempts: Number(attempts) || 3,
      ...(mode === "first_to_reach" ? { target: Number(target) } : {}),
    };
    if (!initial) return { ...next, description: description || null };
    const was: Record<string, unknown> = { ...initial, starts_at: local(initial.starts_at), ends_at: local(initial.ends_at) };
    const changed = Object.fromEntries(Object.entries(next).filter(([k, v]) => (was[k] ?? null) !== v)) as EventChanges;
    return description !== (baseline.current ?? "") ? { ...changed, description: description || null } : changed;
  }

  async function save(notify: boolean) {
    setAsking(null);
    try {
      const res = await run("save", async (k) => {
        const media = await prizeMediaValue(prize);
        const body = { ...changes(), ...media };
        if (!initial) {
          return api.admin.createEvent({
            title: body.title!, mode, starts_at: starts, ends_at: ends, prize: body.prize ?? undefined, prize_type: body.prize_type,
            prize_photo: media.prize_photo ?? null, prize_media_kind: media.prize_media_kind, description: body.description ?? undefined,
            target: mode === "first_to_reach" ? Number(target) : null, max_winners: body.max_winners!, max_attempts: body.max_attempts!,
            network_id: network, product: product || null,
          }, k);
        }
        return api.admin.updateEvent(initial.id, { ...body, version: initial.version, notify }, k);
      });
      if (!res) return;
      if (!initial) toast.success("Событие запущено, участники получили уведомление");
      else if ("restarted" in res && res.restarted) toast.success("Событие перезапущено");
      else if ("finished" in res && res.finished) toast.success("Финиш уже прошёл — событие завершено, итоги разосланы");
      else toast.success(notify ? "Сохранено, участники уведомлены" : "Сохранено");
      onDone();
    } catch {
      /* toast shown; a stale version asks to reopen */
    }
  }

  function submit() {
    const critical = initial ? CRITICAL.filter((k) => k in changes()) : [];
    if (critical.length) setAsking(critical);
    else void save(false);
  }

  const nothing = editing && Object.keys(changes()).length === 0 && !prizeTouched(prize);
  const tooLong = descLength > 1000;
  return (
    <div className="space-y-3">
      {!editing && (
        <div className="grid grid-cols-2 gap-1 rounded-2xl bg-muted p-1">
          {(["accumulative", "first_to_reach"] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)} className={cn("press h-10 rounded-xl text-sm font-semibold", mode === m ? "bg-card shadow-lift" : "text-muted-foreground")}>
              {m === "accumulative" ? "Кто больше" : "Гонка до цели"}
            </button>
          ))}
        </div>
      )}
      <input className={field} placeholder="Название, например «Пятничный спринт»" value={title} onChange={(e) => setTitle(e.target.value.slice(0, 80))} />
      <div className="grid grid-cols-3 gap-2">
        {mode === "first_to_reach" && (
          <label className="space-y-1"><span className="text-xs text-muted-foreground">Цель, шт.</span>
            <input inputMode="numeric" className={cn(field, raceLocked && "opacity-60")} disabled={raceLocked} value={target}
                   onChange={(e) => setTarget(e.target.value.replace(/\D/g, "").slice(0, 6))} /></label>
        )}
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Победителей</span>
          <input inputMode="numeric" className={field} value={winners} onChange={(e) => setWinners(e.target.value.replace(/\D/g, "").slice(0, 3))} /></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Попыток</span>
          <input inputMode="numeric" className={field} value={attempts} onChange={(e) => setAttempts(e.target.value.replace(/\D/g, "").slice(0, 3))} /></label>
      </div>
      {raceLocked && <p className="text-xs text-muted-foreground">У гонки уже есть победители — цель не меняется. Чтобы изменить, сначала отмените их результат.</p>}
      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Старт</span>
          <input type="datetime-local" className={cn(field, started && "opacity-60")} disabled={started} value={starts} onChange={(e) => setStarts(e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Финиш</span>
          <input type="datetime-local" className={field} value={ends} onChange={(e) => setEnds(e.target.value)} /></label>
      </div>
      {started && <p className="text-xs text-muted-foreground">Событие уже началось — старт не меняется. Финиш раньше текущего времени завершит его сразу.</p>}
      {everyone && !editing && (nets.data?.items.length ?? 0) > 0 && (
        <select className={field} value={network ?? ""} onChange={(e) => setNetwork(e.target.value ? Number(e.target.value) : null)}>
          <option value="">Для всех</option>
          {nets.data!.items.map((n) => <option key={n.id} value={n.id}>Сеть «{n.name}»</option>)}
        </select>
      )}
      {!editing && (
        <select aria-label="Продукт" className={field} value={product} onChange={(e) => setProduct(e.target.value as Product | "")}>
          <option value="">Для всех продуктов</option>
          {(Object.keys(PRODUCT_TITLES) as Product[]).map((p) => (
            <option key={p} value={p}>Только с доступом к «{PRODUCT_TITLES[p]}»</option>
          ))}
        </select>
      )}
      <PrizeFields types={types} value={prize} onChange={setPrize} currentUrl={initial?.prize_photo_url}
                   currentKind={initial?.prize_media_kind} allowVideo />
      <div className="space-y-1">
        <p className="text-xs text-muted-foreground">Правила и описание — можно выделить жирным, курсивом, добавить ссылку</p>
        <RichEditor ref={editor} label="Правила события" compact
                    placeholder="Например: первый, кто сделает 30 отзывов и загрузит скриншот, получает приз" onChange={onDescription} />
        {descLength > 800 && <p className={cn("text-right text-xs", tooLong ? "text-cancel" : "text-muted-foreground")}>{descLength} / 1000</p>}
      </div>
      {!editing && <p className="text-xs text-muted-foreground">После финиша ещё 15 минут на проверку последних заявок, затем итоги уходят всем участникам.</p>}
      <div className="flex gap-2">
        {onCancel && <Button variant="secondary" className="press h-12 rounded-2xl px-5" onClick={onCancel}>Отмена</Button>}
        <Button className="press h-12 flex-1 rounded-2xl text-[15px] font-semibold"
                disabled={!!busy || !title.trim() || (mode === "first_to_reach" && !Number(target)) || nothing || tooLong} onClick={submit}>
          {busy && <LoaderCircle className="animate-spin" />} {editing ? "Сохранить" : "Запустить"}
        </Button>
      </div>
      <NotifyDialog fields={asking} onChoose={save} onCancel={() => setAsking(null)} />
    </div>
  );
}

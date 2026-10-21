"use client";

import { useState } from "react";
import { Ban, CircleCheck, LoaderCircle, Plus, Target, TrendingUp, Users } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Goal, type GoalChanges, type User } from "@/lib/api";
import { useCan } from "@/lib/caps";
import { addDays, dayMonth, plural } from "@/lib/format";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import {
  ANNUL_PRESETS, DeleteButton, EditButton, NotifyDialog, PrizeFields, PrizeThumb, ReasonInput, VisibilityButton,
  prizePhotoValue, prizeState, prizeText,
} from "../lifecycle";

const KIND: Record<Goal["kind"], string> = { personal: "Личная", team: "Командная", network: "Сеть" };

function Progress({ value, target, done }: { value: number; target: number; done: boolean }) {
  const pct = Math.min(100, Math.round((value / Math.max(target, 1)) * 100));
  return (
    <div className="space-y-1.5">
      <div className="relative h-2.5 overflow-hidden rounded-full bg-muted">
        <span className={cn("bar-draw absolute inset-y-0 left-0 rounded-full", done ? "bg-go" : "bg-primary")} style={{ width: `${pct}%` }} />
      </div>
      <div className="flex justify-between text-xs tabular">
        <span className={done ? "font-semibold text-go" : "font-semibold"}>{value} из {target} шт.</span>
        <span className="text-muted-foreground">{pct}%</span>
      </div>
    </div>
  );
}

/** ТЗ №18: where the numbers come from — earlier approved reports, new ones, and what is still being checked. */
function Breakdown({ g }: { g: Goal }) {
  const before = g.backfill ?? 0;
  const pending = g.pending ?? 0;
  if (!before && !pending) return null;
  return (
    <p className="text-xs text-muted-foreground">
      {before > 0 && <>Учтено автоматически за дни до создания цели: <b className="text-foreground">{before} шт.</b></>}
      {before > 0 && pending > 0 && " · "}
      {pending > 0 && <>на проверке: <b className="text-wait">{pending} шт.</b> — добавятся после принятия</>}
    </p>
  );
}

/** The employee's goals with «Участвую» (ТЗ 6.2); shown on the schedule screen when there are any. */
export function MyGoals() {
  const { data, reload } = useLoad(() => api.goals(), []);
  const { busy, run } = useAction();
  const items = data?.items ?? [];
  if (!items.length) return null;
  return (
    <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
      <h2 className="flex items-center gap-2 px-5 pt-5 pb-1 font-semibold md:px-6"><Target className="size-4" /> Цели</h2>
      <ul className="divide-y">
        {items.map((g) => (
          <li key={g.id} className="row-in space-y-2.5 px-5 py-4 md:px-6">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="font-semibold">{g.done && <CircleCheck className="mr-1 inline size-4 text-go" />}{g.title}</p>
                <p className="text-xs text-muted-foreground">
                  {KIND[g.kind]}{g.network ? ` «${g.network}»` : ""} · до {dayMonth(g.date_to)}
                  {g.mode === "shared" && g.kind !== "personal" ? ` · общая норма, ${g.participants.length} ${plural(g.participants.length, "участник", "участника", "участников")}` : ""}
                </p>
                {prizeText(g) && <p className="mt-1 text-sm">🏆 {prizeText(g)}</p>}
              </div>
              {(g.prize || g.prize_type || g.prize_photo_url) && <PrizeThumb url={g.prize_photo_url} className="size-14" />}
              {!g.joined && !g.my_annulled && g.status === "active" && (
                <Button size="sm" className="press h-9 shrink-0 rounded-xl" disabled={!!busy}
                        onClick={async () => { try { await run(`j${g.id}`, () => api.joinGoal(g.id), "Вы участвуете"); await reload(); } catch { /* toast */ } }}>
                  {busy === `j${g.id}` ? <LoaderCircle className="animate-spin" /> : null} Участвую
                </Button>
              )}
            </div>
            {g.description && <p className="text-sm text-muted-foreground">{g.description}</p>}
            {g.my_annulled && (
              <p className="flex items-start gap-2 rounded-2xl bg-cancel-soft p-3 text-sm"><Ban className="mt-0.5 size-4 shrink-0 text-cancel" />
                <span>Ваш результат в этой цели аннулирован. Причина: {g.my_annulled}</span></p>
            )}
            {(g.joined || g.mode === "shared") && <Progress value={g.progress} target={g.target} done={g.done} />}
            {(g.joined || g.mode === "shared") && <Breakdown g={g} />}
            {g.joined && g.mode === "shared" && g.kind !== "personal" && (
              <p className="text-xs text-muted-foreground">Ваш вклад: {g.my_volume} шт.</p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

const field = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

/** Admin: create goals and watch progress (ТЗ 6.3). Trainers: personal goals for their people and their network.
 *  ТЗ №20/№22: edit a running goal, hide it from the feed, delete it, take «выполнено» back, annul a result. */
export function GoalsSection({ me }: { me: User }) {
  const everyone = useCan("all_people");
  const { data, reload } = useLoad(() => api.admin.goals(), []);
  const [open, setOpen] = useState(false);
  const types = data?.prize_types ?? [];

  return (
    <div className="space-y-4">
      <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <button className="flex w-full items-center gap-3 text-left" onClick={() => setOpen((v) => !v)}>
          <span className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground"><Plus className="size-5" /></span>
          <span className="flex-1">
            <span className="block font-semibold">Новая цель</span>
            <span className="block text-sm text-muted-foreground">Прогресс считается по принятым отчётам за период</span>
          </span>
        </button>
        {open && (
          <div className="row-in mt-4">
            <GoalForm me={me} everyone={everyone} types={types} onDone={async () => { setOpen(false); await reload(); }} />
          </div>
        )}
      </section>

      {!data ? <Skeleton className="h-40 rounded-3xl" /> : data.items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">Целей пока нет.</p>
      ) : (
        <ul className="space-y-3">
          {data.items.map((g) => <GoalCard key={g.id} goal={g} me={me} types={types} days={data.hide_after_days} onDone={reload} />)}
        </ul>
      )}
    </div>
  );
}

function feedNote(g: Goal, days: number) {
  if (g.visibility === "hidden") return "скрыта из ленты";
  if (g.visibility === "shown") return "закреплена в ленте";
  if (g.in_feed === false) return "в архиве";
  return g.date_to < moscowToday() ? `уйдёт в архив через ${days} дн. после окончания` : null;
}

function GoalCard({ goal: g, me, types, days, onDone }: { goal: Goal; me: User; types: string[]; days: number; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [annulFor, setAnnulFor] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const { busy, run } = useAction();
  const note = feedNote(g, days);
  const prize = prizeText(g);
  return (
    <li className={cn("row-in space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6", g.visibility === "hidden" && "opacity-80")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold">{g.status === "completed" && <CircleCheck className="mr-1 inline size-4 text-go" />}{g.title}</p>
          <p className="text-xs text-muted-foreground">
            {KIND[g.kind]}{g.network ? ` «${g.network}»` : ""}{g.person ? ` · ${g.person}` : ""} · {dayMonth(g.date_from)} – {dayMonth(g.date_to)}
            {g.kind !== "personal" ? ` · ${g.mode === "shared" ? "общая норма" : "норма каждому"}` : ""}
          </p>
          {(prize || note) && (
            <p className="mt-1 text-xs font-semibold text-muted-foreground">
              {prize && `🏆 ${prize}`}{prize && note ? " · " : ""}{note && <span className="text-wait">{note}</span>}
            </p>
          )}
        </div>
        {(g.prize || g.prize_type || g.prize_photo_url) && <PrizeThumb url={g.prize_photo_url} className="size-14" />}
      </div>
      {g.can_manage && (
        <div className="-mx-2 flex items-center gap-0.5">
          <EditButton onClick={() => setEditing((v) => !v)} />
          <VisibilityButton item={g} noun="цель" onToggle={async (hidden, k) => { await api.admin.goalVisibility(g.id, hidden, k); onDone(); }} />
          <DeleteButton noun="цель" onDelete={async (k) => { await api.admin.deleteGoal(g.id, k); onDone(); }} />
        </div>
      )}
      {editing && (
        <div className="row-in rounded-2xl bg-muted/50 p-4">
          <GoalForm me={me} everyone={false} types={types} initial={g} onDone={() => { setEditing(false); onDone(); }} onCancel={() => setEditing(false)} />
        </div>
      )}
      {g.can_manage && g.revocable > 0 && (
        <div className="row-in flex flex-wrap items-center gap-3 rounded-2xl bg-wait-soft p-3 text-sm">
          <TrendingUp className="size-4 shrink-0 text-wait" />
          <span className="min-w-0 flex-1">
            Норму подняли: у {g.revocable} {plural(g.revocable, "участника", "участников", "участников")} отметка «выполнено», а результат ниже новой нормы.
          </span>
          <Button size="sm" variant="secondary" className="h-9 rounded-xl" disabled={!!busy}
                  onClick={async () => {
                    try {
                      const res = await run("revoke", (k) => api.admin.revokeGoal(g.id, k));
                      if (res) { toast.success(`Выполнение снято, ${res.revoked} ${plural(res.revoked, "человек получил", "человека получили", "человек получили")} уведомление`); onDone(); }
                    } catch { /* toast */ }
                  }}>
            {busy === "revoke" && <LoaderCircle className="animate-spin" />} Снять выполнение
          </Button>
        </div>
      )}
      {g.mode === "shared" ? <Progress value={g.total} target={g.target} done={g.status === "completed"} /> : null}
      <Breakdown g={g} />
      {g.participants.length > 0 ? (
        <ul className="space-y-1.5">
          {[...g.participants].sort((a, b) => Number(a.annulled) - Number(b.annulled) || b.volume - a.volume).map((p) => (
            <li key={p.user_id} className="space-y-2">
              <div className={cn("flex items-center gap-3 text-sm", p.annulled && "text-muted-foreground")}>
                <span className="min-w-0 flex-1 truncate">
                  {p.done && <CircleCheck className="mr-1 inline size-3.5 text-go" />}
                  <span className={cn(p.annulled && "line-through")}>{p.name}</span>
                  {p.annulled && <span className="ml-1 text-xs">· аннулировано: {p.annul_reason}</span>}
                </span>
                {g.mode === "each" && !p.annulled && (
                  <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                    <span className="block h-full rounded-full bg-primary" style={{ width: `${Math.min(100, (p.volume / g.target) * 100)}%` }} />
                  </span>
                )}
                <span className="w-14 text-right font-semibold tabular">{p.volume}</span>
                {g.can_manage && !p.annulled && (
                  <button aria-label="Аннулировать результат" title="Аннулировать результат"
                          className="press grid size-8 place-items-center rounded-lg text-muted-foreground hover:text-cancel"
                          onClick={() => { setAnnulFor(annulFor === p.user_id ? null : p.user_id); setReason(""); }}>
                    <Ban className="size-4" />
                  </button>
                )}
              </div>
              {annulFor === p.user_id && (
                <div className="row-in space-y-2 rounded-2xl bg-muted/50 p-3">
                  <p className="text-sm">Аннулировать результат: объём человека перестанет учитываться в этой цели.</p>
                  <ReasonInput value={reason} onChange={setReason} presets={ANNUL_PRESETS.slice(0, 2)} />
                  <Button className="press h-10 w-full rounded-xl" disabled={!!busy || !reason.trim()}
                          onClick={async () => {
                            try {
                              const res = await run("annul", (k) => api.admin.annulGoal(g.id, p.user_id, reason.trim(), k), "Результат аннулирован");
                              if (res) { setAnnulFor(null); onDone(); }
                            } catch { /* toast */ }
                          }}>
                    {busy === "annul" && <LoaderCircle className="animate-spin" />} Аннулировать
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="flex items-center gap-2 text-sm text-muted-foreground"><Users className="size-4" /> Пока никто не нажал «Участвую»</p>
      )}
    </li>
  );
}

const CRITICAL: (keyof GoalChanges)[] = ["target", "date_from", "date_to", "prize", "prize_type"];

/** One form to create and to edit a goal. Kind, mode and who it is for are fixed once created. */
function GoalForm({ me, everyone, types, initial, onDone, onCancel }: {
  me: User; everyone: boolean; types: string[]; initial?: Goal; onDone: () => void; onCancel?: () => void;
}) {
  const editing = !!initial;
  const users = useLoad(() => (editing ? Promise.resolve({ items: [] as User[] }) : api.admin.users()), [editing]);
  const nets = useLoad(() => (editing || !everyone ? Promise.resolve({ items: [] }) : api.admin.networks()), [editing, everyone]);
  const today = moscowToday();
  const [kind, setKind] = useState<Goal["kind"]>(initial?.kind ?? (everyone ? "team" : "network"));
  const [mode, setMode] = useState<Goal["mode"]>(initial?.mode ?? "shared");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [target, setTarget] = useState(initial ? String(initial.target) : "");
  const [from, setFrom] = useState(initial?.date_from ?? today);
  const [to, setTo] = useState(initial?.date_to ?? addDays(today, 6));
  const [prize, setPrize] = useState(prizeState(initial));
  const [userId, setUserId] = useState<number | null>(null);
  const [networkId, setNetworkId] = useState<number | null>(me.network_id);
  const [asking, setAsking] = useState<string[] | null>(null);
  const { busy, run } = useAction();
  const kinds: Goal["kind"][] = everyone ? ["team", "network", "personal"] : ["network", "personal"];
  const people = (users.data?.items ?? []).filter((u) => u.id !== me.id);

  function changes(): GoalChanges {
    const next: GoalChanges = {
      title: title.trim(), description: description.trim() || null, target: Number(target), date_from: from, date_to: to,
      prize: prize.prize.trim() || null, prize_type: prize.type.trim() || null,
    };
    if (!initial) return next;
    const was = initial as unknown as Record<string, unknown>;
    return Object.fromEntries(Object.entries(next).filter(([k, v]) => (was[k] ?? null) !== v)) as GoalChanges;
  }

  async function save(notify: boolean) {
    setAsking(null);
    try {
      const res = await run("save", async (k) => {
        const photo = await prizePhotoValue(prize);
        if (!initial) {
          return api.admin.createGoal({
            title: title.trim(), kind, mode: kind === "personal" ? "each" : mode, target: Number(target), date_from: from, date_to: to,
            description: description.trim() || undefined, user_id: kind === "personal" ? userId : null,
            network_id: kind === "network" ? networkId : null, prize: prize.prize.trim() || null, prize_type: prize.type.trim() || null,
            prize_photo: photo ?? null,
          }, k);
        }
        return api.admin.updateGoal(initial.id, { ...changes(), ...(photo !== undefined ? { prize_photo: photo } : {}), version: initial.version, notify }, k);
      });
      if (!res) return;
      toast.success(!initial ? "Цель создана, люди получат уведомление" : notify ? "Сохранено, участники уведомлены" : "Сохранено");
      onDone();
    } catch {
      /* toast shown */
    }
  }

  function submit() {
    const critical = initial ? CRITICAL.filter((k) => k in changes()) : [];
    if (critical.length) setAsking(critical);
    else void save(false);
  }

  const nothing = editing && Object.keys(changes()).length === 0 && !prize.file && !prize.cleared;
  return (
    <div className="space-y-3">
      {!editing && (
        <div className="grid gap-1 rounded-2xl bg-muted p-1" style={{ gridTemplateColumns: `repeat(${kinds.length}, minmax(0, 1fr))` }}>
          {kinds.map((k) => (
            <button key={k} onClick={() => setKind(k)} className={cn("press h-10 rounded-xl text-sm font-semibold", kind === k ? "bg-card shadow-lift" : "text-muted-foreground")}>
              {KIND[k]}
            </button>
          ))}
        </div>
      )}
      <input className={field} placeholder="Название, например «Неделя рекордов»" value={title} onChange={(e) => setTitle(e.target.value.slice(0, 80))} />
      {!editing && kind === "personal" && (
        <select className={field} value={userId ?? ""} onChange={(e) => setUserId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">Кому</option>
          {people.map((u) => <option key={u.id} value={u.id}>{u.first_name} {u.last_name}</option>)}
        </select>
      )}
      {!editing && kind === "network" && everyone && (
        <select className={field} value={networkId ?? ""} onChange={(e) => setNetworkId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">Сеть</option>
          {nets.data?.items.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
        </select>
      )}
      {!editing && kind !== "personal" && (
        <div className="grid grid-cols-2 gap-1 rounded-2xl bg-muted p-1">
          {(["shared", "each"] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)} className={cn("press h-10 rounded-xl text-sm font-semibold", mode === m ? "bg-card shadow-lift" : "text-muted-foreground")}>
              {m === "shared" ? "Общая норма" : "Норма каждому"}
            </button>
          ))}
        </div>
      )}
      <div className="grid grid-cols-3 gap-2">
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Норма, шт.</span>
          <input inputMode="numeric" className={cn(field, "tabular")} value={target} onChange={(e) => setTarget(e.target.value.replace(/\D/g, "").slice(0, 7))} /></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">С</span>
          <input type="date" className={field} value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">По</span>
          <input type="date" className={field} value={to} onChange={(e) => setTo(e.target.value)} /></label>
      </div>
      {editing && Number(target) > (initial?.target ?? 0) && initial?.status === "completed" && (
        <p className="text-xs text-muted-foreground">Выполнение не снимется само — после сохранения появится кнопка «Снять выполнение».</p>
      )}
      <PrizeFields types={types} value={prize} onChange={setPrize} currentUrl={initial?.prize_photo_url} />
      <textarea rows={2} className="w-full resize-none rounded-xl border-0 bg-muted px-3 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-ring/25"
                placeholder="Описание (необязательно)" value={description} onChange={(e) => setDescription(e.target.value.slice(0, 500))} />
      <div className="flex gap-2">
        {onCancel && <Button variant="secondary" className="press h-12 rounded-2xl px-5" onClick={onCancel}>Отмена</Button>}
        <Button className="press h-12 flex-1 rounded-2xl text-[15px] font-semibold"
                disabled={!!busy || !title.trim() || !Number(target) || nothing
                  || (!editing && kind === "personal" && !userId) || (!editing && kind === "network" && !networkId)}
                onClick={submit}>
          {busy === "save" && <LoaderCircle className="animate-spin" />} {editing ? "Сохранить" : "Создать и сообщить"}
        </Button>
      </div>
      <NotifyDialog fields={asking} onChoose={save} onCancel={() => setAsking(null)} />
    </div>
  );
}

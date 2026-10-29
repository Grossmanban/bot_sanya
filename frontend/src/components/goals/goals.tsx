"use client";

import { useState } from "react";
import { Ban, CircleCheck, LoaderCircle, Plus, Target, TrendingUp, Users } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, ApiError, PRODUCT_TITLES, type Goal, type GoalChanges, type Product, type User } from "@/lib/api";
import { useCan, useManageableProducts } from "@/lib/caps";
import { addDays, dayMonth, plural } from "@/lib/format";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import {
  ANNUL_PRESETS, DeleteButton, EditButton, NotifyDialog, PrizeFields, PrizeThumb, ReasonInput, VisibilityButton,
  prizePhotoValue, prizeState, prizeText,
} from "../lifecycle";

const KIND: Record<Goal["kind"], string> = { personal: "Личная", team: "Командная", network: "Сеть", group: "Группа", point: "Точка" };
const FOREVER = "9999-12-31";

/** «Точка «Европа» · каждый день с 06:00» or «Сеть «Север» · до 23 октября». */
function subtitle(g: Goal): string {
  if (g.kind === "point") {
    const when = g.forever ? `каждый день с ${g.reset_time}` : `до ${dayMonth(g.date_to)}, день с ${g.reset_time}`;
    return `Точка «${g.location}» · ${when}`;
  }
  return `${KIND[g.kind]}${g.network ? ` «${g.network}»` : ""} · до ${dayMonth(g.date_to)}`;
}

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
  if (g.kind === "point") {
    // a point's day counts reports under review at once
    return <p className="text-xs text-muted-foreground">Из них на проверке: <b className="text-wait">{pending} шт.</b></p>;
  }
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
                  {subtitle(g)}
                  {g.product ? ` · ${PRODUCT_TITLES[g.product]}, принятые выполнения` : ""}
                  {g.kind === "point" ? ` · сегодня на точке ${g.participants.length}`
                    : g.mode === "shared" && g.kind !== "personal" ? ` · общая норма, ${g.participants.length} ${plural(g.participants.length, "участник", "участника", "участников")}` : ""}
                </p>
                {prizeText(g) && <p className="mt-1 text-sm">🏆 {prizeText(g)}</p>}
              </div>
              {(g.prize || g.prize_type || g.prize_photo_url) && <PrizeThumb url={g.prize_photo_url} className="size-14" />}
              {!g.joined && !g.my_annulled && g.status === "active" && (g.kind === "team" || g.kind === "network") && (
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
            {g.kind === "point"
              ? `${subtitle(g)} · ${dayMonth(g.day ?? g.date_from)}: план ${g.plan ?? 0} шт.`
              : `${KIND[g.kind]}${g.network ? ` «${g.network}»` : ""}${g.person ? ` · ${g.person}` : ""} · ${dayMonth(g.date_from)} – ${dayMonth(g.date_to)}`}
            {g.kind !== "personal" && g.kind !== "point" ? ` · ${g.mode === "shared" ? "общая норма" : "норма каждому"}` : ""}
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
                  {p.on_review && <span className="ml-1 text-xs text-wait">· на проверке</span>}
                </span>
                {g.mode === "each" && !p.annulled && (
                  <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                    <span className="block h-full rounded-full bg-primary" style={{ width: `${Math.min(100, (p.volume / g.target) * 100)}%` }} />
                  </span>
                )}
                <span className="w-14 text-right font-semibold tabular">{p.volume}</span>
                {g.can_manage && !p.annulled && g.kind !== "point" && (
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

const CRITICAL: (keyof GoalChanges)[] = ["target", "date_from", "date_to", "prize", "prize_type", "reset_time", "location", "forever"];

/** One form to create and to edit a goal. Kind, mode and who it is for are fixed once created. */
function GoalForm({ me, everyone, types, initial, onDone, onCancel }: {
  me: User; everyone: boolean; types: string[]; initial?: Goal; onDone: () => void; onCancel?: () => void;
}) {
  const editing = !!initial;
  const users = useLoad(() => (editing ? Promise.resolve({ items: [] as User[] }) : api.admin.users()), [editing]);
  const nets = useLoad(() => (editing || !everyone ? Promise.resolve({ items: [] }) : api.admin.networks()), [editing, everyone]);
  const points = useLoad(() => (everyone ? api.points() : Promise.resolve({ items: [] })), [everyone]);
  const today = moscowToday();
  const [kind, setKind] = useState<Goal["kind"]>(initial?.kind ?? (everyone ? "team" : "network"));
  const [mode, setMode] = useState<Goal["mode"]>(initial?.mode ?? "shared");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [target, setTarget] = useState(initial ? String(initial.target) : "");
  const [from, setFrom] = useState(initial?.date_from ?? today);
  const [to, setTo] = useState(initial?.date_to && initial.date_to !== FOREVER ? initial.date_to : addDays(today, 6));
  // a point's daily norm: which point, when the day starts again, «каждый день» or until a date
  const [location, setLocation] = useState(initial?.location ?? "");
  const [reset, setReset] = useState(initial?.reset_time ?? "06:00");
  const [forever, setForever] = useState(initial ? !!initial.forever : true);
  const [picked, setPicked] = useState<number[]>([]);
  const [prize, setPrize] = useState(prizeState(initial));
  const [userId, setUserId] = useState<number | null>(null);
  const [networkId, setNetworkId] = useState<number | null>(me.network_id);
  const [asking, setAsking] = useState<string[] | null>(null);
  // ТЗ 27.10 §29: a product's goal; people without access → the server asks, the admin may grant it here
  const [product, setProduct] = useState<Product | "">(initial?.product ?? "");
  const [grantAsk, setGrantAsk] = useState<string | null>(null);
  // ТЗ «Яндекс Плюс» §11: only the products this person may manage are offered (the server checks again)
  const allowed = useManageableProducts();
  const { busy, run } = useAction();
  const kinds: Goal["kind"][] = everyone ? ["team", "network", "group", "point", "personal"] : ["network", "group", "personal"];
  const people = (users.data?.items ?? []).filter((u) => u.id !== me.id);

  function changes(): GoalChanges {
    const next: GoalChanges = {
      title: title.trim(), description: description.trim() || null, target: Number(target), date_from: from,
      ...(kind === "point" && forever ? {} : { date_to: to }),
      prize: prize.prize.trim() || null, prize_type: prize.type.trim() || null,
      ...(kind === "point" ? { reset_time: reset, location } : {}),
    };
    if (!initial) return next;
    const was = initial as unknown as Record<string, unknown>;
    const out = Object.fromEntries(Object.entries(next).filter(([k, v]) => (was[k] ?? null) !== v)) as GoalChanges;
    if (kind === "point" && forever !== !!initial.forever) {
      if (forever) out.forever = true;
      else out.date_to = to;
    }
    return out;
  }

  async function save(notify: boolean, grant = false) {
    setAsking(null);
    setGrantAsk(null);
    try {
      const res = await run("save", async (k) => {
        const photo = await prizePhotoValue(prize);
        if (!initial) {
          return api.admin.createGoal({
            title: title.trim(), kind, mode: kind === "personal" ? "each" : kind === "point" ? "shared" : mode, target: Number(target),
            date_from: from, date_to: to, forever: kind === "point" && forever,
            description: description.trim() || undefined, user_id: kind === "personal" ? userId : null,
            network_id: kind === "network" ? networkId : null, prize: prize.prize.trim() || null, prize_type: prize.type.trim() || null,
            prize_photo: photo ?? null, user_ids: kind === "group" ? picked : undefined,
            location: kind === "point" ? location : undefined, reset_time: kind === "point" ? reset : undefined,
            product: kind === "point" ? null : product || null, grant_access: grant,
          }, k);
        }
        return api.admin.updateGoal(initial.id, { ...changes(), ...(photo !== undefined ? { prize_photo: photo } : {}), version: initial.version, notify }, k);
      });
      if (!res) return;
      toast.success(!initial ? "Цель создана, люди получат уведомление" : notify ? "Сохранено, участники уведомлены" : "Сохранено");
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.code === "no_product_access") setGrantAsk(e.message);
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
        <div className="flex flex-wrap gap-1 rounded-2xl bg-muted p-1">
          {kinds.map((k) => (
            <button key={k} onClick={() => setKind(k)} className={cn("press h-10 min-w-[30%] flex-1 rounded-xl px-2 text-sm font-semibold", kind === k ? "bg-card shadow-lift" : "text-muted-foreground")}>
              {KIND[k]}
            </button>
          ))}
        </div>
      )}
      {!editing && kind !== "point" && allowed.length > 0 && (
        <select aria-label="Что считаем" className={field} value={product} onChange={(e) => setProduct(e.target.value as Product | "")}>
          <option value="">Считаем штуки смен</option>
          {(Object.keys(PRODUCT_TITLES) as Product[]).filter((p) => allowed.includes(p)).map((p) => (
            <option key={p} value={p}>Считаем принятые выполнения «{PRODUCT_TITLES[p]}»</option>
          ))}
        </select>
      )}
      {grantAsk && (
        <div className="space-y-2 rounded-2xl bg-wait-soft p-3 text-sm text-wait">
          <p>{grantAsk}</p>
          <div className="flex gap-2">
            <Button size="sm" className="press h-9 rounded-xl" disabled={!!busy} onClick={() => save(false, true)}>Выдать доступ и создать</Button>
            <Button size="sm" variant="ghost" className="h-9 rounded-xl" onClick={() => setGrantAsk(null)}>Отмена</Button>
          </div>
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
      {!editing && kind === "group" && <PeopleChecklist people={people} value={picked} onChange={setPicked} />}
      {kind === "point" && (
        <div className="space-y-3 rounded-2xl bg-muted/50 p-3">
          <select className={field} value={location} onChange={(e) => setLocation(e.target.value)} aria-label="Точка">
            <option value="">Точка</option>
            {(points.data?.items ?? []).map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
          </select>
          {(points.data?.items.length ?? 1) === 0 && <p className="text-xs text-cancel">Сначала добавьте точки в разделе «Точки».</p>}
          <label className="flex items-center gap-3 text-sm">
            <span className="flex-1">
              <span className="block font-medium">День начинается в</span>
              <span className="block text-xs text-muted-foreground">В это время прогресс точки обнуляется и считается новый день</span>
            </span>
            <input type="time" className="h-10 rounded-xl border-0 bg-card px-3 tabular outline-none" value={reset}
                   onChange={(e) => e.target.value && setReset(e.target.value)} />
          </label>
          <label className="flex cursor-pointer items-center gap-3 text-sm">
            <span className="flex-1 font-medium">Каждый день, пока не измените</span>
            <Switch checked={forever} onCheckedChange={setForever} />
          </label>
          <p className="text-xs text-muted-foreground">
            Участвуют все, у кого в этот день смена на точке. Считаются принятые отчёты и отчёты на проверке — с правками администратора.
          </p>
        </div>
      )}
      {!editing && kind !== "personal" && kind !== "point" && (
        <div className="grid grid-cols-2 gap-1 rounded-2xl bg-muted p-1">
          {(["shared", "each"] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)} className={cn("press h-10 rounded-xl text-sm font-semibold", mode === m ? "bg-card shadow-lift" : "text-muted-foreground")}>
              {m === "shared" ? "Общая норма" : "Норма каждому"}
            </button>
          ))}
        </div>
      )}
      <div className="grid grid-cols-3 gap-2">
        <label className="space-y-1"><span className="text-xs text-muted-foreground">{kind === "point" ? "Норма на день" : "Норма, шт."}</span>
          <input inputMode="numeric" className={cn(field, "tabular")} value={target} onChange={(e) => setTarget(e.target.value.replace(/\D/g, "").slice(0, 7))} /></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">С</span>
          <input type="date" className={field} value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        {!(kind === "point" && forever) && (
          <label className="space-y-1"><span className="text-xs text-muted-foreground">По</span>
            <input type="date" className={field} value={to} onChange={(e) => setTo(e.target.value)} /></label>
        )}
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
                  || (!editing && kind === "personal" && !userId) || (!editing && kind === "network" && !networkId)
                  || (!editing && kind === "group" && picked.length === 0) || (kind === "point" && !location)}
                onClick={submit}>
          {busy === "save" && <LoaderCircle className="animate-spin" />} {editing ? "Сохранить" : "Создать и сообщить"}
        </Button>
      </div>
      <NotifyDialog fields={asking} onChoose={save} onCancel={() => setAsking(null)} />
    </div>
  );
}

/** A group goal: tick the people (the customer: no saved groups, just checkboxes each time). */
function PeopleChecklist({ people, value, onChange }: { people: User[]; value: number[]; onChange: (v: number[]) => void }) {
  const [q, setQ] = useState("");
  const shown = people.filter((u) => `${u.first_name} ${u.last_name ?? ""}`.toLowerCase().includes(q.trim().toLowerCase()));
  const all = shown.length > 0 && shown.every((u) => value.includes(u.id));
  return (
    <div className="rounded-2xl bg-muted/50 p-3">
      <div className="flex items-center gap-2">
        <input className="h-10 min-w-0 flex-1 rounded-xl border-0 bg-card px-3 text-sm outline-none focus:ring-2 focus:ring-ring/25"
               placeholder="Поиск по имени" value={q} onChange={(e) => setQ(e.target.value)} />
        <Button variant="ghost" size="sm" className="h-10 shrink-0 rounded-xl"
                onClick={() => onChange(all ? value.filter((id) => !shown.some((u) => u.id === id)) : [...new Set([...value, ...shown.map((u) => u.id)])])}>
          {all ? "Снять выбор" : "Выбрать всех"}
        </Button>
      </div>
      <ul className="mt-2 max-h-64 overflow-y-auto" aria-label="Кому цель">
        {shown.map((u) => {
          const on = value.includes(u.id);
          return (
            <li key={u.id}>
              <label className="flex cursor-pointer items-center gap-3 rounded-xl px-2 py-2 hover:bg-card">
                <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={on}
                       onChange={() => onChange(on ? value.filter((x) => x !== u.id) : [...value, u.id])} />
                <span className="min-w-0 flex-1 truncate text-[15px]">{u.first_name} {u.last_name}</span>
              </label>
            </li>
          );
        })}
      </ul>
      <p className="mt-1 px-2 text-xs text-muted-foreground">Выбрано: {value.length}</p>
    </div>
  );
}

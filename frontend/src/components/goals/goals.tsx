"use client";

import { useState } from "react";
import { Archive, CircleCheck, LoaderCircle, Plus, Target, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Goal, type User } from "@/lib/api";
import { useCan } from "@/lib/caps";
import { addDays, dayMonth, plural } from "@/lib/format";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";

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
  const items = data?.items.filter((g) => g.status !== "archived") ?? [];
  if (!items.length) return null;
  return (
    <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
      <h2 className="flex items-center gap-2 px-5 pt-5 pb-1 font-semibold md:px-6"><Target className="size-4" /> Цели</h2>
      <ul className="divide-y">
        {items.map((g) => (
          <li key={g.id} className="row-in space-y-2.5 px-5 py-4 md:px-6">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-semibold">{g.done && <CircleCheck className="mr-1 inline size-4 text-go" />}{g.title}</p>
                <p className="text-xs text-muted-foreground">
                  {KIND[g.kind]}{g.network ? ` «${g.network}»` : ""} · до {dayMonth(g.date_to)}
                  {g.mode === "shared" && g.kind !== "personal" ? ` · общая норма, ${g.participants.length} ${plural(g.participants.length, "участник", "участника", "участников")}` : ""}
                </p>
              </div>
              {!g.joined && g.status === "active" && (
                <Button size="sm" className="press h-9 shrink-0 rounded-xl" disabled={!!busy}
                        onClick={async () => { try { await run(`j${g.id}`, () => api.joinGoal(g.id), "Вы участвуете"); await reload(); } catch { /* toast */ } }}>
                  {busy === `j${g.id}` ? <LoaderCircle className="animate-spin" /> : null} Участвую
                </Button>
              )}
            </div>
            {g.description && <p className="text-sm text-muted-foreground">{g.description}</p>}
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

/** Admin: create goals and watch progress (ТЗ 6.3). Trainers: personal goals for their people and their network. */
export function GoalsSection({ me }: { me: User }) {
  const everyone = useCan("raspred");
  const { data, reload } = useLoad(() => api.admin.goals(), []);
  const users = useLoad(() => api.admin.users(), []);
  const nets = useLoad(() => api.admin.networks(), []);
  const today = moscowToday();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<Goal["kind"]>(everyone ? "team" : "network");
  const [mode, setMode] = useState<Goal["mode"]>("shared");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [target, setTarget] = useState("");
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(addDays(today, 6));
  const [userId, setUserId] = useState<number | null>(null);
  const [networkId, setNetworkId] = useState<number | null>(me.network_id);
  const { busy, run } = useAction();
  const kinds: Goal["kind"][] = everyone ? ["team", "network", "personal"] : ["network", "personal"];
  const people = (users.data?.items ?? []).filter((u) => u.id !== me.id);

  async function create() {
    try {
      const res = await run("create", (k) => api.admin.createGoal({
        title: title.trim(), kind, mode: kind === "personal" ? "each" : mode, target: Number(target), date_from: from, date_to: to,
        description: description.trim() || undefined, user_id: kind === "personal" ? userId : null,
        network_id: kind === "network" ? networkId : null,
      }, k), "Цель создана, люди получат уведомление");
      if (res) {
        setOpen(false);
        setTitle("");
        setTarget("");
        setDescription("");
        await reload();
      }
    } catch {
      /* toast shown */
    }
  }

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
          <div className="row-in mt-4 space-y-3">
            <div className="grid gap-1 rounded-2xl bg-muted p-1" style={{ gridTemplateColumns: `repeat(${kinds.length}, minmax(0, 1fr))` }}>
              {kinds.map((k) => (
                <button key={k} onClick={() => setKind(k)} className={cn("press h-10 rounded-xl text-sm font-semibold", kind === k ? "bg-card shadow-lift" : "text-muted-foreground")}>
                  {KIND[k]}
                </button>
              ))}
            </div>
            <input className={field} placeholder="Название, например «Неделя рекордов»" value={title} onChange={(e) => setTitle(e.target.value.slice(0, 80))} />
            {kind === "personal" && (
              <select className={field} value={userId ?? ""} onChange={(e) => setUserId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">Кому</option>
                {people.map((u) => <option key={u.id} value={u.id}>{u.first_name} {u.last_name}</option>)}
              </select>
            )}
            {kind === "network" && everyone && (
              <select className={field} value={networkId ?? ""} onChange={(e) => setNetworkId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">Сеть</option>
                {nets.data?.items.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
              </select>
            )}
            {kind !== "personal" && (
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
            <textarea rows={2} className="w-full resize-none rounded-xl border-0 bg-muted px-3 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-ring/25"
                      placeholder="Описание или приз (необязательно)" value={description} onChange={(e) => setDescription(e.target.value.slice(0, 500))} />
            <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold"
                    disabled={!!busy || !title.trim() || !Number(target) || (kind === "personal" && !userId) || (kind === "network" && !networkId)}
                    onClick={create}>
              {busy === "create" && <LoaderCircle className="animate-spin" />} Создать и сообщить
            </Button>
          </div>
        )}
      </section>

      {!data ? <Skeleton className="h-40 rounded-3xl" /> : data.items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">Целей пока нет.</p>
      ) : (
        <ul className="space-y-3">
          {data.items.map((g) => (
            <li key={g.id} className="row-in space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-semibold">{g.status === "completed" && <CircleCheck className="mr-1 inline size-4 text-go" />}{g.title}</p>
                  <p className="text-xs text-muted-foreground">
                    {KIND[g.kind]}{g.network ? ` «${g.network}»` : ""}{g.person ? ` · ${g.person}` : ""} · {dayMonth(g.date_from)} – {dayMonth(g.date_to)}
                    {g.kind !== "personal" ? ` · ${g.mode === "shared" ? "общая норма" : "норма каждому"}` : ""}
                  </p>
                </div>
                <ArchiveGoal goal={g} onDone={reload} />
              </div>
              {g.mode === "shared" ? <Progress value={g.total} target={g.target} done={g.status === "completed"} /> : null}
              <Breakdown g={g} />
              {g.participants.length > 0 ? (
                <ul className="space-y-1.5">
                  {[...g.participants].sort((a, b) => b.volume - a.volume).map((p) => (
                    <li key={p.user_id} className="flex items-center gap-3 text-sm">
                      <span className="min-w-0 flex-1 truncate">{p.done && <CircleCheck className="mr-1 inline size-3.5 text-go" />}{p.name}</span>
                      {g.mode === "each" && (
                        <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                          <span className="block h-full rounded-full bg-primary" style={{ width: `${Math.min(100, (p.volume / g.target) * 100)}%` }} />
                        </span>
                      )}
                      <span className="w-14 text-right font-semibold tabular">{p.volume}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="flex items-center gap-2 text-sm text-muted-foreground"><Users className="size-4" /> Пока никто не нажал «Участвую»</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ArchiveGoal({ goal, onDone }: { goal: Goal; onDone: () => void }) {
  const { busy, run } = useAction();
  return (
    <Button variant="ghost" size="sm" className="h-8 shrink-0 rounded-lg text-muted-foreground" disabled={!!busy} aria-label="В архив"
            onClick={async () => { try { await run("arch", (k) => api.admin.archiveGoal(goal.id, k), "Цель в архиве"); onDone(); } catch { /* toast */ } }}>
      {busy ? <LoaderCircle className="animate-spin" /> : <Archive />}
    </Button>
  );
}

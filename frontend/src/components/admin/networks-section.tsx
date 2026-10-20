"use client";

import { useState } from "react";
import { Archive, Coins, Crown, LoaderCircle, Pencil, Plus, Trash2, UserMinus, UserPlus, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, type Network, type User } from "@/lib/api";
import { useCan } from "@/lib/caps";
import { plural } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { Avatar } from "../avatar";

const field = "h-11 min-w-0 flex-1 rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

/** The organisation tree (ТЗ 9.2): networks, their trainer and people. Owners edit, admins look. */
export function NetworksSection() {
  const canEdit = useCan("networks");
  const nets = useLoad(() => api.admin.networks(), []);
  const users = useLoad(() => api.admin.users(), []);
  const reload = async () => {
    await Promise.all([nets.reload(), users.reload()]);
  };
  if (!nets.data || !users.data) return <Skeleton className="h-80 rounded-3xl" />;
  const people = users.data.items.filter((u) => !u.is_owner && u.role !== "admin" && u.account_state !== "blocked");
  // an owner may head networks too (customer round B): their people stand right under the owner, no trainer share
  const owners = users.data.items.filter((u) => u.is_owner && u.status === "approved");
  const loose = people.filter((u) => u.network_id == null);

  return (
    <div className="space-y-4">
      {canEdit && <CreateNetwork onDone={reload} />}
      {nets.data.items.length === 0 && (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">
          Сетей пока нет. Сеть — это команда с тренером: тренер видит и ведёт только своих.
        </p>
      )}
      {nets.data.items.map((n) => (
        <NetworkCard key={n.id} net={n} people={people} owners={owners} canEdit={canEdit} onChanged={reload} />
      ))}
      {loose.length > 0 && (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <h2 className="px-5 pt-5 pb-2 font-semibold md:px-6">Без сети <span className="text-muted-foreground">{loose.length}</span></h2>
          <ul className="divide-y">
            {loose.map((u) => (
              <li key={u.id} className="flex items-center gap-3 px-5 py-3 md:px-6">
                <Avatar user={u} className="size-9 rounded-xl text-xs" />
                <span className="min-w-0 flex-1 truncate">{u.first_name} {u.last_name}</span>
                {canEdit && nets.data!.items.length > 0 && <MoveTo user={u} nets={nets.data!.items} onDone={reload} />}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function CreateNetwork({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const { busy, run } = useAction();
  return (
    <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <h2 className="font-semibold">Новая сеть</h2>
      <div className="mt-3 flex gap-2">
        <input className={field} placeholder="Название сети" value={name} onChange={(e) => setName(e.target.value.slice(0, 60))} />
        <Button className="press h-11 rounded-xl px-4 font-semibold" disabled={!!busy || !name.trim()}
                onClick={async () => {
                  try {
                    await run("create", (k) => api.admin.createNetwork(name.trim(), k), "Сеть создана");
                    setName("");
                    onDone();
                  } catch {
                    /* toast shown */
                  }
                }}>
          {busy ? <LoaderCircle className="animate-spin" /> : <Plus />} Создать
        </Button>
      </div>
    </section>
  );
}

function NetworkCard({ net, people, owners, canEdit, onChanged }: {
  net: Network; people: User[]; owners: User[]; canEdit: boolean; onChanged: () => void;
}) {
  const canMoney = useCan("money");
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(net.name);
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState(false);
  const { busy, run } = useAction();
  const memberIds = new Set([...net.members.map((m) => m.id), net.trainer?.id]);
  const candidates = people.filter((u) => !memberIds.has(u.id));

  const act = async (name: string, fn: (k: string) => Promise<unknown>, ok: string) => {
    try {
      await run(name, fn, ok);
      onChanged();
    } catch {
      /* toast shown */
    }
  };

  return (
    <section className="row-in overflow-hidden rounded-3xl bg-card shadow-lift">
      <div className="flex items-start gap-3 p-5 md:px-6">
        <span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-primary text-primary-foreground"><Users className="size-5" /></span>
        <div className="min-w-0 flex-1">
          {renaming ? (
            <div className="flex gap-2">
              <input autoFocus className={field} value={name} onChange={(e) => setName(e.target.value.slice(0, 60))} />
              <Button size="sm" className="press h-11 rounded-xl" disabled={!!busy || !name.trim()}
                      onClick={() => act("rename", (k) => api.admin.updateNetwork(net.id, { name: name.trim() }, k), "Переименовано").then(() => setRenaming(false))}>
                OK
              </Button>
            </div>
          ) : (
            <h2 className="font-display text-lg font-semibold">{net.name}</h2>
          )}
          <p className="text-sm text-muted-foreground">
            {net.members.length} {plural(net.members.length, "человек", "человека", "человек")}
          </p>
        </div>
        {canEdit && !renaming && (
          <Button variant="ghost" size="sm" className="h-9 rounded-xl" aria-label="Переименовать" onClick={() => setRenaming(true)}>
            <Pencil />
          </Button>
        )}
      </div>

      <div className="border-t px-5 py-3.5 md:px-6">
        {net.trainer ? (
          <div className="flex items-center gap-3">
            <span className="grid size-9 place-items-center rounded-xl bg-wait-soft text-wait"><Crown className="size-4" /></span>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{net.trainer.name}</span>
              <span className="block text-xs text-muted-foreground">
                {net.trainer.is_owner ? "владелец — руководит напрямую, без тренерских" : "тренер"}
              </span>
            </span>
            {canEdit && (
              <Button variant="ghost" size="sm" className="h-8 rounded-lg text-muted-foreground" onClick={() => setPicking((v) => !v)}>
                Сменить
              </Button>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-muted-foreground">Тренер не назначен</span>
            {canEdit && <Button size="sm" className="press h-9 rounded-xl" onClick={() => setPicking((v) => !v)}><Crown /> Назначить</Button>}
          </div>
        )}
        {picking && (
          <Picker people={[...owners, ...people].filter((u) => u.id !== net.trainer?.id)} busy={!!busy} label="Руководитель сети"
                  onPick={(u) => act("trainer", (k) => api.admin.updateNetwork(net.id, { trainer_id: u.id }, k), u.is_owner ? `${u.first_name} руководит сетью` : `${u.first_name} — тренер сети`).then(() => setPicking(false))} />
        )}
      </div>

      {net.members.length > 0 && (
        <ul className="divide-y border-t">
          {net.members.map((m) => (
            <li key={m.id} className="flex items-center gap-3 px-5 py-2.5 md:px-6">
              <span className={cn("size-1.5 rounded-full", m.work_status === "working" ? "bg-go" : "bg-idle")} />
              <span className="min-w-0 flex-1 truncate text-[15px]">{m.name}</span>
              {m.account_state === "paused" && <span className="text-xs font-semibold text-wait">пауза</span>}
              {canEdit && (
                <Button variant="ghost" size="sm" className="h-8 rounded-lg text-muted-foreground" aria-label="Убрать из сети" disabled={!!busy}
                        onClick={() => act(`out-${m.id}`, (k) => api.admin.setMemberNetwork(m.id, null, k), "Убран из сети")}>
                  {busy === `out-${m.id}` ? <LoaderCircle className="animate-spin" /> : <UserMinus />}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      {canMoney && <NetworkRates net={net} onChanged={onChanged} />}

      {canEdit && (
        <div className="flex flex-wrap gap-2 border-t px-5 py-3.5 md:px-6">
          <Button variant="secondary" size="sm" className="press h-9 rounded-xl" onClick={() => setAdding((v) => !v)}><UserPlus /> Добавить людей</Button>
          {net.members.length === 0 && !net.trainer && (
            <Button variant="ghost" size="sm" className="h-9 rounded-xl text-muted-foreground" disabled={!!busy}
                    onClick={() => act("archive", (k) => api.admin.updateNetwork(net.id, { archive: true }, k), "Сеть в архиве")}>
              <Archive /> В архив
            </Button>
          )}
          {adding && (
            <div className="w-full">
              <Picker people={candidates} busy={!!busy} label="Добавить в сеть"
                      onPick={(u) => act(`in-${u.id}`, (k) => api.admin.setMemberNetwork(u.id, net.id, k), `${u.first_name} в сети «${net.name}»`)} />
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function Picker({ people, onPick, busy, label }: { people: User[]; onPick: (u: User) => void; busy: boolean; label: string }) {
  const [q, setQ] = useState("");
  const list = people.filter((u) => `${u.first_name} ${u.last_name}`.toLowerCase().includes(q.toLowerCase())).slice(0, 30);
  return (
    <div className="row-in mt-3 space-y-2">
      <input autoFocus className={cn(field, "w-full")} placeholder={`${label}: поиск`} value={q} onChange={(e) => setQ(e.target.value)} />
      <ul className="max-h-60 overflow-y-auto">
        {list.length === 0 && <li className="px-2 py-2 text-sm text-muted-foreground">Никого не нашлось</li>}
        {list.map((u) => (
          <li key={u.id}>
            <button disabled={busy} onClick={() => onPick(u)} className="press flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-muted">
              <Avatar user={u} className="size-8 rounded-lg text-xs" />
              <span className="min-w-0 flex-1 truncate">{u.first_name} {u.last_name}</span>
              {u.is_owner ? <span className="text-xs text-muted-foreground">владелец</span>
                : u.network_id != null && <span className="text-xs text-muted-foreground">из другой сети</span>}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MoveTo({ user, nets, onDone }: { user: User; nets: Network[]; onDone: () => void }) {
  const { busy, run } = useAction();
  return (
    <select disabled={!!busy} defaultValue="" aria-label="В сеть"
            className="h-9 rounded-xl border-0 bg-muted px-2 text-sm outline-none"
            onChange={async (e) => {
              const id = Number(e.target.value);
              if (!id) return;
              try {
                await run("move", (k) => api.admin.setMemberNetwork(user.id, id, k), "Добавлен в сеть");
                onDone();
              } catch {
                /* toast shown */
              }
            }}>
      <option value="">В сеть…</option>
      {nets.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
    </select>
  );
}

/** Owner only (customer round B): the network's base rate and the trainer's ladder. */
function NetworkRates({ net, onChanged }: { net: Network; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [rate, setRate] = useState(net.rate_base != null ? String(net.rate_base) : "");
  const [steps, setSteps] = useState<{ from: string; rate: string }[]>(
    (net.ladder ?? []).map((s) => ({ from: String(s.from), rate: String(s.rate) })));
  const [period, setPeriod] = useState<"day" | "week">(net.ladder_period ?? "day");
  const [withTrainer, setWithTrainer] = useState(!!net.ladder_counts_trainer);
  const { busy, run } = useAction();
  const ownerLed = !!net.trainer?.is_owner;
  const summary = [
    net.rate_base != null ? `ставка ${net.rate_base} ₽` : "общая ставка",
    ownerLed ? "без тренерских" : (net.ladder?.length ? `лестница: ${net.ladder.map((s) => `от ${s.from} — ${s.rate} ₽`).join(", ")}`
      : "тренеру — общая комиссия"),
  ].join(" · ");

  async function save() {
    const ladder = steps.filter((s) => s.from !== "" && s.rate !== "").map((s) => ({ from: Number(s.from), rate: Number(s.rate) }));
    try {
      await run("rates", (k) => api.admin.updateNetwork(net.id, {
        ...(rate ? { rate_base: Number(rate) } : { clear_rate: true }),
        ladder, ladder_period: period, ladder_counts_trainer: withTrainer,
      }, k), "Ставки сети сохранены");
      setOpen(false);
      onChanged();
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="border-t px-5 py-3.5 md:px-6">
      <button className="press flex w-full items-center gap-3 text-left" onClick={() => setOpen((v) => !v)}>
        <span className="grid size-9 place-items-center rounded-xl bg-muted"><Coins className="size-4" /></span>
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-medium">Ставки сети</span>
          <span className="block truncate text-xs text-muted-foreground">{summary}</span>
        </span>
        <span className="text-xs font-semibold text-muted-foreground">{open ? "Скрыть" : "Изменить"}</span>
      </button>
      {open && (
        <div className="row-in mt-4 space-y-4">
          <label className="flex items-center gap-3">
            <span className="min-w-0 flex-1 text-sm">
              Ставка сотрудникам сети
              <span className="block text-xs text-muted-foreground">Пусто — общая; личная ставка сотрудника важнее</span>
            </span>
            <input inputMode="numeric" placeholder="общая" value={rate} onChange={(e) => setRate(e.target.value.replace(/\D/g, "").slice(0, 6))}
                   className="h-10 w-24 rounded-xl border-0 bg-muted px-3 text-sm font-semibold tabular outline-none focus:ring-2 focus:ring-ring/25" />
          </label>

          {ownerLed ? (
            <p className="rounded-xl bg-muted/60 px-3 py-2.5 text-sm text-muted-foreground">Сетью руководит владелец: тренерские не начисляются.</p>
          ) : (
            <div className="space-y-3">
              <div>
                <p className="text-sm font-semibold">Лестница тренера</p>
                <p className="text-xs text-muted-foreground">
                  Команда сделала объём ступени — тренер получает ставку ступени за <b>каждую</b> штуку команды.
                  Без ступеней — общая комиссия за штуку.
                </p>
              </div>
              <div className="flex gap-1 rounded-xl bg-muted p-1">
                {(["day", "week"] as const).map((p) => (
                  <button key={p} onClick={() => setPeriod(p)}
                          className={cn("press h-8 flex-1 rounded-lg text-xs font-semibold", period === p ? "bg-card shadow-lift" : "text-muted-foreground")}>
                    {p === "day" ? "Объём за день" : "Объём за неделю"}
                  </button>
                ))}
              </div>
              <ul className="space-y-2">
                {steps.map((st, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm">
                    <span className="text-muted-foreground">от</span>
                    <input inputMode="numeric" aria-label="От, штук" value={st.from}
                           onChange={(e) => setSteps(steps.map((x, j) => (j === i ? { ...x, from: e.target.value.replace(/\D/g, "").slice(0, 6) } : x)))}
                           className="h-9 w-20 rounded-lg border-0 bg-muted px-2 text-sm tabular outline-none" />
                    <span className="text-muted-foreground">шт. →</span>
                    <input inputMode="numeric" aria-label="Ставка, ₽ за штуку" value={st.rate}
                           onChange={(e) => setSteps(steps.map((x, j) => (j === i ? { ...x, rate: e.target.value.replace(/\D/g, "").slice(0, 6) } : x)))}
                           className="h-9 w-16 rounded-lg border-0 bg-muted px-2 text-sm tabular outline-none" />
                    <span className="text-muted-foreground">₽/шт.</span>
                    <button aria-label="Убрать ступень" onClick={() => setSteps(steps.filter((_, j) => j !== i))}
                            className="press ml-auto grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted">
                      <Trash2 className="size-4" />
                    </button>
                  </li>
                ))}
              </ul>
              {steps.length < 10 && (
                <Button variant="secondary" size="sm" className="press h-9 rounded-xl"
                        onClick={() => setSteps([...steps, { from: steps.length ? "" : "0", rate: "" }])}>
                  <Plus /> Ступень
                </Button>
              )}
              <label className="flex cursor-pointer items-center gap-3">
                <span className="min-w-0 flex-1 text-sm">
                  Учитывать штуки самого тренера
                  <span className="block text-xs text-muted-foreground">По умолчанию — только результат команды</span>
                </span>
                <Switch checked={withTrainer} onCheckedChange={setWithTrainer} />
              </label>
            </div>
          )}
          <Button className="press h-11 w-full rounded-xl font-semibold" disabled={!!busy} onClick={save}>
            {busy && <LoaderCircle className="animate-spin" />} Сохранить ставки сети
          </Button>
        </div>
      )}
    </div>
  );
}

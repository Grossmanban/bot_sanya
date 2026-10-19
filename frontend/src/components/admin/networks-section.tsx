"use client";

import { useState } from "react";
import { Archive, Crown, LoaderCircle, Pencil, Plus, UserMinus, UserPlus, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
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
        <NetworkCard key={n.id} net={n} people={people} canEdit={canEdit} onChanged={reload} />
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

function NetworkCard({ net, people, canEdit, onChanged }: { net: Network; people: User[]; canEdit: boolean; onChanged: () => void }) {
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
              <span className="block text-xs text-muted-foreground">тренер</span>
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
          <Picker people={people.filter((u) => u.id !== net.trainer?.id)} busy={!!busy} label="Назначить тренером"
                  onPick={(u) => act("trainer", (k) => api.admin.updateNetwork(net.id, { trainer_id: u.id }, k), `${u.first_name} — тренер сети`).then(() => setPicking(false))} />
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
              {u.network_id != null && <span className="text-xs text-muted-foreground">из другой сети</span>}
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

"use client";

import { useState } from "react";
import { Check, ChevronRight, LoaderCircle, MessageCircle, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Drawer, DrawerClose, DrawerContent, DrawerDescription, DrawerTitle } from "@/components/ui/drawer";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type MoneyPerson, type Tx, type TxType, type User } from "@/lib/api";
import { useCan } from "@/lib/caps";
import { rub } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { openProfile, tapHaptic } from "@/lib/telegram";
import { useWide } from "@/lib/use-media";
import { cn } from "@/lib/utils";
import { Avatar } from "../avatar";
import { BalanceCard, TxForm, TxRow } from "./money";

type Tab = "people" | "approvals" | "journal";

/** Money: balances (/finances), trainer fine approvals and fine drafts, the ledger (ТЗ модуль 4). */
export function FinanceSection({ onChanged }: { onChanged: () => void }) {
  const [tab, setTab] = useState<Tab>("people");
  const [person, setPerson] = useState<{ id: number; name: string; username: string | null; telegram_id: number } | null>(null);
  const summary = useLoad(() => api.admin.financeSummary(), []);
  const waiting = (summary.data?.pending.length ?? 0) + (summary.data?.drafts.length ?? 0);
  const reload = async () => {
    await summary.reload();
    onChanged();
  };

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "people", label: "Балансы" },
    { id: "approvals", label: "Решения", count: waiting },
    { id: "journal", label: "Журнал" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex gap-1 rounded-2xl bg-card p-1 shadow-lift">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => { tapHaptic(); setTab(t.id); }}
                  className={cn("press flex h-10 flex-1 items-center justify-center gap-1.5 rounded-xl text-sm font-semibold",
                    tab === t.id ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>
            {t.label}
            {!!t.count && (
              <span className={cn("inline-grid min-w-5 place-items-center rounded-full px-1.5 text-[11px] leading-5 tabular",
                tab === t.id ? "bg-white/20" : "bg-wait text-white")}>{t.count}</span>
            )}
          </button>
        ))}
      </div>

      {tab === "people" && (
        <>
          <NewEntry onPick={(u) => setPerson({ id: u.id, name: `${u.first_name} ${u.last_name}`, username: u.username, telegram_id: u.telegram_id })} />
          {!summary.data ? <Skeleton className="h-48 rounded-3xl" /> : (
            <People people={summary.data.people} onPick={(p) => setPerson({ id: p.user_id, name: p.name, username: p.username, telegram_id: p.telegram_id })} />
          )}
        </>
      )}
      {tab === "approvals" && (!summary.data ? <Skeleton className="h-48 rounded-3xl" /> : (
        <Approvals pending={summary.data.pending} drafts={summary.data.drafts} onChanged={reload} />
      ))}
      {tab === "journal" && <Journal />}

      <PersonMoney person={person} onClose={() => setPerson(null)} onChanged={reload} />
    </div>
  );
}

function NewEntry({ onPick }: { onPick: (u: User) => void }) {
  const users = useLoad(() => api.admin.users(), []);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const list = (users.data?.items ?? []).filter((u) => `${u.first_name} ${u.last_name} ${u.username ?? ""}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <button className="flex w-full items-center gap-3 text-left" onClick={() => setOpen((v) => !v)}>
        <span className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground"><Plus className="size-5" /></span>
        <span className="flex-1">
          <span className="block font-semibold">Штраф, бонус или вычет</span>
          <span className="block text-sm text-muted-foreground">Выберите сотрудника</span>
        </span>
        <ChevronRight className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="row-in mt-4 space-y-2">
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по имени"
                 className="h-11 w-full rounded-xl border-0 bg-muted px-3 outline-none focus:ring-2 focus:ring-ring/25" />
          <ul className="max-h-64 overflow-y-auto">
            {list.map((u) => (
              <li key={u.id}>
                <button className="press flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-muted" onClick={() => onPick(u)}>
                  <Avatar user={u} className="size-8 rounded-lg text-xs" />
                  <span className="truncate">{u.first_name} {u.last_name}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function People({ people, onPick }: { people: MoneyPerson[]; onPick: (p: MoneyPerson) => void }) {
  if (people.length === 0) {
    return <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">Долгов и начислений нет.</p>;
  }
  return (
    <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
      <ul className="divide-y">
        {people.map((p) => (
          <li key={p.user_id}>
            <button onClick={() => onPick(p)} className="press flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-muted/60 md:px-6">
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{p.name}</span>
                <span className="block text-xs text-muted-foreground">
                  {[p.fines && `штрафы ${rub(p.fines)}`, p.bonuses && `бонусы ${rub(p.bonuses)}`, p.deductions && `вычеты ${rub(p.deductions)}`,
                    p.pending && `на согласовании ${rub(p.pending)}`, p.drafts && `без суммы: ${p.drafts}`].filter(Boolean).join(" · ")}
                </span>
              </span>
              <span className={cn("font-display font-semibold tabular", p.balance < 0 ? "text-cancel" : p.balance > 0 ? "text-go" : "text-muted-foreground")}>
                {p.balance > 0 ? "+" : p.balance < 0 ? "−" : ""}{rub(Math.abs(p.balance))}
              </span>
              <ChevronRight className="size-4 text-muted-foreground" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Approvals({ pending, drafts, onChanged }: { pending: Tx[]; drafts: Tx[]; onChanged: () => void }) {
  const canApprove = useCan("approve_fines");
  if (!pending.length && !drafts.length) {
    return <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">Решений не ждёт ничего.</p>;
  }
  return (
    <div className="space-y-4">
      {pending.length > 0 && canApprove && (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <h2 className="px-5 pt-5 font-semibold md:px-6">Штрафы от тренеров</h2>
          <p className="px-5 pb-2 text-sm text-muted-foreground md:px-6">Баланс не изменится, пока вы не одобрите.</p>
          <ul className="divide-y">
            {pending.map((tx) => <PendingRow key={tx.id} tx={tx} onDone={onChanged} />)}
          </ul>
        </section>
      )}
      {drafts.length > 0 && (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <h2 className="px-5 pt-5 font-semibold md:px-6">Штрафы без суммы</h2>
          <p className="px-5 pb-2 text-sm text-muted-foreground md:px-6">Окончательные отмены смен — назначьте сумму или аннулируйте.</p>
          <ul className="divide-y">
            {drafts.map((tx) => <TxRow key={tx.id} tx={tx} showName actions onChanged={onChanged} />)}
          </ul>
        </section>
      )}
    </div>
  );
}

function PendingRow({ tx, onDone }: { tx: Tx; onDone: () => void }) {
  const { busy, run } = useAction();
  const [comment, setComment] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const decide = async (approve: boolean) => {
    try {
      await run(approve ? "ok" : "no", (k) => api.admin.decideTx(tx.id, approve, comment.trim() || null, k), approve ? "Штраф одобрен" : "Заявка отклонена");
      onDone();
    } catch {
      /* toast shown */
    }
  };
  return (
    <li className="space-y-3 px-5 py-4 md:px-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold">{tx.name}</p>
          <p className="text-sm text-muted-foreground">{tx.reason}</p>
          <p className="text-xs text-muted-foreground">от {tx.created_by}</p>
        </div>
        <span className="font-display text-lg font-semibold text-cancel tabular">{rub(tx.amount ?? 0)}</span>
      </div>
      {rejecting && (
        <input autoFocus value={comment} onChange={(e) => setComment(e.target.value.slice(0, 300))} placeholder="Комментарий тренеру (необязательно)"
               className="h-10 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25" />
      )}
      <div className="flex gap-2">
        <Button className="press h-10 flex-1 rounded-xl bg-go font-semibold text-white hover:bg-go/90" disabled={!!busy} onClick={() => decide(true)}>
          {busy === "ok" ? <LoaderCircle className="animate-spin" /> : <Check />} Одобрить
        </Button>
        <Button variant="secondary" className="press h-10 flex-1 rounded-xl font-semibold" disabled={!!busy}
                onClick={() => (rejecting ? decide(false) : setRejecting(true))}>
          {busy === "no" ? <LoaderCircle className="animate-spin" /> : <X />} Отклонить
        </Button>
      </div>
    </li>
  );
}

const FILTERS: { id: TxType | "all"; label: string }[] = [
  { id: "all", label: "Все" },
  { id: "fine", label: "Штрафы" },
  { id: "bonus", label: "Бонусы" },
  { id: "deduction", label: "Вычеты" },
];

function Journal() {
  const [type, setType] = useState<TxType | "all">("all");
  const { data, reload } = useLoad(() => api.admin.finances({ type: type === "all" ? undefined : type }), [type]);
  return (
    <div className="space-y-3">
      <div className="flex gap-2 overflow-x-auto no-scrollbar">
        {FILTERS.map((f) => (
          <button key={f.id} onClick={() => setType(f.id)}
                  className={cn("press h-9 shrink-0 rounded-full px-4 text-sm font-semibold", type === f.id ? "bg-primary text-primary-foreground" : "bg-card shadow-lift")}>
            {f.label}
          </button>
        ))}
      </div>
      {!data ? <Skeleton className="h-48 rounded-3xl" /> : data.items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift">Записей нет.</p>
      ) : (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <ul className="divide-y">{data.items.map((tx) => <TxRow key={tx.id} tx={tx} showName actions onChanged={reload} />)}</ul>
        </section>
      )}
    </div>
  );
}

/** One person's money: balance, quick fine/bonus/deduction, «погасить всё», the ledger with annul. */
export function PersonMoney({ person, onClose, onChanged }: {
  person: { id: number; name: string; username: string | null; telegram_id: number } | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const wide = useWide();
  const canPay = useCan("money");
  const { data, reload } = useLoad(() => (person ? api.admin.finances({ userId: person.id }) : Promise.resolve(null)), [person?.id]);
  const { busy, run } = useAction();
  const refresh = async () => {
    await reload();
    onChanged();
  };

  return (
    <Drawer open={!!person} onOpenChange={(o) => !o && onClose()} direction={wide ? "right" : "bottom"} repositionInputs={false}>
      <DrawerContent className={cn("border-0 bg-card", wide ? "w-[28rem] max-w-[28rem] sm:max-w-[28rem]" : "mx-auto max-h-[92dvh] max-w-2xl rounded-t-[1.75rem]")}>
        {person && (
          <div className={cn("space-y-5 overflow-y-auto px-5", wide ? "h-full py-8" : "pb-safe pt-2")}>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <DrawerTitle className="font-display text-xl font-semibold">
                  <button type="button" className="text-left underline-offset-2 hover:underline"
                          onClick={() => openProfile(person.username, person.telegram_id, person.name)}>{person.name}</button>
                </DrawerTitle>
                <DrawerDescription>Штрафы, бонусы и вычеты</DrawerDescription>
              </div>
              {person.username && (
                <Button variant="secondary" size="sm" className="press h-9 rounded-xl" onClick={() => openProfile(person.username, person.telegram_id)}>
                  <MessageCircle /> Написать
                </Button>
              )}
              {wide && (
                <DrawerClose className="grid size-9 place-items-center rounded-full bg-muted" aria-label="Закрыть"><X className="size-4" /></DrawerClose>
              )}
            </div>
            {data?.balance && <BalanceCard balance={data.balance} />}
            {canPay && !!data?.balance?.fines && (
              <Button variant="secondary" className="press h-11 w-full rounded-xl font-semibold" disabled={!!busy}
                      onClick={async () => {
                        try {
                          const res = await run("pay", (k) => api.admin.payAll(person.id, k));
                          if (res) await refresh();
                        } catch {
                          /* toast shown */
                        }
                      }}>
                {busy === "pay" && <LoaderCircle className="animate-spin" />} Погасить все штрафы ({rub(data.balance.fines)})
              </Button>
            )}
            <div className="rounded-2xl bg-muted/50 p-4">
              <TxForm userId={person.id} onDone={refresh} />
            </div>
            {!data ? <Skeleton className="h-32 rounded-2xl" /> : data.items.length > 0 && (
              <section className="-mx-5 overflow-hidden border-t">
                <ul className="divide-y">{data.items.map((tx) => <TxRow key={tx.id} tx={tx} actions onChanged={refresh} />)}</ul>
              </section>
            )}
          </div>
        )}
      </DrawerContent>
    </Drawer>
  );
}

"use client";

import { useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Copy, FileSpreadsheet, ImagePlus, LoaderCircle, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ROLE_TITLES, type PayoutBatch, type PayoutRates, type PayoutRow, type PayoutSummary } from "@/lib/api";
import { compressImage } from "@/lib/image";
import { addDays, dayMonth, plural, rub } from "@/lib/format";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { haptic, tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { TxForm } from "./money";

const field = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

/** Payout registry (ТЗ модуль 10): history and the three-step wizard. Owners only. */
export function PayoutsSection() {
  const list = useLoad(() => api.admin.payouts(), []);
  const [mode, setMode] = useState<"list" | "new" | number>("list");

  if (mode === "new" && list.data) {
    return <Wizard defaults={list.data.rates} onClose={async (id) => { await list.reload(); setMode(id ?? "list"); }} />;
  }
  if (typeof mode === "number") return <BatchView id={mode} onBack={() => setMode("list")} />;

  return (
    <div className="space-y-4">
      <section className="rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
        <h2 className="font-display text-xl font-semibold">Реестр выплат</h2>
        <p className="mt-1.5 text-[15px] text-hero-muted">
          База считается по принятым отчётам с налогом на лень, тренерам — комиссия с объёма сети. Бонусы плюсуются,
          штрафы и вычеты зачитываются, долг сверх выплаты переносится.
        </p>
        <Button className="press mt-4 h-12 rounded-2xl bg-white px-5 text-[15px] font-semibold text-hero hover:bg-white/90"
                disabled={!list.data} onClick={() => { tapHaptic(); setMode("new"); }}>
          <Plus /> Новая выплата
        </Button>
      </section>
      {!list.data ? <Skeleton className="h-40 rounded-3xl" /> : list.data.items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">Выплат ещё не было.</p>
      ) : (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <ul className="divide-y">
            {list.data.items.map((b) => (
              <li key={b.id}>
                <button onClick={() => setMode(b.id)} className="press flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-muted/60 md:px-6">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{b.title}</span>
                    <span className="block text-xs text-muted-foreground">
                      {dayMonth(b.period_from)} – {dayMonth(b.period_to)} · {b.people} {plural(b.people, "человек", "человека", "человек")}
                    </span>
                  </span>
                  <span className="font-display font-semibold tabular">{rub(b.total)}</span>
                  <ChevronRight className="size-4 text-muted-foreground" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function periodPresets(today: string) {
  const [y, m, d] = today.split("-").map(Number);
  const pad = (n: number) => String(n).padStart(2, "0");
  const first = `${y}-${pad(m)}-01`;
  const mid = `${y}-${pad(m)}-15`;
  const prevEnd = addDays(first, -1);
  const prevMid = `${prevEnd.slice(0, 8)}16`;
  return d > 15
    ? [{ label: "1–15", from: first, to: mid }, { label: "16 – сегодня", from: `${y}-${pad(m)}-16`, to: today }]
    : [{ label: `16–${prevEnd.slice(8)} прошлого`, from: prevMid, to: prevEnd }, { label: "1 – сегодня", from: first, to: today }];
}

function Wizard({ defaults, onClose }: { defaults: PayoutRates; onClose: (id?: number) => void }) {
  const today = moscowToday();
  const presets = periodPresets(today);
  const nets = useLoad(() => api.admin.networks(), []);
  const [step, setStep] = useState(1);
  const [title, setTitle] = useState("Зарплата");
  const [from, setFrom] = useState(presets[0].from);
  const [to, setTo] = useState(presets[0].to);
  const [network, setNetwork] = useState<number | null>(null);
  const [rates, setRates] = useState<PayoutRates>(defaults);
  const [rows, setRows] = useState<PayoutRow[]>([]);
  const [summary, setSummary] = useState<PayoutSummary | null>(null);
  const [include, setInclude] = useState<Record<number, boolean>>({});
  const [override, setOverride] = useState<Record<number, string>>({});
  const [proof, setProof] = useState<string[]>([]);
  const [dedProof, setDedProof] = useState<string[]>([]);
  const { busy, run } = useAction();

  async function calc() {
    try {
      const res = await run("calc", () => api.admin.previewPayout({ date_from: from, date_to: to, network_id: network, rates }));
      if (!res) return;
      setRows(res.rows);
      setSummary(res.summary);
      setInclude(Object.fromEntries(res.rows.map((r) => [r.user_id, r.total > 0 || r.base_calc > 0])));
      setOverride({});
      setStep(2);
    } catch {
      /* toast shown */
    }
  }

  // a bonus, fine or deduction added right in the grid: recalculate, keep the ticks and overrides
  async function refresh() {
    try {
      const res = await api.admin.previewPayout({ date_from: from, date_to: to, network_id: network, rates });
      setRows(res.rows);
      setSummary(res.summary);
      setInclude((cur) => ({ ...Object.fromEntries(res.rows.map((r) => [r.user_id, r.total > 0 || r.base_calc > 0])), ...cur }));
    } catch {
      /* the grid stays as it was */
    }
  }

  // the server recalculates on commit; here an override is shown as base + bonuses − fines − deductions
  const shown = (r: PayoutRow) => {
    const o = override[r.user_id];
    if (o === undefined || o === "") return r.total;
    return Math.max(0, Number(o) + r.bonuses - r.fines - r.deductions);
  };
  const picked = rows.filter((r) => include[r.user_id]);
  const total = picked.reduce((s, r) => s + shown(r), 0);
  const needsDedProof = picked.some((r) => r.deductions > 0);

  async function commit() {
    try {
      const res = await run("commit", (k) => api.admin.commitPayout({
        title, date_from: from, date_to: to, network_id: network, rates,
        choices: rows.map((r) => ({ user_id: r.user_id, include: !!include[r.user_id],
          base_override: override[r.user_id] ? Number(override[r.user_id]) : null })),
        proof, deduction_proof: dedProof,
      }, k), "Выплата зафиксирована");
      if (res) onClose(res.batch.id);
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" className="h-9 rounded-xl" onClick={() => (step > 1 ? setStep(step - 1) : onClose())}>
          <ChevronLeft /> {step > 1 ? "Назад" : "Отмена"}
        </Button>
        <div className="ml-auto flex gap-1.5">
          {[1, 2, 3].map((s) => (
            <span key={s} className={cn("h-1.5 w-8 rounded-full", s <= step ? "bg-primary" : "bg-foreground/12")} />
          ))}
        </div>
      </div>

      {step === 1 && (
        <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
          <h2 className="font-display text-lg font-semibold">1. Период и ставки</h2>
          <label className="block space-y-1.5">
            <span className="text-sm text-muted-foreground">Название</span>
            <input className={field} value={title} onChange={(e) => setTitle(e.target.value.slice(0, 100))} />
          </label>
          <div className="flex flex-wrap gap-2">
            {presets.map((p) => (
              <button key={p.label} onClick={() => { setFrom(p.from); setTo(p.to); }}
                      className={cn("press h-9 rounded-full px-4 text-sm font-semibold", from === p.from && to === p.to ? "bg-primary text-primary-foreground" : "bg-muted")}>
                {p.label}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1.5"><span className="text-sm text-muted-foreground">С</span>
              <input type="date" className={field} value={from} onChange={(e) => setFrom(e.target.value)} /></label>
            <label className="space-y-1.5"><span className="text-sm text-muted-foreground">По</span>
              <input type="date" className={field} value={to} onChange={(e) => setTo(e.target.value)} /></label>
          </div>
          <label className="block space-y-1.5">
            <span className="text-sm text-muted-foreground">Сеть</span>
            <select className={field} value={network ?? ""} onChange={(e) => setNetwork(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Все</option>
              {nets.data?.items.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
            </select>
          </label>
          <div className="grid grid-cols-2 gap-3">
            {([
              ["rate_base", "Общая ставка, ₽ за шт."],
              ["rate_lazy", "Налог на лень, ₽ за шт."],
              ["lazy_threshold", "Налог, если меньше, шт."],
              ["trainer_commission", "Тренеру, ₽ за шт. сети"],
            ] as [keyof PayoutRates, string][]).map(([k, label]) => (
              <label key={k} className="space-y-1.5">
                <span className="text-xs text-muted-foreground">{label}</span>
                <input inputMode="numeric" className={cn(field, "tabular")} value={String(rates[k])}
                       onChange={(e) => setRates({ ...rates, [k]: Number(e.target.value.replace(/\D/g, "") || 0) })} />
              </label>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            Личные ставки сотрудников, ставки и лестницы сетей применяются поверх общих — они настраиваются в «Команде» и «Сетях».
          </p>
          <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy || !title.trim() || !from || !to} onClick={calc}>
            {busy && <LoaderCircle className="animate-spin" />} Рассчитать
          </Button>
        </section>
      )}

      {step === 2 && (
        <>
          <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
            <div className="flex items-center justify-between px-5 pt-5 pb-3 md:px-6">
              <h2 className="font-display text-lg font-semibold">2. Расчёт</h2>
              <span className="text-sm text-muted-foreground">{dayMonth(from)} – {dayMonth(to)}</span>
            </div>
            {rows.length === 0 ? (
              <p className="px-5 pb-5 text-sm text-muted-foreground md:px-6">За период нет принятых отчётов и начислений.</p>
            ) : (
              <ul className="divide-y border-t">{rows.map((r) => (
                <PayoutRowCard key={r.user_id} row={r} on={!!include[r.user_id]} total={shown(r)}
                               override={override[r.user_id] ?? ""}
                               onToggle={() => setInclude({ ...include, [r.user_id]: !include[r.user_id] })}
                               onOverride={(v) => setOverride({ ...override, [r.user_id]: v })} onMoney={refresh} />
              ))}</ul>
            )}
          </section>
          {summary && <OwnerSummary s={summary} picked={total} />}
          <section className="sticky bottom-28 flex items-center justify-between gap-3 rounded-3xl bg-hero p-4 pl-5 text-hero-foreground shadow-lift-strong lg:bottom-4">
            <div>
              <p className="text-xs text-hero-muted">{picked.length} {plural(picked.length, "человек", "человека", "человек")}</p>
              <p className="font-display text-xl font-semibold tabular">{rub(total)}</p>
            </div>
            <Button className="press h-11 rounded-xl bg-white px-5 font-semibold text-hero hover:bg-white/90" disabled={!picked.length} onClick={() => setStep(3)}>
              Далее <ChevronRight />
            </Button>
          </section>
        </>
      )}

      {step === 3 && (
        <section className="space-y-5 rounded-3xl bg-card p-5 shadow-lift md:p-6">
          <h2 className="font-display text-lg font-semibold">3. Документы и фиксация</h2>
          <Uploads title="1. Основание выплаты" hint="Скриншоты переводов, ведомости, приказы" value={proof} onChange={setProof} />
          <Uploads title="2. Основание вычетов" hint="Акты, фото, согласия на удержание" value={dedProof} onChange={setDedProof} />
          {needsDedProof && dedProof.length === 0 && (
            <p className="flex items-start gap-2 rounded-xl bg-wait-soft px-3 py-2.5 text-sm text-wait">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" /> В выплате есть вычеты, но документы по ним не загружены.
            </p>
          )}
          <div className="rounded-2xl bg-muted/60 p-4 text-sm">
            <p className="font-semibold">{title}</p>
            <p className="text-muted-foreground">{picked.length} {plural(picked.length, "человек", "человека", "человек")}, к выплате {rub(total)}</p>
            <p className="mt-1 text-muted-foreground">Штрафы, бонусы и вычеты в выплате получат статус «учтён в выплате». Реестр Excel и список для банка придут вам в бот, сводка — в архив.</p>
          </div>
          <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy} onClick={commit}>
            {busy === "commit" ? <LoaderCircle className="animate-spin" /> : <Check />} Зафиксировать выплату
          </Button>
        </section>
      )}
    </div>
  );
}

function PayoutRowCard({ row, on, total, override, onToggle, onOverride, onMoney }: {
  row: PayoutRow; on: boolean; total: number; override: string; onToggle: () => void; onOverride: (v: string) => void;
  onMoney: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const steps = [...new Set((row.commission_days ?? []).map((d) => d.rate))];
  return (
    <li className={cn("px-5 py-4 md:px-6", !on && "opacity-50")}>
      <div className="flex items-start gap-3">
        <button onClick={onToggle} aria-label={on ? "Убрать из реестра" : "Включить в реестр"}
                className={cn("press mt-0.5 grid size-6 shrink-0 place-items-center rounded-md border-2", on ? "border-primary bg-primary text-primary-foreground" : "border-border")}>
          {on && <Check className="size-4" />}
        </button>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold">{row.name}</p>
          <p className="text-xs text-muted-foreground">
            {ROLE_TITLES[row.role]}{row.network ? ` · ${row.network}` : ""} · {row.personal_volume} шт.
            {row.team_volume ? ` · сеть ${row.team_volume} шт.` : ""}
          </p>
          <p className={cn("text-xs", row.bank ? "text-muted-foreground" : "font-semibold text-wait")}>
            {row.bank ? `${row.bank}, ${row.phone}` : "Реквизиты не заполнены"}
          </p>
          {(row.pieces_pay > 0 || row.commission > 0) && (
            <p className="mt-1 text-xs text-muted-foreground">
              {row.pieces_pay > 0 && (
                <>
                  За штуки {rub(row.pieces_pay)}
                  {row.rate_base != null && ` (${row.full_units ?? row.personal_volume - row.lazy_units} × ${row.rate_base} ₽`}
                  {row.rate_base != null && row.lazy_units > 0 && ` + ${row.lazy_units} × ${row.rate_lazy} ₽ налог на лень`}
                  {row.rate_base != null && ")"}
                </>
              )}
              {row.pieces_pay > 0 && row.commission > 0 && " · "}
              {row.commission > 0 && (
                <span className="font-semibold text-foreground">
                  тренерские {rub(row.commission)} ({row.team_volume} шт. по {steps.join("/")} ₽)
                </span>
              )}
            </p>
          )}
        </div>
        <span className="font-display text-lg font-semibold tabular">{rub(total)}</span>
      </div>
      <div className="mt-3 grid grid-cols-4 gap-2 pl-9 text-xs">
        <label className="space-y-1">
          <span className="text-muted-foreground">База</span>
          <input inputMode="numeric" placeholder={String(row.base_calc)} value={override}
                 onChange={(e) => onOverride(e.target.value.replace(/\D/g, "").slice(0, 8))}
                 className="h-9 w-full rounded-lg border-0 bg-muted px-2 text-sm tabular outline-none focus:ring-2 focus:ring-ring/25" />
        </label>
        <Money label="Бонусы" value={row.bonuses} sign="+" tone="text-go" />
        <Money label="Штрафы" value={row.fines} sign="−" tone="text-cancel" />
        <Money label="Вычеты" value={row.deductions} sign="−" tone="text-final" />
      </div>
      {!!row.lazy_before && (
        <p className="mt-2 pl-9 text-xs text-muted-foreground">
          Копилка налога на лень: <span className="font-semibold text-foreground tabular">{row.lazy_before} → {row.lazy_after ?? row.lazy_before - row.lazy_units} шт.</span>
          {" "}— спишется только при фиксации выплаты.
        </p>
      )}
      {!!row.fine_units && (
        <p className="mt-1 pl-9 text-xs text-muted-foreground">Штрафы: {row.fine_units} ед. = {rub(row.fines)}</p>
      )}
      {row.carried_debt > 0 && (
        <p className="mt-2 pl-9 text-xs text-wait">Не поместилось в выплату: {rub(row.carried_debt)} — останется долгом.</p>
      )}
      <div className="mt-2 pl-9">
        <Button variant="ghost" size="sm" className="press h-8 rounded-lg px-2 text-muted-foreground" onClick={() => setAdding((v) => !v)}>
          {adding ? <X /> : <Plus />} {adding ? "Закрыть" : "Начислить или удержать"}
        </Button>
        {adding && (
          <div className="row-in mt-2 rounded-2xl bg-muted/50 p-4">
            <TxForm userId={row.user_id} onDone={() => { setAdding(false); onMoney(); }} />
          </div>
        )}
      </div>
    </li>
  );
}

/** The owner's side: revenue for every approved piece minus what people get (customer round B). */
function OwnerSummary({ s, picked }: { s: PayoutSummary; picked: number }) {
  if (!s.revenue_per_piece) {
    return (
      <p className="rounded-2xl bg-muted/60 px-4 py-3 text-sm text-muted-foreground">
        Заработок владельца появится здесь, когда в «Настройках → Ставки» указан доход за штуку.
      </p>
    );
  }
  const owner = s.revenue - picked;
  return (
    <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <p className="text-sm text-muted-foreground">Заработок владельца</p>
      <p className={cn("font-display text-2xl font-semibold tabular", owner < 0 && "text-cancel")}>{rub(owner)}</p>
      <dl className="mt-3 space-y-1.5 text-sm tabular">
        <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Выручка</dt>
          <dd className="font-medium">{s.pieces} шт. × {rub(s.revenue_per_piece)} = {rub(s.revenue)}</dd></div>
        <div className="flex justify-between gap-3"><dt className="text-muted-foreground">К выплате людям (отмеченным)</dt>
          <dd className="font-medium">−{rub(picked)}</dd></div>
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">
        По всем строкам: за штуки {rub(s.pieces_pay)}, тренерские {rub(s.commission)}, бонусы {rub(s.bonuses)},
        удержано штрафов и вычетов {rub(s.withheld)}.
      </p>
    </section>
  );
}

function Money({ label, value, sign, tone }: { label: string; value: number; sign: string; tone: string }) {
  return (
    <div className="space-y-1">
      <span className="text-muted-foreground">{label}</span>
      <p className={cn("flex h-9 items-center text-sm font-semibold tabular", value ? tone : "text-muted-foreground")}>
        {value ? `${sign}${rub(value)}` : "—"}
      </p>
    </div>
  );
}

/** Proof photos uploaded to Telegram one by one; the ids go with the form. */
export function Uploads({ title, hint, value, onChange, max = 10 }: {
  title: string; hint: string; value: string[]; onChange: (v: string[]) => void; max?: number;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [loading, setLoading] = useState(0);
  async function pick(files: FileList | null) {
    if (!files) return;
    const list = Array.from(files).slice(0, max - value.length);
    setLoading(list.length);
    const ids: string[] = [];
    for (const f of list) {
      try {
        const res = await api.admin.uploadMedia(await compressImage(f));
        ids.push(res.file_id);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Не удалось загрузить файл");
      }
      setLoading((n) => n - 1);
    }
    if (ids.length) haptic("success");
    onChange([...value, ...ids]);
    if (input.current) input.current.value = "";
  }
  return (
    <div>
      <p className="font-semibold">{title}</p>
      <p className="text-sm text-muted-foreground">{hint}</p>
      <input ref={input} type="file" accept="image/*" multiple className="hidden" onChange={(e) => pick(e.target.files)} />
      <div className="mt-2 flex flex-wrap gap-2">
        {value.map((id, i) => (
          <span key={id} className="flex h-10 items-center gap-1.5 rounded-xl bg-go-soft pr-1 pl-3 text-sm font-semibold text-go">
            Файл {i + 1}
            <button aria-label="Убрать" className="grid size-7 place-items-center rounded-lg hover:bg-black/5" onClick={() => onChange(value.filter((x) => x !== id))}>
              <X className="size-3.5" />
            </button>
          </span>
        ))}
        {value.length < max && (
          <button onClick={() => input.current?.click()} disabled={loading > 0}
                  className="press flex h-10 items-center gap-1.5 rounded-xl border-2 border-dashed px-3 text-sm font-semibold text-muted-foreground">
            {loading ? <LoaderCircle className="size-4 animate-spin" /> : <ImagePlus className="size-4" />} Добавить
          </button>
        )}
      </div>
    </div>
  );
}

function BatchView({ id, onBack }: { id: number; onBack: () => void }) {
  const { data } = useLoad(() => api.admin.payout(id), [id]);
  const { busy, run } = useAction();
  const byBank = useMemo(() => groupByBank(data?.batch), [data]);
  if (!data) return <Skeleton className="h-80 rounded-3xl" />;
  const b = data.batch;
  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" className="h-9 rounded-xl" onClick={onBack}><ChevronLeft /> Все выплаты</Button>
      <section className="rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
        <p className="text-sm text-hero-muted">{dayMonth(b.period_from)} – {dayMonth(b.period_to)}</p>
        <h2 className="font-display text-xl font-semibold">{b.title}</h2>
        <p className="mt-2 font-display text-3xl font-semibold tabular">{rub(b.total)}</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button className="press h-10 rounded-xl bg-white text-hero hover:bg-white/90" disabled={!!busy}
                  onClick={async () => { try { await run("xlsx", () => api.admin.payoutExcel(id), "Реестр отправлен в бот"); } catch { /* toast */ } }}>
            {busy ? <LoaderCircle className="animate-spin" /> : <FileSpreadsheet />} Excel в бот
          </Button>
          <Button className="press h-10 rounded-xl bg-white/10 text-white hover:bg-white/15"
                  onClick={async () => { try { await navigator.clipboard.writeText(data.bank_text); } catch { /* shown */ } haptic("success"); toast.success("Список для банка скопирован"); }}>
            <Copy /> Список для банка
          </Button>
        </div>
      </section>
      {b.summary && b.summary.revenue_per_piece > 0 && <OwnerSummary s={b.summary} picked={b.total} />}
      {byBank.map(([bank, items]) => (
        <section key={bank} className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <div className="flex items-center justify-between px-5 pt-5 pb-2 md:px-6">
            <h3 className="font-semibold">{bank}</h3>
            <span className="text-sm font-semibold tabular">{rub(items.reduce((s, i) => s + i.total, 0))}</span>
          </div>
          <ul className="divide-y">
            {items.map((it) => (
              <li key={it.id} className="flex items-center gap-3 px-5 py-3 md:px-6">
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{it.recipient ?? it.name}</span>
                  <span className="block text-xs text-muted-foreground">
                    {it.phone ?? "нет телефона"} · база {rub(it.base)}{it.bonuses ? ` +${rub(it.bonuses)}` : ""}
                    {it.fines ? ` −${rub(it.fines)}` : ""}{it.deductions ? ` −${rub(it.deductions)} вычет` : ""}
                    {it.lazy_units ? ` · налог на лень ${it.lazy_units} шт. по ${it.rate_lazy} ₽` : ""}
                  </span>
                </span>
                <span className="font-display font-semibold tabular">{rub(it.total)}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function groupByBank(b: PayoutBatch | undefined) {
  if (!b) return [];
  const groups = new Map<string, PayoutBatch["items"]>();
  for (const it of b.items) {
    const key = it.bank ?? "Реквизиты не указаны";
    groups.set(key, [...(groups.get(key) ?? []), it]);
  }
  return [...groups.entries()].sort(([a], [b]) => (a === "Реквизиты не указаны" ? 1 : b === "Реквизиты не указаны" ? -1 : a.localeCompare(b)));
}

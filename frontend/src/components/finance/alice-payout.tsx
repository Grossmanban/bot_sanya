"use client";

import { useState } from "react";
import { ChevronLeft, LoaderCircle, Wallet } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, type AliceReconcile } from "@/lib/api";
import { dayMonth, rub } from "@/lib/format";
import { moscowToday, useAction } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { Uploads } from "./payouts-section";

const field = "h-11 w-full rounded-2xl border-0 bg-muted px-4 outline-none focus:ring-2 focus:ring-ring/25";

/** ТЗ 27.10 §41–44, ТЗ 28.10 §25: an «Алиса» payout in the same registry. Yandex reports a number per QR code for the
 * period; that many of the period's accepted tasks are paid (picked at random, the same on every reopening), the rest
 * of the period become «Отклонено Яндексом». The person's open fines and deductions are taken too (one debt).
 * edit — the same wizard changes an existing payout (a new version, no duplicate). */
export function AlicePayout({ onClose, edit }: { onClose: (id?: number) => void;
  edit?: { id: number; title: string; from: string; to: string; confirmed: Record<string, number> } }) {
  const today = moscowToday();
  const [from, setFrom] = useState(edit?.from ?? `${today.slice(0, 8)}01`);
  const [to, setTo] = useState(edit?.to ?? today);
  const [title, setTitle] = useState(edit?.title ?? "Алиса");
  const [confirmed, setConfirmed] = useState<Record<number, string>>(
    () => Object.fromEntries(Object.entries(edit?.confirmed ?? {}).map(([k, v]) => [Number(k), String(v)])));
  const [plan, setPlan] = useState<AliceReconcile | null>(null);
  const [proof, setProof] = useState<string[]>([]);
  const { busy, run } = useAction();

  const numbers = () => Object.fromEntries(Object.entries(confirmed).filter(([, v]) => v !== "").map(([k, v]) => [Number(k), Number(v)]));

  async function preview() {
    try {
      const body = { date_from: from, date_to: to, confirmed: numbers() };
      const res = await run("calc", () => api.admin.alicePayoutPreview(body));
      if (res) setPlan(res);
    } catch {
      /* toast shown (e.g. more than waiting) */
    }
  }

  async function commit() {
    try {
      const res = edit
        ? await run("commit", (k) => api.admin.editPayout(edit.id, { confirmed: numbers() }, k), "Выплата пересчитана — новая версия")
        : await run("commit", (k) => api.admin.alicePayoutCommit({ title, date_from: from, date_to: to, confirmed: numbers(),
          proof_media: proof }, k), "Выплата зафиксирована");
      if (res) onClose(res.batch.id);
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" className="h-9 rounded-xl" onClick={() => onClose()}><ChevronLeft /> Отмена</Button>
      <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <h2 className="font-display text-xl font-semibold">{edit ? `Изменить выплату №${edit.id}` : "Выплата по «Алисе»"}</h2>
        <p className="text-sm text-muted-foreground">
          Учитываются принятые задания «Алисы», выполненные в выбранный период. Введите, сколько подтвердил отчёт Яндекса по
          каждому QR-коду: столько заданий будут оплачены (выбираются случайно, при повторном открытии — те же), остальные задания
          периода получат «Отклонено Яндексом». QR без числа не трогается. Штрафы и вычеты человека удерживаются — один общий долг,
          без налога на лень. Пока не нажата «Зафиксировать», ничего не меняется.
        </p>
        <label className="block space-y-1.5"><span className="text-sm text-muted-foreground">Название</span>
          <input className={field} value={title} onChange={(e) => setTitle(e.target.value.slice(0, 100))} /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1.5"><span className="text-sm text-muted-foreground">С</span>
            <input type="date" className={field} value={from} disabled={!!edit} onChange={(e) => { setFrom(e.target.value); setPlan(null); }} /></label>
          <label className="space-y-1.5"><span className="text-sm text-muted-foreground">По</span>
            <input type="date" className={field} value={to} disabled={!!edit} onChange={(e) => { setTo(e.target.value); setPlan(null); }} /></label>
        </div>
        <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy} onClick={() => preview()}>
          {busy === "calc" && <LoaderCircle className="animate-spin" />} Показать сверку
        </Button>
      </section>

      {plan && (
        <>
          <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
            <p className="px-5 pt-5 text-sm text-muted-foreground md:px-6">
              Ставка {rub(plan.rate)} за выполнение («Настройки → Ставки»). Период {dayMonth(from)} – {dayMonth(to)}.
            </p>
            {plan.rows.length === 0 ? (
              <p className="p-5 text-sm text-muted-foreground md:px-6">Принятых выполнений нет.</p>
            ) : (
              <ul className="divide-y">
                {plan.rows.map((r) => (
                  <li key={r.qr_id} className="space-y-2 px-5 py-4 md:px-6">
                    <div className="flex items-start justify-between gap-3">
                      <span className="min-w-0">
                        <span className="block font-semibold">QR {r.number ?? r.qr_id}</span>
                        <span className="block text-xs text-muted-foreground">{r.people.map((p) => p.name).join(", ")}</span>
                      </span>
                      <label className="w-28 shrink-0 space-y-1 text-right">
                        <span className="block text-xs text-muted-foreground">Подтвердил Яндекс</span>
                        <input inputMode="numeric" aria-label={`Подтвердил Яндекс, QR ${r.number ?? r.qr_id}`} placeholder="0"
                               value={confirmed[r.qr_id] ?? ""}
                               onChange={(e) => setConfirmed({ ...confirmed, [r.qr_id]: e.target.value.replace(/\D/g, "").slice(0, 5) })}
                               onBlur={() => preview()}
                               className={cn("h-10 w-full rounded-xl border-0 bg-muted px-3 text-right font-semibold tabular outline-none",
                                 Number(confirmed[r.qr_id] || 0) > r.waiting && "ring-2 ring-cancel")} />
                      </label>
                    </div>
                    <dl className="grid grid-cols-4 gap-2 text-xs">
                      <div><dt className="text-muted-foreground">Принято за период</dt><dd className="font-semibold tabular">{r.accepted}</dd></div>
                      <div><dt className="text-muted-foreground">Уже оплачено</dt><dd className="font-semibold tabular">{r.paid}</dd></div>
                      <div><dt className="text-muted-foreground">Ждут оплаты</dt><dd className="font-semibold tabular">{r.waiting}</dd></div>
                      <div><dt className="text-muted-foreground">{r.confirmed == null ? "Без сверки" : "Отклонит Яндекс"}</dt>
                        <dd className="font-semibold tabular">{r.confirmed == null ? r.left : r.yandex_rejected}</dd></div>
                    </dl>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {(plan.pay.length > 0 || plan.rejected.length > 0) && (
            <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
              <h3 className="font-semibold">К выплате</h3>
              <ul className="divide-y text-sm">
                {plan.pay.map((p) => (
                  <li key={p.user_id} className="flex justify-between gap-3 py-2">
                    <span className="min-w-0">
                      {p.name} · {p.count} × {rub(plan.rate)}
                      {(p.fines || p.deductions || p.bonuses) ? (
                        <span className="block text-xs text-muted-foreground">
                          {p.bonuses ? `бонусы +${rub(p.bonuses)} ` : ""}{p.fines ? `штрафы −${rub(p.fines)} ` : ""}{p.deductions ? `вычеты −${rub(p.deductions)}` : ""}
                          {p.carried_debt ? ` · долг остаётся ${rub(p.carried_debt)}` : ""}
                        </span>
                      ) : null}
                    </span>
                    <span className="font-semibold tabular">{rub(p.total)}</span>
                  </li>
                ))}
              </ul>
              {!edit && <Uploads title="Основание выплаты" hint="Отчёт Яндекса, скриншот — до 10 файлов, по желанию" value={proof} onChange={setProof} />}
              <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy || !title.trim()} onClick={commit}>
                {busy === "commit" ? <LoaderCircle className="animate-spin" /> : <Wallet />} {edit ? "Сохранить изменения" : "Зафиксировать"} {rub(plan.total)}
              </Button>
            </section>
          )}
        </>
      )}
    </div>
  );
}

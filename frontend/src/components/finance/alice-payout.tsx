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

/** ТЗ 27.10 §41–44: a «Яндекс Алиса» payout in the same registry. Yandex reports a number per QR code; it is spread
 * over the oldest accepted executions of that QR, the rest keeps waiting. No fines, no «налог на лень». */
export function AlicePayout({ onClose }: { onClose: (id?: number) => void }) {
  const today = moscowToday();
  const [from, setFrom] = useState(`${today.slice(0, 8)}01`);
  const [to, setTo] = useState(today);
  const [title, setTitle] = useState("Яндекс Алиса");
  const [confirmed, setConfirmed] = useState<Record<number, string>>({});
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
      const res = await run("commit", (k) => api.admin.alicePayoutCommit({ title, date_from: from, date_to: to, confirmed: numbers(),
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
        <h2 className="font-display text-xl font-semibold">Выплата по «Яндекс Алисе»</h2>
        <p className="text-sm text-muted-foreground">
          Учитываются только принятые выполнения этого продукта — без штрафов, налога на лень и обычных штук. Введите, сколько
          подтвердил отчёт Яндекса по каждому QR-коду: оплачиваются самые старые принятые, остальные ждут следующей сверки.
          Пока не нажата «Зафиксировать», ничего не меняется.
        </p>
        <label className="block space-y-1.5"><span className="text-sm text-muted-foreground">Название</span>
          <input className={field} value={title} onChange={(e) => setTitle(e.target.value.slice(0, 100))} /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1.5"><span className="text-sm text-muted-foreground">С</span>
            <input type="date" className={field} value={from} onChange={(e) => { setFrom(e.target.value); setPlan(null); }} /></label>
          <label className="space-y-1.5"><span className="text-sm text-muted-foreground">По</span>
            <input type="date" className={field} value={to} onChange={(e) => { setTo(e.target.value); setPlan(null); }} /></label>
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
                      <div><dt className="text-muted-foreground">Останется</dt><dd className="font-semibold tabular">{r.left}</dd></div>
                    </dl>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {plan.pay.length > 0 && (
            <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
              <h3 className="font-semibold">К выплате</h3>
              <ul className="divide-y text-sm">
                {plan.pay.map((p) => (
                  <li key={p.user_id} className="flex justify-between py-2">
                    <span>{p.name} · {p.count} × {rub(plan.rate)}</span>
                    <span className="font-semibold tabular">{rub(p.amount)}</span>
                  </li>
                ))}
              </ul>
              <Uploads title="Основание выплаты" hint="Отчёт Яндекса, скриншот — до 10 файлов, по желанию" value={proof} onChange={setProof} />
              <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy || !title.trim()} onClick={commit}>
                {busy === "commit" ? <LoaderCircle className="animate-spin" /> : <Wallet />} Зафиксировать {rub(plan.total)}
              </Button>
            </section>
          )}
        </>
      )}
    </div>
  );
}

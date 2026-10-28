"use client";

import { useState } from "react";
import { CircleCheck, Clock3, LoaderCircle, Minus, Plus, RotateCcw, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, type Pricing, type Shift } from "@/lib/api";
import { rub } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { PlanFactBar } from "../admin/shortfall";
import { fineShort } from "../finance/money";
import { PhotoPicker, PhotoThumb } from "./photo";

/** "до 00:00" or "до 01:30 (17.10)" when the deadline falls on another day. */
export function deadlineText(iso: string, shiftDate: string): string {
  const time = iso.slice(11, 16);
  const day = iso.slice(0, 10);
  const next = new Date(`${shiftDate}T12:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  const nextDay = next.toISOString().slice(0, 10);
  if (day === shiftDate || (day === nextDay && time === "00:00")) return `до ${time}`;
  return `до ${time} (${day.slice(8, 10)}.${day.slice(5, 7)})`;
}

/** Report on the employee's own shift: the form, or where the report stands (ТЗ 3.2–3.4). */
export function ReportBlock({ shift, pricing, onChanged }: { shift: Shift; pricing: Pricing; onChanged: () => Promise<void> | void }) {
  const report = shift.report;
  const [replacing, setReplacing] = useState(false);
  if (!report || (!report.started && !report.state)) return null;

  const plan = shift.volume ?? 0;
  const last = report.last;
  const state = report.state;

  if (state === null && !report.can_submit) {
    return <Status tone="cancel" icon={ShieldAlert} title="Срок сдачи отчёта прошёл" text="Решение примет администратор." />;
  }
  if (state === null || (state === "needs_fix" && report.can_submit) || (state === "pending_review" && replacing)) {
    return (
      <div className="space-y-3">
        {state === "needs_fix" && (
          <Status tone="wait" icon={RotateCcw} title="Отчёт вернули на исправление"
                  text={report.note ?? "Сдайте отчёт заново."} photo={last?.photo_url} />
        )}
        <ReportForm shift={shift} pricing={pricing} replacing={state === "pending_review"}
                    onCancel={replacing ? () => setReplacing(false) : undefined}
                    onDone={async () => {
                      setReplacing(false);
                      await onChanged();
                    }} />
      </div>
    );
  }

  const fact = last?.fact_volume ?? shift.fact_volume ?? 0;
  if (state === "pending_review") {
    return (
      <Status tone="wait" icon={Clock3} title="Отчёт на проверке" photo={last?.photo_url}
              text={`${fact} из ${plan} шт. ${report.team
                ? "Команда не выполнила общий план — решение за администратором."
                : "Если команда выполнит общий план дня, отчёт примут автоматически."}`}>
        <PlanFactBar plan={plan} fact={fact} />
        {report.can_submit && (
          <Button variant="secondary" className="press h-11 w-full rounded-xl bg-card font-semibold" onClick={() => setReplacing(true)}>
            Заменить отчёт
          </Button>
        )}
      </Status>
    );
  }
  if (state === "approved") {
    const extra = [
      last?.source === "admin" ? "внесено руководителем" : null,
      report.decided === "waived" ? "недобор учтён без штрафа" : null,
    ].filter(Boolean);
    return (
      <Status tone="go" icon={CircleCheck} title="Отчёт принят" photo={last?.photo_url}
              text={`${fact} из ${plan} шт.${shift.fact_pay != null && !shift.training ? `, заработок ${rub(shift.fact_pay)}` : ""}${extra.length ? ` · ${extra.join(", ")}` : ""}`}>
        {fact < plan && <PlanFactBar plan={plan} fact={fact} />}
        {report.note && <p className="text-sm text-muted-foreground">{report.note}</p>}
      </Status>
    );
  }
  if (state === "rejected") {
    return (
      <Status tone="cancel" icon={ShieldAlert} title={`Отчёт отклонён · штраф ${fineShort(report.fine_units, report.fine)}`} photo={last?.photo_url}
              text={report.note ?? "Решение руководителя."}>
        {plan > 0 && <PlanFactBar plan={plan} fact={fact} />}
      </Status>
    );
  }
  // no_show
  return (
    <Status tone="cancel" icon={ShieldAlert} title="Неявка: отчёт не сдан вовремя"
            text={report.decided === "fined"
              ? `Штраф: ${fineShort(report.fine_units, report.fine)}.${report.note ? ` ${report.note}` : ""}`
              : report.decided === "waived"
                ? `Закрыто без штрафа.${report.note ? ` ${report.note}` : ""}`
                : "Решение примет администратор."} />
  );
}

const TONE = {
  go: { box: "bg-go-soft", icon: "text-go" },
  wait: { box: "bg-wait-soft", icon: "text-wait" },
  cancel: { box: "bg-cancel-soft", icon: "text-cancel" },
};

function Status({
  tone,
  icon: Icon,
  title,
  text,
  photo,
  children,
}: {
  tone: keyof typeof TONE;
  icon: typeof Clock3;
  title: string;
  text: string;
  photo?: string | null;
  children?: React.ReactNode;
}) {
  return (
    <div className={cn("row-in space-y-3 rounded-2xl p-4", TONE[tone].box)}>
      <div className="flex items-start gap-3">
        <Icon className={cn("mt-0.5 size-5 shrink-0", TONE[tone].icon)} />
        <div className="min-w-0 flex-1">
          <p className="font-semibold">{title}</p>
          <p className="mt-0.5 text-sm text-foreground/75">{text}</p>
        </div>
        <PhotoThumb src={photo} className="size-14" />
      </div>
      {children}
    </div>
  );
}

function ReportForm({
  shift,
  pricing,
  replacing,
  onCancel,
  onDone,
}: {
  shift: Shift;
  pricing: Pricing;
  replacing: boolean;
  onCancel?: () => void;
  onDone: () => Promise<void> | void;
}) {
  const plan = shift.volume ?? 0;
  const [value, setValue] = useState(String(shift.report?.last?.fact_volume ?? plan));
  const [photo, setPhoto] = useState<Blob | null>(null);
  const { busy, run } = useAction();
  const n = Number(value) || 0;
  const rate = n > 0 && n < pricing.lazy_threshold ? pricing.lazy_rate : pricing.base_rate;
  const ready = /^\d+$/.test(value) && !!photo;

  async function send() {
    if (!ready) return;
    try {
      const res = await run("report", (k) => api.submitReport(shift.id, n, photo!, k),
        n >= plan ? "Отчёт принят" : "Отчёт отправлен на проверку");
      if (res) await onDone();
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="space-y-3 rounded-2xl bg-muted/60 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold">{replacing ? "Новый отчёт" : "Отчёт о смене"}</p>
          <p className="text-sm text-muted-foreground">
            Факт и скриншот результата. План {plan} шт.
            {shift.report?.state !== "needs_fix" && shift.report && `, сдать ${deadlineText(shift.report.deadline, shift.date)}`}
          </p>
        </div>
        {onCancel && (
          <Button variant="ghost" size="sm" className="rounded-xl" onClick={onCancel}>
            Отмена
          </Button>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button type="button" aria-label="Минус 1" onClick={() => setValue(String(Math.max(0, n - 1)))}
                className="press grid size-12 shrink-0 place-items-center rounded-2xl bg-card">
          <Minus className="size-5" />
        </button>
        <input inputMode="numeric" aria-label="Факт, шт." value={value}
               onChange={(e) => setValue(e.target.value.replace(/\D/g, "").slice(0, 5))}
               className="h-12 w-full rounded-2xl border-0 bg-card text-center font-display text-xl font-semibold tabular outline-none focus:ring-2 focus:ring-ring/25" />
        <button type="button" aria-label="Плюс 1" onClick={() => setValue(String(n + 1))}
                className="press grid size-12 shrink-0 place-items-center rounded-2xl bg-card">
          <Plus className="size-5" />
        </button>
      </div>
      {n > 0 && <p className="text-sm text-muted-foreground">{n} × {rate} ₽ = {rub(n * rate)}</p>}
      {value !== "" && n < plan && (
        <p className="rounded-xl bg-cancel-soft px-3 py-2 text-sm text-cancel">
          Меньше плана на {plan - n} шт. Отчёт уйдёт на проверку администратору.
        </p>
      )}
      <PhotoPicker value={photo} onChange={setPhoto} />
      <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy || !ready} onClick={send}>
        {busy && <LoaderCircle className="animate-spin" />}
        {photo ? "Отправить отчёт" : "Прикрепите скриншот"}
      </Button>
    </div>
  );
}

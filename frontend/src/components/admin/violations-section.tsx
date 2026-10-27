"use client";

import { useState } from "react";
import { CalendarX2, CheckCircle2, LoaderCircle, RefreshCw, ShieldCheck, Wallet, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Drawer, DrawerClose, DrawerContent, DrawerDescription, DrawerTitle } from "@/components/ui/drawer";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Violation } from "@/lib/api";
import { useCan } from "@/lib/caps";
import { WEEKDAYS_SHORT, ddmm, parseDay, plural, rub, weekRange } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { tapHaptic } from "@/lib/telegram";
import { useWide } from "@/lib/use-media";
import { cn } from "@/lib/utils";
import { Avatar } from "../avatar";
import { FineUnits } from "../finance/money";
import { Uploads } from "../finance/payouts-section";
import { PhotoThumb } from "../report/photo";

const field = "w-full rounded-2xl border-0 bg-muted px-4 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";
const EXCUSES = ["Больничный лист предоставлен", "Согласовано с руководителем", "Технический простой"];

const days = (n: number) => `${n} ${plural(n, "день", "дня", "дней")}`;
const dayChip = (iso: string) => `${WEEKDAYS_SHORT[parseDay(iso).weekday]} ${ddmm(iso)}`;
/** the violation in a few words: the week's norm, or the one day nobody marked */
const what = (v: Violation) => v.kind === "unmarked"
  ? `не отметил график на ${dayChip(v.week_start)}`
  : `вышел ${v.actual_days} из ${v.required_days}, ${weekRange(v.week_start, v.week_end)}`;

function initials(name: string) {
  const [first = "", last = ""] = name.split(" ");
  return { first_name: first, last_name: last };
}

/** ТЗ 12.4: weeks below the norm of work days — every one gets «штраф» or «оправдание», once. */
export function ViolationsSection({ onChanged }: { onChanged: () => void }) {
  const [tab, setTab] = useState<"pending" | "resolved">("pending");
  const [open, setOpen] = useState<{ v: Violation; mode: "fine" | "waive" } | null>(null);
  const list = useLoad(() => api.admin.violations(tab), [tab]);
  const canScan = useCan("settings");
  const { busy, run } = useAction();

  const changed = async () => {
    setOpen(null);
    await list.reload();
    onChanged();
  };

  return (
    <div className="space-y-4">
      <section className="flex items-start gap-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-primary text-primary-foreground"><CalendarX2 className="size-5" /></span>
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">Нарушения графика</h2>
          <p className="text-sm text-muted-foreground">
            {list.data?.quota
              ? `Норма — ${days(list.data.quota)} в неделю. Каждый понедельник прошлая неделя проверяется автоматически.`
              : "Норма выходов не задана — проверка выключена (Настройки)."}
          </p>
        </div>
        {canScan && !!list.data?.quota && (
          <Button variant="secondary" size="sm" className="press h-9 shrink-0 rounded-xl" disabled={!!busy} aria-label="Проверить прошлую неделю"
                  onClick={async () => {
                    try {
                      const r = await run("scan", () => api.admin.scanViolations());
                      if (r) toast.success(r.created ? `Новых нарушений: ${r.created}` : "Новых нарушений нет");
                      await changed();
                    } catch {
                      /* toast shown */
                    }
                  }}>
            {busy === "scan" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
            <span className="hidden sm:inline">Проверить неделю</span>
          </Button>
        )}
      </section>

      <div className="flex gap-1 rounded-2xl bg-card p-1 shadow-lift">
        {(["pending", "resolved"] as const).map((t) => (
          <button key={t} onClick={() => { tapHaptic(); setTab(t); }}
                  className={cn("press inline-flex h-10 flex-1 items-center justify-center gap-1.5 rounded-xl text-sm font-semibold transition-colors",
                    tab === t ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>
            {t === "pending" ? "Ждут решения" : "Решённые"}
            {t === "pending" && (list.data?.pending ?? 0) > 0 && (
              <span className={cn("inline-grid min-w-5 place-items-center rounded-full px-1.5 text-[11px] leading-5 tabular",
                tab === t ? "bg-white/20" : "bg-cancel text-white")}>{list.data?.pending}</span>
            )}
          </button>
        ))}
      </div>

      {!list.data ? (
        <Skeleton className="h-64 rounded-3xl" />
      ) : list.data.items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">
          {tab === "pending" ? "Все нарушения разобраны." : "Решённых нарушений пока нет."}
        </p>
      ) : (
        list.data.items.map((v) =>
          tab === "pending"
            ? <PendingCard key={v.id} v={v} onPick={(mode) => { tapHaptic(); setOpen({ v, mode }); }} />
            : <ResolvedCard key={v.id} v={v} />,
        )
      )}

      <DecisionDrawer open={open} onClose={() => setOpen(null)} onDone={changed} />
    </div>
  );
}

function Head({ v }: { v: Violation }) {
  return (
    <div className="flex items-start gap-3">
      <Avatar user={initials(v.name)} className="size-11 rounded-2xl text-sm" />
      <div className="min-w-0 flex-1">
        <p className="truncate font-semibold">{v.name}</p>
        <p className="text-sm text-muted-foreground">
          {v.network ? `${v.network} · ` : ""}
          {v.kind === "unmarked" ? `Не отметил график на ${dayChip(v.week_start)}` : `Неделя ${v.week_number}, ${weekRange(v.week_start, v.week_end)}`}
        </p>
      </div>
    </div>
  );
}

function PendingCard({ v, onPick }: { v: Violation; onPick: (mode: "fine" | "waive") => void }) {
  const missed = Math.max(0, v.required_days - v.actual_days);
  return (
    <section className="row-in space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <Head v={v} />
      {v.kind === "unmarked" ? (
        <p className="rounded-2xl bg-cancel-soft px-4 py-3 text-sm text-cancel">
          На {dayChip(v.week_start)} к сроку не было ни смены, ни выходного, хотя статус «Работаю».
        </p>
      ) : (
      <>
      <div className="grid grid-cols-3 gap-2 text-center">
        {[["Требование", days(v.required_days), ""], ["Факт", days(v.actual_days), "text-cancel"],
          ["Пропущено", days(missed), "text-cancel"]].map(([label, value, tone]) => (
          <div key={label} className="rounded-2xl bg-muted/70 px-2 py-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className={cn("font-display text-lg font-semibold tabular", tone)}>{value}</p>
          </div>
        ))}
      </div>
      <div>
        <p className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Не выходил</p>
        <div className="flex flex-wrap gap-1.5">
          {v.absent_dates.map((d) => (
            <span key={d} className="rounded-lg bg-cancel-soft px-2.5 py-1 text-sm font-medium text-cancel tabular">{dayChip(d)}</span>
          ))}
        </div>
      </div>
      </>
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        <Button className="press h-12 rounded-2xl bg-cancel text-[15px] font-semibold text-white hover:bg-cancel/90" onClick={() => onPick("fine")}>
          <Wallet /> Назначить штраф
        </Button>
        <Button variant="secondary" className="press h-12 rounded-2xl bg-go-soft text-[15px] font-semibold text-go hover:bg-go-soft/80"
                onClick={() => onPick("waive")}>
          <ShieldCheck /> Простить
        </Button>
      </div>
    </section>
  );
}

function ResolvedCard({ v }: { v: Violation }) {
  const fined = v.status === "penalized";
  const waiting = v.fine?.status === "pending_admin_approval";
  return (
    <section className="row-in space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1"><Head v={v} /></div>
        <span className={cn("shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold",
          fined ? (waiting ? "bg-wait-soft text-wait" : "bg-cancel-soft text-cancel") : "bg-go-soft text-go")}>
          {fined ? (waiting ? "штраф на согласовании" : `штраф ${rub(v.fine?.amount ?? 0)}`) : "снято"}
        </span>
      </div>
      <p className="text-sm">
        <span className="text-muted-foreground">{what(v).replace(/^./, (c) => c.toUpperCase())}. </span>
        {v.resolution_comment}
      </p>
      {v.media.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {v.media.map((src, i) => <PhotoThumb key={src} src={src} label={`${i + 1}`} className="size-16" />)}
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        {v.resolved_by}{v.resolved_at ? `, ${ddmm(v.resolved_at.slice(0, 10))} в ${v.resolved_at.slice(11, 16)}` : ""}
        {v.fine?.status === "cancelled" ? " · штраф позже аннулирован" : ""}
      </p>
    </section>
  );
}

function DecisionDrawer({ open, onClose, onDone }: {
  open: { v: Violation; mode: "fine" | "waive" } | null; onClose: () => void; onDone: () => void;
}) {
  const wide = useWide();
  return (
    <Drawer open={!!open} onOpenChange={(o) => !o && onClose()} direction={wide ? "right" : "bottom"} repositionInputs={false}>
      <DrawerContent className={cn("border-0 bg-card",
        wide ? "w-[28rem] max-w-[28rem] sm:max-w-[28rem]" : "mx-auto max-h-[92dvh] max-w-2xl rounded-t-[1.75rem]")}>
        {open && (
          <div className={cn("overflow-y-auto px-5", wide ? "h-full py-8" : "pb-safe pt-2")}>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <DrawerTitle className="font-display text-xl font-semibold">
                  {open.mode === "fine" ? "Штраф за пропуск графика" : "Простить нарушение"}
                </DrawerTitle>
                <DrawerDescription>
                  {open.v.name} · {what(open.v)}
                </DrawerDescription>
              </div>
              {wide && (
                <DrawerClose className="grid size-9 place-items-center rounded-full bg-muted" aria-label="Закрыть"><X className="size-4" /></DrawerClose>
              )}
            </div>
            <DecisionForm key={`${open.v.id}-${open.mode}`} v={open.v} mode={open.mode} onDone={onDone} />
          </div>
        )}
      </DrawerContent>
    </Drawer>
  );
}

function DecisionForm({ v, mode, onDone }: { v: Violation; mode: "fine" | "waive"; onDone: () => void }) {
  const fine = mode === "fine";
  const direct = useCan("fine_direct");
  const [amount, setAmount] = useState(fine && v.suggested_units ? String(v.suggested_units) : "");
  const [comment, setComment] = useState(fine ? v.default_comment : "");
  const [files, setFiles] = useState<string[]>([]);
  const [sent, setSent] = useState(false);
  const { busy, run } = useAction();
  const sum = Number(amount);
  // a fine needs its reason; «Простить» — the reason is optional
  const ready = !fine || (comment.trim().length > 0 && Number.isInteger(sum) && sum > 0);

  async function submit() {
    try {
      await run(mode, (k) => fine
        ? api.admin.fineViolation(v.id, { units: sum, comment: comment.trim(), media_files: files }, k)
        : api.admin.waiveViolation(v.id, { comment: comment.trim(), media_files: files }, k),
      fine ? (direct ? "Штраф назначен, сотрудник получил уведомление" : "Штраф отправлен на согласование администраторам")
        : "Нарушение снято, сотрудник получил уведомление");
      setSent(true);  // the buttons stay disabled: one violation, one decision
      onDone();
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="mt-5 space-y-5 pb-4">
      {fine && (
        <div>
          <p className="mb-2 text-sm font-semibold">Штрафные единицы</p>
          <FineUnits value={amount} onChange={(x) => { tapHaptic(); setAmount(x); }} rate={v.unit_rate} />
          {v.suggested_units ? (
            <p className="mt-1.5 text-xs text-muted-foreground">За неотмеченный день по правилам — {v.suggested_units} ед.</p>
          ) : null}
        </div>
      )}

      <div>
        <p className="mb-2 text-sm font-semibold">{fine ? "Причина" : "Почему прощаете (по желанию)"}</p>
        {!fine && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {EXCUSES.map((e) => (
              <button key={e} onClick={() => { tapHaptic(); setComment(e); }}
                      className={cn("press rounded-xl px-3 py-1.5 text-sm", comment === e ? "bg-primary text-primary-foreground" : "bg-muted")}>{e}</button>
            ))}
          </div>
        )}
        <textarea rows={fine ? 4 : 3} className={cn(field, "resize-none py-3 leading-snug")} maxLength={500}
                  placeholder={fine ? "" : "Например: предоставлена справка из больницы, претензий нет"}
                  value={comment} onChange={(e) => setComment(e.target.value)} />
        <p className="mt-1 text-xs text-muted-foreground">
          {fine ? "Обязательно — сотрудник увидит этот текст, копия уйдёт в архив." : "По желанию — если укажете, сотрудник увидит этот текст."}
        </p>
      </div>

      <Uploads title={fine ? "Доказательства" : "Документы"} max={5} value={files} onChange={setFiles}
               hint={fine ? "Переписка, фото прогула — до 5 файлов, по желанию" : "Скриншот больничного, приказ, переписка — до 5 файлов"} />

      {fine && !direct && (
        <p className="rounded-2xl bg-wait-soft p-3 text-sm text-wait">Штраф тренера вступит в силу после согласования администратором.</p>
      )}

      <Button className={cn("press h-12 w-full rounded-2xl text-[15px] font-semibold",
                fine ? "bg-cancel text-white hover:bg-cancel/90" : "bg-go text-white hover:bg-go/90")}
              disabled={!ready || !!busy || sent} onClick={submit}>
        {busy ? <LoaderCircle className="animate-spin" /> : fine ? <Wallet /> : <CheckCircle2 />}
        {fine ? `Подтвердить штраф${sum > 0 ? `: ${sum} ед.${v.unit_rate ? ` = ${rub(sum * v.unit_rate)}` : ""}` : ""}` : "Подтвердить снятие"}
      </Button>
    </div>
  );
}

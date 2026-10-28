"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Clock3, LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Application, TrainingStage } from "@/lib/api";
import { dayMonth } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ReasonChips } from "../reason-chips";

export const field = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

const TONE: Partial<Record<TrainingStage, string>> = {
  new: "bg-muted text-muted-foreground", trainer: "bg-wait-soft text-wait", new_date: "bg-wait-soft text-wait",
  confirm: "bg-wait-soft text-wait", agreed: "bg-go-soft text-go", started: "bg-go-soft text-go",
  internship: "bg-primary/10 text-primary", finish_review: "bg-wait-soft text-wait", done: "bg-go-soft text-go",
  rejected: "bg-cancel-soft text-cancel", returned: "bg-muted text-muted-foreground",
};

/** ТЗ 28.10 §3: the candidate's stage, the same words in «Онбординг» and «Обучение». */
export function StageBadge({ a }: { a: Application }) {
  if (!a.stage_title) return null;
  return (
    <span className={cn("inline-flex max-w-full items-center rounded-full px-2.5 py-1 text-xs font-semibold break-words",
      TONE[a.stage ?? "new"])}>{a.stage_title}</span>
  );
}

/** §2: hours since «свяжитесь с тренером» (or a manual reset) — red after 48 h; never an automatic refusal. */
export function ContactTimer({ since }: { since: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(t);
  }, []);
  const hours = Math.max(0, Math.floor((now - Date.parse(since)) / 3_600_000));
  const late = hours >= 48;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-semibold", late ? "text-cancel" : "text-muted-foreground")}>
      <Clock3 className="size-3.5" /> {hours} ч с сообщения о тренере{late ? " — больше 48 ч" : ""}
    </span>
  );
}

/** §5: «Отмены обучения: 1/3» — dates, who and why on tap. */
export function CancelCounter({ a }: { a: Application }) {
  const [open, setOpen] = useState(false);
  if (!a.cancel_limit) return null;
  const n = a.cancel_count ?? 0;
  return (
    <div className="text-xs">
      <button type="button" onClick={() => setOpen((v) => !v)}
              className={cn("font-semibold underline-offset-2 hover:underline", n >= a.cancel_limit ? "text-cancel" : n ? "text-wait" : "text-muted-foreground")}>
        Отмены обучения: {n}/{a.cancel_limit}
      </button>
      {open && n > 0 && (
        <ul className="mt-1 space-y-0.5 text-muted-foreground">
          {a.cancellations!.map((c, i) => (
            <li key={i}>
              {dayMonth(c.at.slice(0, 10))} · {c.kind === "no_show" ? "неявка" : "отмена"}
              {c.initiator ? ` · ${c.initiator}` : ""}{c.reason ? ` · ${c.reason}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** A required (or optional) reason with the templates of its kind (§2, В2). */
export function ReasonForm({ kind, required = true, tone = "muted", note, confirm, busy, onSave, children }: {
  kind: string; required?: boolean; tone?: "muted" | "cancel"; note?: string; confirm: string; busy: boolean;
  onSave: (reason: string) => void; children?: React.ReactNode;
}) {
  const [reason, setReason] = useState("");
  return (
    <div className={cn("row-in space-y-2 rounded-2xl p-3", tone === "cancel" ? "bg-cancel-soft" : "bg-muted/50")}>
      {children}
      <ReasonChips kind={kind} value={reason} onPick={setReason} />
      <textarea rows={2} className={cn(field, "h-auto resize-none py-2.5")} value={reason}
                placeholder={required ? "Причина (обязательно)" : "Причина (по желанию)"}
                onChange={(e) => setReason(e.target.value.slice(0, 500))} />
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
      <Button className={cn("press h-11 w-full rounded-xl", tone === "cancel" && "bg-cancel text-white hover:bg-cancel/90")}
              disabled={busy || (required && !reason.trim())} onClick={() => onSave(reason.trim())}>
        {busy && <LoaderCircle className="animate-spin" />} {confirm}
      </Button>
    </div>
  );
}

export function CancelWarning({ a, children }: { a: Application; children?: React.ReactNode }) {
  if (!a.cancel_warning) return null;
  return (
    <div className="space-y-2 rounded-2xl bg-cancel-soft p-3">
      <p className="flex items-center gap-2 text-sm font-semibold text-cancel">
        <AlertTriangle className="size-4 shrink-0" /> Отмены обучения: {a.cancel_count}/{a.cancel_limit}
      </p>
      <p className="text-xs text-muted-foreground">Решите: продолжить обучение или отказать кандидату. Автоматического отказа нет.</p>
      {children}
    </div>
  );
}

export function TrainingLine({ a }: { a: Application }) {
  if (!a.training_date) return null;
  return (
    <p className="text-sm">
      📅 {dayMonth(a.training_date)} в {a.training_time}, {a.training_location}
      <span className="block text-xs text-muted-foreground">
        {a.training_confirmed_at ? "кандидат подтвердил" : "кандидат ещё не подтвердил"} ·{" "}
        {a.trainer_confirmed_at ? "тренер подтвердил" : "тренер ещё не подтвердил"}
      </span>
    </p>
  );
}

export function requestLines(r: { kind: string; payload: { date?: string; time?: string; location?: string; note?: string };
  old: { training_date?: string | null; training_time?: string | null; training_location?: string | null }; new_trainer: string | null }) {
  const out: string[] = [];
  if (r.kind !== "change") return out;
  if (r.payload.date) out.push(`Дата: ${r.old.training_date ? dayMonth(r.old.training_date) : "—"} → ${dayMonth(r.payload.date)}`);
  if (r.payload.time) out.push(`Время: ${r.old.training_time ?? "—"} → ${r.payload.time}`);
  if (r.payload.location) out.push(`Место: ${r.old.training_location ?? "—"} → ${r.payload.location}`);
  if (r.new_trainer) out.push(`Новый тренер: ${r.new_trainer}`);
  if (r.payload.note) out.push(`Пожелание: ${r.payload.note}`);
  return out;
}

"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, Clock3, LoaderCircle, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Application, TrainingStage } from "@/lib/api";
import { dayMonth } from "@/lib/format";
import { TONE_CHIP, type Tone } from "@/lib/tone";
import { cn } from "@/lib/utils";
import { ReasonChips } from "../reason-chips";

export const field = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

// §27: the shared scale — waits for someone → yellow, on track / done → green, refusal → red, returned → grey
const TONE: Partial<Record<TrainingStage, Tone>> = {
  new: "wait", trainer: "wait", new_date: "wait", confirm: "wait", finish_review: "wait", cancelled: "bad",
  agreed: "ok", started: "ok", internship: "ok", done: "ok",
  rejected: "bad", returned: "archive",
};

/** ТЗ 28.10 §3: the candidate's stage, the same words in «Онбординг» and «Обучение». */
export function StageBadge({ a }: { a: Application }) {
  if (!a.stage_title) return null;
  return (
    <span className={cn("inline-flex max-w-full items-center rounded-full px-2.5 py-1 text-xs font-semibold break-words",
      TONE_CHIP[TONE[a.stage ?? "new"] ?? "wait"])}>{a.stage_title}</span>
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
      <p className="text-xs text-muted-foreground">Решите: назначить обучение снова или отказать кандидату. Автоматического отказа нет.</p>
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

/** Moscow time of a stored moment: «30.10 в 03:47» (ТЗ 30.10 §7 — never the phone's zone). */
export function mskShort(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const f = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit",
    minute: "2-digit", hourCycle: "h23" }).formatToParts(d);
  const p = (t: string) => f.find((x) => x.type === t)?.value ?? "";
  return `${p("day")}.${p("month")} в ${p("hour")}:${p("minute")}`;
}

/** ТЗ 30.10 §7: when the candidate sent the questionnaire — the server's Moscow time. */
export function Submitted({ a, className }: { a: Application; className?: string }) {
  if (!a.submitted_text) return null;
  return (
    <p className={cn("flex items-center gap-1.5 text-xs text-muted-foreground", className)}>
      <Send className="size-3.5 shrink-0" /> <span>Анкета отправлена: <b className="font-semibold text-foreground">{a.submitted_text}</b></span>
    </p>
  );
}

/** ТЗ 30.10 §4: the green mark — only when the candidate and the trainer both confirmed the current date. */
export const agreedCard = (a: Application) => (a.agreement === "agreed" ? "ring-2 ring-go/70" : "");

export function AgreementMark({ a }: { a: Application }) {
  if (a.agreement === "agreed") {
    return (
      <p className="flex items-center gap-1.5 rounded-xl bg-go-soft px-3 py-1.5 text-sm font-semibold text-go">
        <CheckCircle2 className="size-4 shrink-0" /> Дата согласована обеими сторонами
      </p>
    );
  }
  if (a.status !== "scheduled" || !a.agreement_title) return null;
  return <p className="text-xs font-semibold text-wait">{a.agreement_title}</p>;
}

/** ТЗ 30.10 §5: every attempt of training — trainer, date, confirmations, the cancellation and the statuses. */
export function AttemptsHistory({ a }: { a: Application }) {
  const [open, setOpen] = useState(false);
  const list = a.attempts ?? [];
  if (list.length === 0 || (list.length === 1 && list[0].status === "active" && list[0].events.length === 0)) return null;
  return (
    <div className="text-xs">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
              className="inline-flex items-center gap-1 font-semibold text-muted-foreground underline-offset-2 hover:underline">
        История попыток обучения: {list.length}
        <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ol className="mt-2 space-y-2">
          {list.slice().reverse().map((t) => (
            <li key={t.n} className={cn("space-y-1 rounded-xl p-2.5 break-words", t.status === "cancelled" ? "bg-cancel-soft/60" : "bg-muted/60")}>
              <p className="font-semibold text-foreground">
                Попытка {t.n} · <span className={t.status === "cancelled" ? "text-cancel" : "text-foreground"}>{t.status_title}</span>
              </p>
              <p className="text-muted-foreground">
                Тренер: {t.trainer ?? "—"}
                {t.training_date ? ` · ${dayMonth(t.training_date)} в ${t.training_time ?? "—"}${t.training_location ? `, ${t.training_location}` : ""}` : " · дата не назначалась"}
              </p>
              {t.training_date && (
                <p className="text-muted-foreground">
                  {t.candidate_confirmed_at ? `кандидат подтвердил ${mskShort(t.candidate_confirmed_at)}` : "кандидат не подтвердил"} ·{" "}
                  {t.trainer_confirmed_at ? `тренер подтвердил ${mskShort(t.trainer_confirmed_at)}` : "тренер не подтвердил"}
                </p>
              )}
              {t.status === "cancelled" && (
                <p className="text-muted-foreground">
                  Отменено {mskShort(t.cancelled_at)}{t.initiator ? ` · ${t.initiator}` : ""}{t.reason ? ` · причина: ${t.reason}` : ""}
                </p>
              )}
              {t.events.length > 0 && (
                <ul className="space-y-0.5 border-l-2 border-border pl-2 text-muted-foreground">
                  {t.events.map((e, i) => <li key={i}><span className="tabular">{mskShort(e.at)}</span> — {e.title}</li>)}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

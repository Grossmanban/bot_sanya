"use client";

import { useState } from "react";
import { CircleCheck, Clock3, LoaderCircle, MessageCircle, RotateCcw, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type QueueItem, type QueueKind, type ReportAttempt, type ReportCounts, type ReportState, type TeamDay } from "@/lib/api";
import { useCan } from "@/lib/caps";
import { addDays, dayMonth, rub } from "@/lib/format";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { openChat, tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { deadlineText } from "../report/report-block";
import { PhotoPicker, PhotoThumb } from "../report/photo";
import { PlanFactBar } from "./shortfall";
import { FineUnits, unitsText } from "../finance/money";

const REASONS = {
  fine: ["Не выполнена норма", "Скриншот не подтверждает результат"],
  no_show: ["Не сдан отчёт о смене", "Не вышел на смену"],
  excuse: ["Уважительная причина", "Смена не состоялась"],
};

const TABS: { id: QueueKind; label: string }[] = [
  { id: "review", label: "На проверке" },
  { id: "no_show", label: "Неявки" },
  { id: "missing", label: "Не сдали" },
];

/** What a decision needs to know about a shift's report; built from a queue item or an admin day row. */
export interface ReportSubject {
  shift_id: number;
  date: string;
  volume: number;
  fact_volume: number | null;
  state: ReportState | null;
  decided: "fined" | "waived" | null;
  fine: number | null;
  note: string | null;
  deadline: string;
  attempts: ReportAttempt[];
  team: TeamDay | null;
  fine_block: string | null;
  fine_pending?: number | null;
  /** one fine unit in rubles (the person's own rate); absent where the card doesn't know it */
  unit_rate?: number | null;
}

export function ReportsSection({ onChanged }: { onChanged: () => void }) {
  const [kind, setKind] = useState<QueueKind>("review");
  const [day, setDay] = useState(moscowToday());
  const { data, reload } = useLoad(() => api.admin.reports(kind, kind === "missing" ? day : undefined), [kind, day]);
  const counts: ReportCounts = data?.counts ?? { review: 0, no_show: 0 };

  return (
    <div className="space-y-4">
      <div className="flex gap-1 rounded-2xl bg-card p-1 shadow-lift">
        {TABS.map((t) => {
          const n = t.id === "missing" ? 0 : counts[t.id];
          return (
            <button key={t.id}
                    onClick={() => {
                      tapHaptic();
                      setKind(t.id);
                    }}
                    className={cn("press flex h-10 min-w-0 flex-1 items-center justify-center gap-1.5 rounded-xl px-1 text-[13px] font-semibold whitespace-nowrap transition-colors sm:text-sm",
                      kind === t.id ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>
              {t.label}
              {n > 0 && (
                <span className={cn("inline-grid min-w-5 place-items-center rounded-full px-1.5 text-[11px] leading-5 tabular",
                  kind === t.id ? "bg-white/20" : t.id === "no_show" ? "bg-cancel text-white" : "bg-wait text-white")}>
                  {n}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {kind === "missing" && (
        <div className="flex gap-2">
          {[0, -1].map((d) => {
            const iso = addDays(moscowToday(), d);
            return (
              <button key={d} onClick={() => setDay(iso)}
                      className={cn("press h-10 rounded-full px-4 text-sm font-semibold", day === iso ? "bg-primary text-primary-foreground" : "bg-card shadow-lift")}>
                {d === 0 ? "Сегодня" : "Вчера"}
              </button>
            );
          })}
        </div>
      )}

      {!data ? (
        <Skeleton className="h-56 rounded-3xl" />
      ) : data.items.length === 0 ? (
        <section className="flex items-center gap-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
          <span className="grid size-10 place-items-center rounded-xl bg-go-soft text-go">
            <CircleCheck className="size-5" />
          </span>
          <p className="text-sm text-muted-foreground">
            {kind === "review" ? "Все отчёты проверены. Отчёты с фактом не меньше плана принимаются сами."
              : kind === "no_show" ? "Неявок без решения нет."
                : "Все, кто вышел, отчёт сдали."}
          </p>
        </section>
      ) : (
        <ul className="space-y-3">
          {data.items.map((item) => (
            <ReportCard key={item.shift_id} item={item} onDone={async () => {
              await reload();
              onChanged();
            }} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ReportCard({ item, onDone }: { item: QueueItem; onDone: () => void }) {
  return (
    <li className="row-in space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold">{item.name}</p>
          <p className="text-sm text-muted-foreground">
            {dayMonth(item.date)}, {item.start_time}–{item.end_time}, {item.location}
          </p>
        </div>
        <Button variant="secondary" size="sm" className="press h-9 shrink-0 rounded-xl" onClick={() => openChat(item)}>
          <MessageCircle /> Написать
        </Button>
      </div>
      <ReportDecision subject={{ ...item, volume: item.volume }} onDone={onDone} />
    </li>
  );
}

type Mode = null | "return" | "fine" | "excuse" | "manual";

/** Status, screenshots and the actions available for this report state (ТЗ 3.3–3.4). */
export function ReportDecision({ subject, onDone }: { subject: ReportSubject; onDone: () => void }) {
  // trainers' fines go to an owner for approval (ТЗ 5.3)
  const direct = useCan("fine_direct");
  const request = useCan("fine_request");
  const canFine = direct || request;
  const [mode, setMode] = useState<Mode>(null);
  const { state, attempts } = subject;
  const last = attempts[attempts.length - 1] ?? null;
  const fact = last?.fact_volume ?? subject.fact_volume;
  const noShowOpen = state === "no_show" && !subject.decided;
  // shortfall fines wait for the day's global actual and are off when the team met the plan (ТЗ 3.5)
  const reportFine = canFine && !subject.fine_block;

  const toggle = (m: Mode) => {
    tapHaptic();
    setMode((cur) => (cur === m ? null : m));
  };

  return (
    <div className="space-y-4">
      <StateLine subject={subject} />
      {fact != null && subject.volume > 0 && <PlanFactBar plan={subject.volume} fact={fact} />}
      {(state === "pending_review" || subject.team) && fact != null && fact < subject.volume && <TeamLine team={subject.team} />}

      {attempts.length > 0 && (
        <div className="flex gap-3 overflow-x-auto no-scrollbar">
          {attempts.map((a) => (
            <div key={a.id} className="w-20 shrink-0 space-y-1">
              {a.photo_url ? (
                <PhotoThumb src={a.photo_url} label={attempts.length > 1 ? `#${a.attempt}` : undefined} />
              ) : (
                <div className="grid size-20 place-items-center rounded-xl bg-muted text-center text-[11px] text-muted-foreground">
                  без фото
                </div>
              )}
              <p className="text-xs tabular">{a.fact_volume} шт.</p>
              {a.review_comment && <p className="line-clamp-2 text-[11px] text-muted-foreground">{a.review_comment}</p>}
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {state === "pending_review" && (
          <>
            <Approve shiftId={subject.shift_id} onDone={onDone} />
            <Chip active={mode === "return"} onClick={() => toggle("return")} icon={RotateCcw}>На исправление</Chip>
            {reportFine && <Chip active={mode === "fine"} danger onClick={() => toggle("fine")}>Отклонить + штраф</Chip>}
          </>
        )}
        {noShowOpen && (
          <>
            {canFine && <Chip active={mode === "fine"} danger onClick={() => toggle("fine")}>Штраф</Chip>}
            <Chip active={mode === "excuse"} onClick={() => toggle("excuse")}>Без штрафа</Chip>
          </>
        )}
        {state === "approved" && reportFine && (
          <Chip active={mode === "fine"} danger onClick={() => toggle("fine")}>Отклонить + штраф</Chip>
        )}
        <Chip active={mode === "manual"} onClick={() => toggle("manual")}>
          {state === "approved" ? "Исправить факт" : "Внести результат"}
        </Chip>
      </div>

      {mode === "return" && <ReturnForm shiftId={subject.shift_id} onDone={onDone} />}
      {mode === "fine" && <FineForm subject={subject} onDone={onDone} />}
      {mode === "excuse" && <ExcuseForm shiftId={subject.shift_id} onDone={onDone} />}
      {mode === "manual" && <ManualForm subject={subject} onDone={onDone} />}
    </div>
  );
}

/** The day's global actual next to a below-plan report: without it a shortfall can't be fined. */
function TeamLine({ team }: { team: TeamDay | null }) {
  if (!team) {
    return (
      <p className="rounded-xl bg-muted px-3 py-2 text-sm text-muted-foreground">
        Общий факт дня ещё не введён. Если команда выполнит план, отчёт примут автоматически; штраф за недобор — только после ввода.
      </p>
    );
  }
  return (
    <p className={cn("rounded-xl px-3 py-2 text-sm", team.met ? "bg-go-soft text-go" : "bg-cancel-soft text-cancel")}>
      Общий факт: {team.actual} из {team.plan} шт. — {team.met ? "команда выполнила план" : "план команды не выполнен"}
    </p>
  );
}

function StateLine({ subject }: { subject: ReportSubject }) {
  const { state, decided, fine, note } = subject;
  const [icon, tone, text] = ((): [typeof Clock3, string, string] => {
    switch (state) {
      case "pending_review":
        return [Clock3, "text-wait", "Ждёт проверки"];
      case "approved":
        return [CircleCheck, "text-go", decided === "waived" ? "Принят, недобор без штрафа" : "Принят"];
      case "needs_fix":
        return [RotateCcw, "text-wait", "Вернули на исправление — ждём новый отчёт"];
      case "rejected":
        return [ShieldAlert, "text-cancel", subject.fine_pending ? `Отклонён, штраф ${rub(subject.fine_pending)} на согласовании`
          : fine ? `Отклонён, штраф ${rub(fine)}` : decided === "waived" ? "Отклонён, штраф аннулирован" : "Отклонён"];
      case "no_show":
        return [ShieldAlert, "text-cancel",
          subject.fine_pending ? `Неявка, штраф ${rub(subject.fine_pending)} на согласовании`
            : decided === "fined" ? `Неявка, штраф ${rub(fine ?? 0)}` : decided === "waived" ? "Неявка, без штрафа" : "Неявка: отчёт не сдан"];
      default:
        return [Clock3, "text-muted-foreground", `Отчёт не сдан, срок ${deadlineText(subject.deadline, subject.date)}`];
    }
  })();
  const Icon = icon;
  return (
    <div className="space-y-1">
      <p className={cn("flex items-center gap-2 text-sm font-semibold", tone)}>
        <Icon className="size-4" /> {text}
      </p>
      {note && <p className="text-sm text-muted-foreground">{note}</p>}
    </div>
  );
}

function Chip({ active, danger, icon: Icon, onClick, children }: {
  active: boolean;
  danger?: boolean;
  icon?: typeof Clock3;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button type="button" onClick={onClick}
            className={cn("press inline-flex h-10 items-center gap-1.5 rounded-xl px-4 text-sm font-semibold transition-colors",
              active ? (danger ? "bg-cancel text-white" : "bg-primary text-primary-foreground")
                : danger ? "bg-cancel-soft text-cancel" : "bg-muted")}>
      {Icon && <Icon className="size-4" />}
      {children}
    </button>
  );
}

function Approve({ shiftId, onDone }: { shiftId: number; onDone: () => void }) {
  const { busy, run } = useAction();
  return (
    <Button className="press h-10 rounded-xl bg-go px-4 font-semibold text-white hover:bg-go/90" disabled={!!busy}
            onClick={async () => {
              try {
                await run("approve", (k) => api.admin.review(shiftId, "approve", {}, k), "Отчёт принят");
                onDone();
              } catch {
                /* toast shown */
              }
            }}>
      {busy ? <LoaderCircle className="animate-spin" /> : <CircleCheck />} Принять
    </Button>
  );
}

const area = "w-full resize-none rounded-xl border-0 bg-muted px-3 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

function Reasons({ list, value, onPick }: { list: string[]; value: string; onPick: (r: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {list.map((r) => (
        <button key={r} type="button" onClick={() => onPick(r)}
                className={cn("press rounded-full px-3 py-1.5 text-xs font-semibold", value === r ? "bg-primary text-primary-foreground" : "bg-muted")}>
          {r}
        </button>
      ))}
    </div>
  );
}

function Panel({ children }: { children: React.ReactNode }) {
  return <div className="row-in space-y-3 rounded-2xl border p-4">{children}</div>;
}

function ReturnForm({ shiftId, onDone }: { shiftId: number; onDone: () => void }) {
  const [comment, setComment] = useState("");
  const { busy, run } = useAction();
  return (
    <Panel>
      <p className="text-sm font-semibold">Что исправить? Сотрудник получит комментарий и сдаст отчёт заново.</p>
      <textarea rows={2} autoFocus value={comment} onChange={(e) => setComment(e.target.value.slice(0, 500))}
                placeholder="Например: на скриншоте не видно итог за день" className={area} />
      <Button className="press h-11 w-full rounded-xl font-semibold" disabled={!!busy || !comment.trim()}
              onClick={async () => {
                try {
                  await run("return", (k) => api.admin.review(shiftId, "return", { comment: comment.trim() }, k), "Вернули на исправление");
                  onDone();
                } catch {
                  /* toast shown */
                }
              }}>
        {busy && <LoaderCircle className="animate-spin" />} Вернуть на исправление
      </Button>
    </Panel>
  );
}

function FineForm({ subject, onDone }: { subject: ReportSubject; onDone: () => void }) {
  const noShow = subject.state === "no_show";
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const { busy, run } = useAction();
  const n = Number(amount);
  return (
    <Panel>
      <FineUnits value={amount} onChange={setAmount} rate={subject.unit_rate} tone="soft" />
      <Reasons list={noShow ? REASONS.no_show : REASONS.fine} value={reason} onPick={setReason} />
      <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value.slice(0, 500))} placeholder="Причина штрафа" className={area} />
      <Button className="press h-11 w-full rounded-xl bg-cancel font-semibold text-white hover:bg-cancel/90" disabled={!!busy || !n || !reason.trim()}
              onClick={async () => {
                const extra = { units: n, comment: reason.trim() };
                try {
                  await run("fine", (k) => noShow ? api.admin.noShow(subject.shift_id, "fine", extra, k)
                    : api.admin.review(subject.shift_id, "fine", extra, k), `Штраф назначен: ${unitsText(n)}`);
                  onDone();
                } catch {
                  /* toast shown */
                }
              }}>
        {busy && <LoaderCircle className="animate-spin" />} {n ? `Штраф: ${n} ед.${subject.unit_rate ? ` = ${rub(n * subject.unit_rate)}` : ""}` : "Сколько единиц"}
      </Button>
    </Panel>
  );
}

function ExcuseForm({ shiftId, onDone }: { shiftId: number; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const { busy, run } = useAction();
  return (
    <Panel>
      <Reasons list={REASONS.excuse} value={reason} onPick={setReason} />
      <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value.slice(0, 500))} placeholder="Причина" className={area} />
      <Button className="press h-11 w-full rounded-xl font-semibold" disabled={!!busy || !reason.trim()}
              onClick={async () => {
                try {
                  await run("excuse", (k) => api.admin.noShow(shiftId, "excuse", { comment: reason.trim() }, k), "Закрыто без штрафа");
                  onDone();
                } catch {
                  /* toast shown */
                }
              }}>
        {busy && <LoaderCircle className="animate-spin" />} Закрыть без штрафа
      </Button>
    </Panel>
  );
}

function ManualForm({ subject, onDone }: { subject: ReportSubject; onDone: () => void }) {
  const [value, setValue] = useState(String(subject.fact_volume ?? subject.volume));
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [comment, setComment] = useState("");
  const { busy, run } = useAction();
  const n = Number(value);
  return (
    <Panel>
      <p className="text-sm text-muted-foreground">
        Результат за сотрудника: смена будет принята{subject.state === "rejected" || subject.decided === "fined" ? ", штраф снимется" : ""}.
      </p>
      <label className="flex items-center gap-3">
        <span className="text-sm font-semibold">Факт, шт.</span>
        <input inputMode="numeric" value={value} onChange={(e) => setValue(e.target.value.replace(/\D/g, "").slice(0, 5))}
               className="h-11 w-28 rounded-xl border-0 bg-muted px-3 text-center font-display text-lg font-semibold tabular outline-none focus:ring-2 focus:ring-ring/25" />
        {n < subject.volume && value !== "" && (
          <span className="text-xs text-cancel">меньше плана на {subject.volume - n} шт.</span>
        )}
      </label>
      <PhotoPicker value={photo} onChange={setPhoto} label="Подтверждение" optional />
      <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value.slice(0, 500))}
                placeholder="Комментарий, например «скрин прислал в личку»" className={area} />
      <Button className="press h-11 w-full rounded-xl font-semibold" disabled={!!busy || value === ""}
              onClick={async () => {
                try {
                  await run("manual", (k) => api.admin.manualResult(subject.shift_id, n, comment.trim(), photo, k), "Результат внесён");
                  onDone();
                } catch {
                  /* toast shown */
                }
              }}>
        {busy && <LoaderCircle className="animate-spin" />} Внести и принять
      </Button>
    </Panel>
  );
}

"use client";

import { useEffect, useState } from "react";
import { CalendarCog, CalendarPlus, CalendarX, Check, ClipboardList, GraduationCap, LoaderCircle, Play, Send, UserCog, UserX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Application, type TrainingRequestKind } from "@/lib/api";
import { ddmm, plural } from "@/lib/format";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { openProfile } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { AgreementMark, AttemptsHistory, CancelCounter, ReasonForm, StageBadge, Submitted, TrainingLine, agreedCard, field,
  requestLines } from "./shared";
import { ManualsReader } from "./manuals";

const DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

/** ТЗ 28.10 §3, §12: «Обучение» — the trainer's candidates and/or the manuals given to this person. */
export function TrainingScreen({ trainer, manuals }: { trainer: boolean; manuals: number }) {
  const [part, setPart] = useState<"candidates" | "manuals">(trainer ? "candidates" : "manuals");
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="font-display text-2xl font-semibold">Обучение</h1>
      {trainer && manuals > 0 && (
        <div className="flex gap-1 rounded-2xl bg-card p-1 shadow-lift">
          {([["candidates", "Кандидаты"], ["manuals", "Методички"]] as const).map(([k, label]) => (
            <button key={k} onClick={() => setPart(k)}
                    className={cn("press h-10 flex-1 rounded-xl text-sm font-semibold", part === k ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>
              {label}
            </button>
          ))}
        </div>
      )}
      {part === "manuals" && manuals > 0 ? <ManualsReader /> : trainer ? <Candidates /> : <ManualsReader />}
    </div>
  );
}

function Candidates() {
  const { data, reload } = useLoad(() => api.training(), []);
  // ТЗ 30.10 §4: the order follows the candidates' confirmations without a manual reload
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void reload();
    }, 30_000);
    return () => window.clearInterval(id);
  }, [reload]);
  if (!data) return <Skeleton className="h-64 rounded-3xl" />;
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Ваши кандидаты. Дату и время обучения вы назначаете и меняете сами — кандидат и владелец получат уведомление.
        Отменить обучение можно сразу, с причиной. Начать обучение, стажировку, отказ и передачу другому тренеру — запросом:
        решает администратор. Сверху — те, у кого дату подтвердили и вы, и кандидат.
      </p>
      {data.items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift">Сейчас за вами нет кандидатов.</p>
      ) : (
        <ul className="space-y-3">
          {data.items.map((a) => <Card key={a.id} a={a} trainers={data.trainers} points={data.points} onDone={reload} />)}
        </ul>
      )}
    </div>
  );
}

type Panel = null | "change" | "cancel" | "refuse" | "form" | "date";

function Card({ a, trainers, points, onDone }: { a: Application; trainers: { id: number; name: string }[]; points: string[]; onDone: () => void }) {
  const [panel, setPanel] = useState<Panel>(null);
  const { busy, run } = useAction();
  const pending = new Set(a.requests?.map((r) => r.kind) ?? []);
  const ask = async (kind: TrainingRequestKind, body: { reason?: string; date?: string; time?: string; location?: string; trainer_id?: number } = {}) => {
    try {
      if (await run(kind, (k) => api.trainingRequest(a.id, { kind, ...body }, k), "Запрос отправлен администратору")) {
        setPanel(null);
        onDone();
      }
    } catch {
      /* toast shown */
    }
  };
  const simple = async (name: string, fn: (k: string) => Promise<unknown>, ok: string, close = false) => {
    try {
      if (await run(name, fn, ok)) {
        if (close) setPanel(null);
        onDone();
      }
    } catch {
      /* toast shown */
    }
  };
  const scheduled = a.status === "scheduled";
  const toggle = (x: Panel) => setPanel(panel === x ? null : x);

  return (
    <li className={cn("row-in space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6", agreedCard(a))}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold break-words">{a.full_name}</p>
          <p className="text-sm text-muted-foreground">{a.age} {plural(a.age, "год", "года", "лет")} · м. {a.metro}</p>
          <Submitted a={a} className="mt-0.5" />
          <div className="mt-2"><StageBadge a={a} /></div>
        </div>
        <Button variant="secondary" size="sm" className="h-9 shrink-0 rounded-xl" onClick={() => openProfile(a.username, a.telegram_id, a.full_name)}>
          <Send /> Профиль
        </Button>
      </div>
      <TrainingLine a={a} />
      <AgreementMark a={a} />
      {scheduled && <CancelCounter a={a} />}
      <AttemptsHistory a={a} />
      {(a.requests?.length ?? 0) > 0 && (
        <div className="space-y-1 rounded-2xl bg-wait-soft px-4 py-2.5 text-sm text-wait">
          {a.requests!.map((r) => (
            <p key={r.id}>⏳ {r.title} — ждёт решения администратора{requestLines(r).length ? `: ${requestLines(r).join("; ")}` : ""}</p>
          ))}
        </div>
      )}

      <div className="-mx-1 flex flex-wrap gap-1.5">
        <Button variant="ghost" size="sm" className="h-9 rounded-xl" onClick={() => toggle("form")}><ClipboardList /> Анкета</Button>
        {scheduled && a.training_date && !a.trainer_confirmed_at && (
          <Button size="sm" className="h-9 rounded-xl bg-go text-white hover:bg-go/90" disabled={!!busy}
                  onClick={() => simple("confirm", (k) => api.trainingConfirm(a.id, k), "Подтверждено")}>
            <Check /> Подтвердить дату и место
          </Button>
        )}
        {scheduled && !a.training_started_at && (
          <Button size="sm" className="h-9 rounded-xl" disabled={!!busy || pending.has("start")} onClick={() => ask("start")}>
            {busy === "start" ? <LoaderCircle className="animate-spin" /> : <Play />} Начать обучение
          </Button>
        )}
        {scheduled && (
          <Button size="sm" variant={a.training_date ? "secondary" : "default"} className="h-9 rounded-xl" onClick={() => toggle("date")}>
            {a.training_date ? <CalendarCog /> : <CalendarPlus />} {a.training_date ? "Изменить дату" : "Назначить дату"}
          </Button>
        )}
        {scheduled && (
          <Button size="sm" variant="ghost" className="h-9 rounded-xl" disabled={pending.has("change")} onClick={() => toggle("change")}>
            <UserCog /> Передать другому тренеру
          </Button>
        )}
        {scheduled && (
          <Button size="sm" variant="secondary" className="h-9 rounded-xl" disabled={!!busy || pending.has("internship")} onClick={() => ask("internship")}>
            <GraduationCap /> Начать стажировку
          </Button>
        )}
        {a.status === "internship" && !a.finish_requested_at && (
          <Button size="sm" className="h-9 rounded-xl bg-go text-white hover:bg-go/90" disabled={!!busy}
                  onClick={() => simple("finish", (k) => api.trainingFinish(a.id, k), "Отправлено на проверку администратору")}>
            <Check /> Стажировка завершена
          </Button>
        )}
        {scheduled && (
          <Button size="sm" variant="secondary" className="h-9 rounded-xl" onClick={() => toggle("cancel")}>
            <CalendarX /> Отменить обучение
          </Button>
        )}
        <Button size="sm" variant="ghost" className="h-9 rounded-xl text-cancel hover:bg-cancel-soft hover:text-cancel"
                disabled={pending.has("refuse")} onClick={() => toggle("refuse")}>
          <UserX /> Отказать в обучении
        </Button>
      </div>

      {panel === "form" && (
        <dl className="row-in grid grid-cols-2 gap-x-4 gap-y-2 rounded-2xl bg-muted/50 p-3 text-sm">
          <div><dt className="text-xs text-muted-foreground">Возраст</dt><dd>{a.age}</dd></div>
          <div><dt className="text-xs text-muted-foreground">Метро</dt><dd>{a.metro}</dd></div>
          <div><dt className="text-xs text-muted-foreground">Дней в неделю</dt><dd>от {a.min_days}</dd></div>
          <div><dt className="text-xs text-muted-foreground">Навыки общения</dt><dd>{a.communication}/10</dd></div>
          <div className="col-span-2"><dt className="text-xs text-muted-foreground">Удобные дни для обучения</dt>
            <dd>{a.training_days.map((d) => DAYS[d]).join(", ")}</dd></div>
          <div className="col-span-2"><dt className="text-xs text-muted-foreground">Анкета отправлена</dt>
            <dd>{a.submitted_text ?? ddmm(a.created_at.slice(0, 10))}</dd></div>
        </dl>
      )}
      {panel === "date" && (
        <DateForm a={a} points={points} busy={!!busy}
                  onSave={(body) => simple("date", (k) => api.trainingSchedule(a.id, body, k),
                    a.training_date ? "Дата изменена — кандидат и владелец получили уведомление" : "Дата назначена — кандидат и владелец получили уведомление",
                    true)} />
      )}
      {panel === "change" && <ChangeForm trainers={trainers} busy={!!busy} onSave={(body) => ask("change", body)} />}
      {panel === "cancel" && (
        <CancelDirect a={a} busy={!!busy} onCancel={() => setPanel(null)}
                      onConfirm={(reason) => simple("cancel", (k) => api.trainingCancel(a.id, reason, k),
                        "Обучение отменено: кандидат вернулся в «Ожидают рассмотрения»", true)} />
      )}
      {panel === "refuse" && (
        <ReasonForm kind="reject_candidate" tone="cancel" busy={!!busy} confirm="Отправить запрос на отказ"
                    note="До решения администратора кандидат остаётся в обучении."
                    onSave={(reason) => ask("refuse", { reason })} />
      )}
    </li>
  );
}

/** ТЗ 30.10 §2: the date and time — set by the trainer directly, with the same date / time / place picker as an admin's. */
function DateForm({ a, points, busy, onSave }: { a: Application; points: string[]; busy: boolean;
  onSave: (body: { date: string; time: string; location: string }) => void }) {
  const [date, setDate] = useState(a.training_date ?? moscowToday());
  const [time, setTime] = useState(a.training_time ?? "11:00");
  const [location, setLocation] = useState(a.training_location ?? points[0] ?? "");
  const same = date === a.training_date && time === a.training_time && location.trim() === a.training_location;
  return (
    <div className="row-in space-y-2 rounded-2xl bg-muted/50 p-3">
      <div className="grid grid-cols-2 gap-2">
        <label className="min-w-0 space-y-1"><span className="text-xs text-muted-foreground">Дата</span>
          <input type="date" className={cn(field, "min-w-0")} min={moscowToday()} value={date} onChange={(e) => setDate(e.target.value)} /></label>
        <label className="min-w-0 space-y-1"><span className="text-xs text-muted-foreground">Время</span>
          <input type="time" className={cn(field, "min-w-0")} value={time} onChange={(e) => e.target.value && setTime(e.target.value)} /></label>
      </div>
      {points.length > 0 ? (
        <select className={field} aria-label="Место" value={location} onChange={(e) => setLocation(e.target.value)}>
          {location && !points.includes(location) && <option value={location}>{location}</option>}
          {points.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      ) : <input className={field} placeholder="Место" value={location} onChange={(e) => setLocation(e.target.value.slice(0, 100))} />}
      <Button className="press h-11 w-full rounded-xl" disabled={busy || !date || !time || !location.trim() || same}
              onClick={() => onSave({ date, time, location: location.trim() })}>
        {busy && <LoaderCircle className="animate-spin" />} {a.training_date ? "Сохранить новую дату" : "Назначить дату"}
      </Button>
      <p className="text-xs text-muted-foreground">
        Кандидат получит дату и время с кнопкой «Подтвердить запись», владелец — уведомление{a.training_date ? " с прежней и новой датой" : ""}.
        Ваше подтверждение ставится сразу. Перенос — не отмена.
      </p>
    </div>
  );
}

/** ТЗ 30.10 §5: a reason, then an explicit confirmation — the cancellation is applied at once. */
function CancelDirect({ a, busy, onConfirm, onCancel }: { a: Application; busy: boolean; onConfirm: (reason: string) => void; onCancel: () => void }) {
  const [reason, setReason] = useState<string | null>(null);
  if (reason === null) {
    return (
      <ReasonForm kind="cancel_training" busy={busy} confirm="Далее"
                  note="После подтверждения попытка обучения получит статус «Отменено», кандидат уйдёт из вашего списка и вернётся в «Ожидают рассмотрения». Счётчик отмен увеличится на 1."
                  onSave={setReason} />
    );
  }
  return (
    <div className="row-in space-y-2 rounded-2xl bg-cancel-soft p-3">
      <p className="text-sm font-semibold">Отменить обучение кандидата {a.full_name}?</p>
      <p className="text-sm break-words">Причина: {reason}</p>
      <p className="text-xs text-muted-foreground">
        Кандидат, вы и владелец получите уведомление. Отмены обучения станут: {(a.cancel_count ?? 0) + 1}{a.cancel_limit ? `/${a.cancel_limit}` : ""}.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <Button variant="secondary" className="press h-11 rounded-xl" disabled={busy} onClick={() => (busy ? null : setReason(null))}>Назад</Button>
        <Button className="press h-11 rounded-xl bg-cancel text-white hover:bg-cancel/90" disabled={busy} onClick={() => onConfirm(reason)}>
          {busy && <LoaderCircle className="animate-spin" />} Да, отменить
        </Button>
      </div>
      <button type="button" className="text-xs text-muted-foreground underline-offset-2 hover:underline" onClick={onCancel}>Не отменять</button>
    </div>
  );
}

/** Another trainer — still a request: the admin decides who trains the candidate. */
function ChangeForm({ trainers, busy, onSave }: { trainers: { id: number; name: string }[]; busy: boolean;
  onSave: (body: { reason: string; trainer_id: number }) => void }) {
  const [trainer, setTrainer] = useState<number | null>(null);
  return (
    <ReasonForm kind="change_training" busy={busy} confirm="Отправить запрос администратору"
                note={trainer ? "До решения администратора кандидат остаётся у вас." : "Выберите тренера."}
                onSave={(reason) => trainer && onSave({ reason, trainer_id: trainer })}>
      <select className={cn(field)} aria-label="Другой тренер" value={trainer ?? ""} onChange={(e) => setTrainer(e.target.value ? Number(e.target.value) : null)}>
        <option value="">Кому передать</option>
        {trainers.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
    </ReasonForm>
  );
}

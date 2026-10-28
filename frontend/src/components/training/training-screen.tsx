"use client";

import { useState } from "react";
import { CalendarCog, CalendarX, Check, ClipboardList, GraduationCap, LoaderCircle, Play, Send, UserX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Application, type TrainingRequestKind } from "@/lib/api";
import { ddmm, plural } from "@/lib/format";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { openProfile } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { CancelCounter, ReasonForm, StageBadge, TrainingLine, field, requestLines } from "./shared";

const DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

/** ТЗ 28.10 §3–§4: «Обучение» — the candidates this person trains, their stage, and requests to the admins. */
export function TrainingScreen() {
  const { data, reload } = useLoad(() => api.training(), []);
  if (!data) return <Skeleton className="mx-auto h-64 max-w-2xl rounded-3xl" />;
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="font-display text-2xl font-semibold">Обучение</h1>
      <p className="text-sm text-muted-foreground">
        Ваши кандидаты. Начать, отменить, перенести обучение или отказать — запросом: решение принимает администратор,
        до решения всё остаётся как было.
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

type Panel = null | "change" | "cancel" | "refuse" | "form";

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
  const simple = async (name: string, fn: (k: string) => Promise<unknown>, ok: string) => {
    try {
      if (await run(name, fn, ok)) onDone();
    } catch {
      /* toast shown */
    }
  };
  const scheduled = a.status === "scheduled";
  const toggle = (x: Panel) => setPanel(panel === x ? null : x);

  return (
    <li className="row-in space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold">{a.full_name}</p>
          <p className="text-sm text-muted-foreground">{a.age} {plural(a.age, "год", "года", "лет")} · м. {a.metro}</p>
          <div className="mt-2"><StageBadge a={a} /></div>
        </div>
        <Button variant="secondary" size="sm" className="h-9 shrink-0 rounded-xl" onClick={() => openProfile(a.username, a.telegram_id, a.full_name)}>
          <Send /> Профиль
        </Button>
      </div>
      <TrainingLine a={a} />
      {scheduled && <CancelCounter a={a} />}
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
          <Button size="sm" variant="secondary" className="h-9 rounded-xl" disabled={pending.has("change")} onClick={() => toggle("change")}>
            <CalendarCog /> Изменить обучение
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
        {scheduled && a.training_date && (
          <Button size="sm" variant="secondary" className="h-9 rounded-xl" disabled={pending.has("cancel")} onClick={() => toggle("cancel")}>
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
          <div className="col-span-2"><dt className="text-xs text-muted-foreground">Анкета</dt><dd>{ddmm(a.created_at.slice(0, 10))}</dd></div>
        </dl>
      )}
      {panel === "change" && <ChangeForm a={a} trainers={trainers} points={points} busy={!!busy}
                                         onSave={(body) => ask("change", body)} />}
      {panel === "cancel" && (
        <ReasonForm kind="cancel_training" busy={!!busy} confirm="Отправить запрос на отмену"
                    note="Отмена — не отказ: кандидат останется у вас. Администратор одобрит или отклонит."
                    onSave={(reason) => ask("cancel", { reason })} />
      )}
      {panel === "refuse" && (
        <ReasonForm kind="reject_candidate" tone="cancel" busy={!!busy} confirm="Отправить запрос на отказ"
                    note="До решения администратора кандидат остаётся в обучении."
                    onSave={(reason) => ask("refuse", { reason })} />
      )}
    </li>
  );
}

function ChangeForm({ a, trainers, points, busy, onSave }: { a: Application; trainers: { id: number; name: string }[]; points: string[];
  busy: boolean; onSave: (body: { reason: string; date?: string; time?: string; location?: string; trainer_id?: number }) => void }) {
  const [date, setDate] = useState(a.training_date ?? "");
  const [time, setTime] = useState(a.training_time ?? "");
  const [location, setLocation] = useState(a.training_location ?? "");
  const [trainer, setTrainer] = useState<number | null>(null);
  const changes = {
    ...(date && date !== a.training_date ? { date } : {}),
    ...(time && time !== a.training_time ? { time } : {}),
    ...(location.trim() && location !== a.training_location ? { location: location.trim() } : {}),
    ...(trainer ? { trainer_id: trainer } : {}),
  };
  const nothing = Object.keys(changes).length === 0;
  return (
    <ReasonForm kind="change_training" busy={busy} confirm="Отправить запрос на изменение"
                note={nothing ? "Укажите, что изменить." : "До решения администратора действуют прежние дата, время и место."}
                onSave={(reason) => !nothing && onSave({ reason, ...changes })}>
      <div className="grid grid-cols-2 gap-2">
        <input type="date" aria-label="Дата" className={field} min={moscowToday()} value={date} onChange={(e) => setDate(e.target.value)} />
        <input type="time" aria-label="Время" className={field} value={time} onChange={(e) => setTime(e.target.value)} />
      </div>
      {points.length > 0 ? (
        <select className={field} aria-label="Место" value={location} onChange={(e) => setLocation(e.target.value)}>
          <option value="">Место — без изменений</option>
          {points.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      ) : <input className={field} placeholder="Место" value={location} onChange={(e) => setLocation(e.target.value.slice(0, 100))} />}
      <select className={cn(field)} aria-label="Другой тренер" value={trainer ?? ""} onChange={(e) => setTrainer(e.target.value ? Number(e.target.value) : null)}>
        <option value="">Тренер — без изменений</option>
        {trainers.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
    </ReasonForm>
  );
}

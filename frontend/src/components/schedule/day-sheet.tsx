"use client";

import { useEffect, useState } from "react";
import { Clock3, LoaderCircle, MapPin } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Drawer, DrawerContent, DrawerDescription, DrawerTitle } from "@/components/ui/drawer";
import { api, type Day, type Point, type Pricing, type Shift, type User } from "@/lib/api";
import { STATUS_META, WEEKDAYS, dayMonth, dayState, parseDay, rub, timeRange } from "@/lib/format";
import { readLocal, useAction, writeLocal } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { DayLanes, type Lane } from "../day-line";
import { initialForm, prefillFrom, ShiftForm, validateForm, type FormState } from "./shift-form";
import { ReportBlock } from "../report/report-block";

type Mode = "view" | "create" | "edit" | "restore" | "contact";

export interface DetailProps {
  user: User;
  pricing: Pricing;
  locations: string[];
  points: Point[];
  onChanged: () => Promise<void> | void;
  onOpenProfile: () => void;
}

/** «22:00» out of the deadline's ISO. */
function deadlineTime(iso: string | undefined) {
  return iso ? iso.slice(11, 16) : "22:00";
}

/** How long until a moment, ticking every 30 s: «3 ч 12 мин», «12 мин». */
function useLeft(iso: string | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  if (!iso) return null;
  const left = Date.parse(iso) - now;
  if (left <= 0) return null;
  const h = Math.floor(left / 3_600_000);
  const m = Math.max(1, Math.floor((left % 3_600_000) / 60_000));
  const d = Math.floor(h / 24);
  return d > 0 ? `${d} д ${h % 24} ч` : h > 0 ? `${h} ч ${m} мин` : `${m} мин`;
}

/** ТЗ 27.10 §16: what can still be changed and the timer to the next limit. */
function DeadlineNote({ day, shift }: { day: Day; shift: Shift | null }) {
  const midnight = `${day.date}T00:00:00+03:00`;
  const toDeadline = useLeft(day.deadline);  // the server sends it with the Moscow offset
  const toMidnight = useLeft(midnight);
  if (day.is_past || !shift || !["active", "restored", "cancelled", "pre_declared_off"].includes(shift.status)) return null;
  if (shift.report_state) return null;
  if (day.phase === "open" && toDeadline) {
    return (
      <p className="flex items-center gap-2 rounded-2xl bg-muted/60 px-4 py-3 text-sm">
        <Clock3 className="size-4 shrink-0 text-muted-foreground" />
        <span>Менять всё и брать выходной можно до {deadlineTime(day.deadline)} накануне — осталось <b className="tabular">{toDeadline}</b>.</span>
      </p>
    );
  }
  if (day.phase === "late" && toMidnight) {
    return (
      <p className="flex items-center gap-2 rounded-2xl bg-wait-soft px-4 py-3 text-sm">
        <Clock3 className="size-4 shrink-0 text-wait" />
        <span>Объём и статус зафиксированы. Время и точку можно менять до 00:00 — осталось <b className="tabular">{toMidnight}</b>.</span>
      </p>
    );
  }
  if (day.phase === "started") {
    return <p className="rounded-2xl bg-muted/60 px-4 py-3 text-sm text-muted-foreground">День смены наступил — изменить смену можно только через администратора.</p>;
  }
  return null;
}

/** Phones and iPad portrait: the day opens in a bottom sheet. */
export function DaySheet({ day, onClose, ...props }: DetailProps & { day: Day | null; onClose: () => void }) {
  return (
    <Drawer open={!!day} onOpenChange={(o) => !o && onClose()} repositionInputs={false}>
      <DrawerContent className="mx-auto max-h-[92dvh] max-w-2xl rounded-t-[1.75rem] border-0 bg-card">
        {day && (
          <>
            <DrawerTitle className="sr-only">{WEEKDAYS[parseDay(day.date).weekday]}</DrawerTitle>
            <DrawerDescription className="sr-only">{dayMonth(day.date)}</DrawerDescription>
            <div className="pb-safe overflow-y-auto px-5 pt-2">
              <DayDetail day={day} {...props} />
            </div>
          </>
        )}
      </DrawerContent>
    </Drawer>
  );
}

/** Everything about one day: who works when, my shift and its actions. */
export function DayDetail({ day, user, pricing, locations, points, onChanged, onOpenProfile }: DetailProps & { day: Day }) {
  const shift = day.my_shift;
  const [mode, setMode] = useState<Mode>(shift ? "view" : "create");
  const [form, setForm] = useState<FormState>(() => initialForm(null, readLocal("last-location") ?? undefined));
  const [confirm, setConfirm] = useState<null | "cancel">(null);
  const [message, setMessage] = useState("");
  const { busy, run } = useAction();

  // reset when the shift changes state underneath (after an action or a poll)
  const stateKey = `${day.date}:${shift?.id ?? "none"}:${shift?.status ?? "none"}`;
  useEffect(() => {
    setMode(shift ? "view" : "create");
    setForm(initialForm(null, readLocal("last-location") ?? undefined));
    setConfirm(null);
  }, [stateKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const working = user.work_status === "working";
  const state = dayState(shift);
  const meta = STATUS_META[state];
  // after 22:00 the day before there's no booking for tomorrow (from 00:00 — an urgent one for today)
  const closed = !shift && day.phase === "late";
  const formMode = mode !== "view" && mode !== "contact" && !(mode === "create" && closed);
  const blocked = !working && mode !== "edit";
  // 22:00 the day before has passed: the volume stays, only the time and the point change
  const volumeLocked = mode === "edit" && !!shift && !shift.volume_open;

  const { weekday } = parseDay(day.date);

  async function submit() {
    const payload = validateForm(form);
    if (typeof payload === "string") {
      toast.error(payload);
      return;
    }
    writeLocal("last-location", payload.location);
    try {
      if (mode === "create") {
        const res = await run("create", (k) => api.create(day.date, payload, k));
        if (res?.shift?.status === "awaiting_approval") toast("Заявка на сегодня у администратора");
        else toast.success("Ты в графике");
      } else if (mode === "edit" && shift) {
        if (await run("edit", (k) => api.edit(shift.id, payload, k), "Смена изменена")) setMode("view");

      } else if (mode === "restore" && shift) {
        await run("restore", (k) => api.restore(shift.id, payload, k), "Смена восстановлена");
      }
      await onChanged();
    } catch {
      /* toast shown by useAction */
    }
  }

  async function act(name: string, fn: (key: string) => Promise<unknown>, success: string) {
    try {
      await run(name, fn, success);
      await onChanged();
    } catch {
      /* toast shown */
    }
  }

  // the day at a glance: me first, then colleagues
  const lanes: Lane[] = [];
  if (formMode) {
    lanes.push({ key: "me", name: "Ты", start_time: form.start_time, end_time: form.end_time, state: "none", highlight: true });
  } else if (shift) {
    lanes.push({ key: "me", name: "Ты", start_time: shift.start_time, end_time: shift.end_time, state, highlight: true });
  }
  day.colleagues.forEach((c, i) =>
    lanes.push({ key: `c${i}`, name: c.name, start_time: c.start_time, end_time: c.end_time, state: "active", caption: c.location }),
  );
  day.colleagues_off.forEach((c, i) => lanes.push({ key: `o${i}`, name: c.name, start_time: null, end_time: null, state: "pre_declared_off" }));

  const submitLabel = () => {
    const range = form.start_time && form.end_time ? ` ${form.start_time}–${form.end_time}` : "";
    if (mode === "edit") return "Сохранить изменения";
    if (mode === "restore") return `Вернуться в график${range}`;
    return day.is_today ? `Попроситься на сегодня${range}` : `Выйду${range}`;
  };

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl font-semibold">{WEEKDAYS[weekday]}</h2>
          <p className="mt-0.5 text-muted-foreground">
            {dayMonth(day.date)}
            {day.is_today ? ", сегодня" : ""}
          </p>
        </div>
        {shift && (
          <span key={state} className={cn("pop mt-1 flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold", meta.soft, meta.text)}>
            <span className={cn("size-1.5 rounded-full", meta.dot)} />
            {meta.label}
          </span>
        )}
      </header>

      {lanes.length > 0 && (
        <div className="rounded-2xl bg-muted/60 p-3.5">
          <DayLanes lanes={lanes} />
        </div>
      )}

      {formMode ? (
        <div className="space-y-4">
          {mode === "edit" && shift && !shift.volume_open && (
            <Notice tone="muted">После {deadlineTime(shift.deadline)} накануне меняются только время и точка — до 00:00, сколько угодно раз.</Notice>
          )}
          {mode === "create" && day.is_today && (
            <Notice tone="wait">Запись на сегодня срочная: администратор подтвердит её в боте.</Notice>
          )}
          {blocked && (
            <Notice tone="cancel">
              Сначала включи статус «Работаю».{" "}
              <button className="font-semibold underline underline-offset-2" onClick={onOpenProfile}>
                Открыть профиль
              </button>
            </Notice>
          )}
          <ShiftForm
            form={form}
            onChange={setForm}
            pricing={pricing}
            locations={locations}
            points={points}
            load={day.points_load}
            ownLocation={mode === "edit" ? shift?.location : null}
            disabled={blocked}
            volumeLocked={volumeLocked}
          />
        </div>
      ) : (
        shift && <ShiftView shift={shift} />
      )}

      {mode === "create" && closed && (
        <Notice tone="muted">
          Отметить завтрашний день можно было до {deadlineTime(day.deadline)}. С 00:00 можно попроситься на сегодня — срочной записью,
          её подтвердит администратор.
        </Notice>
      )}
      {!formMode && mode !== "contact" && <DeadlineNote day={day} shift={shift} />}

      {mode === "contact" && shift && (
        <div className="row-in space-y-3 rounded-2xl bg-muted/60 p-4">
          <p className="text-sm">
            Смена не изменится сама — администратор получит сообщение со сменой и вашим объяснением и решит.
          </p>
          <textarea rows={3} autoFocus value={message} onChange={(e) => setMessage(e.target.value.slice(0, 500))}
                    placeholder="Что случилось и что нужно поменять"
                    className="w-full resize-none rounded-xl border-0 bg-card px-3 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-ring/25" />
          <div className="flex gap-2">
            <Button variant="ghost" className="h-11 rounded-xl" onClick={() => setMode("view")}>Назад</Button>
            <Button className="press h-11 flex-1 rounded-xl font-semibold" disabled={!!busy || message.trim().length < 3}
                    onClick={async () => {
                      try {
                        if (await run("contact", (k) => api.contactAdmin(shift.id, message.trim(), k), "Сообщение отправлено администратору")) {
                          setMessage("");
                          setMode("view");
                        }
                      } catch {
                        /* toast shown */
                      }
                    }}>
              {busy === "contact" && <LoaderCircle className="animate-spin" />} Отправить
            </Button>
          </div>
        </div>
      )}

      {!formMode && shift && <ReportBlock shift={shift} pricing={pricing} onChanged={onChanged} />}

      <div className="space-y-2.5">
        {formMode && (
          <>
            <Button size="lg" className="press h-14 w-full rounded-2xl text-base font-semibold" onClick={submit} disabled={!!busy || blocked}>
              {busy && busy !== "off" && <LoaderCircle className="animate-spin" />}
              {submitLabel()}
            </Button>
            {mode === "create" && (
              <Button
                size="lg"
                variant="secondary"
                className="press h-12 w-full rounded-2xl text-[15px] font-semibold"
                disabled={!!busy || !working}
                onClick={() => act("off", (k) => api.preDeclareOff(day.date, k), "Отмечен выходной")}
              >
                {busy === "off" && <LoaderCircle className="animate-spin" />}
                Отметить выходной
              </Button>
            )}
            {(mode === "edit" || mode === "restore") && (
              <Button variant="ghost" className="h-11 w-full rounded-2xl" onClick={() => setMode("view")}>
                Назад
              </Button>
            )}
          </>
        )}

        {!formMode && mode !== "contact" && shift && (
          <>
            {shift.can_restore && (
              <Button
                size="lg"
                className="press h-14 w-full rounded-2xl text-base font-semibold"
                onClick={() => {
                  setForm(initialForm(prefillFrom(shift)));
                  setMode("restore");
                }}
              >
                Передумал, выйду
              </Button>
            )}
            {shift.can_clear_off && (
              <Button
                size="lg"
                className="press h-14 w-full rounded-2xl text-base font-semibold"
                disabled={!!busy}
                onClick={() => act("clear", (k) => api.clearOff(shift.id, k), "Выходной снят")}
              >
                {busy === "clear" && <LoaderCircle className="animate-spin" />}
                Передумал, выйду
              </Button>
            )}
            {shift.can_edit && (
              <Button
                size="lg"
                variant="secondary"
                className="press h-12 w-full rounded-2xl text-[15px] font-semibold"
                onClick={() => {
                  setForm(initialForm(prefillFrom(shift)));
                  setMode("edit");
                }}
              >
                {shift.volume_open ? "Изменить время, точку или объём" : "Изменить время или точку"}
              </Button>
            )}
            {shift.can_cancel && (
              <Button
                size="lg"
                variant="ghost"
                disabled={!!busy}
                onClick={() => setConfirm("cancel")}
                className="press h-12 w-full rounded-2xl text-[15px] font-semibold text-cancel hover:bg-cancel-soft hover:text-cancel"
              >
                {busy === "cancel" && <LoaderCircle className="animate-spin" />}
                Беру выходной
              </Button>
            )}
            {shift.can_contact_admin && (
              <Button
                size="lg"
                variant="secondary"
                className="press h-12 w-full rounded-2xl text-[15px] font-semibold"
                onClick={() => setMode("contact")}
              >
                Связаться с администратором
              </Button>
            )}
          </>
        )}
      </div>

      <AlertDialog open={confirm === "cancel"} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Взять выходной?</AlertDialogTitle>
            <AlertDialogDescription>
              Смена отменится, а день станет выходным — коллеги это увидят, администраторы получат уведомление.
              До {shift ? deadlineTime(shift.deadline) : "22:00"} накануне можно передумать и вернуться в график.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Оставить</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              className="rounded-xl"
              onClick={() => shift && act("cancel", (k) => api.cancel(shift.id, k), "Выходной отмечен, смена отменена")}
            >
              Беру выходной
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

    </div>
  );
}

function ShiftView({ shift }: { shift: Shift }) {
  const status = shift.status;
  if (status === "pre_declared_off") {
    return <Notice tone="muted">Ты отметил этот день как выходной. Коллеги это видят.</Notice>;
  }
  const struck = status === "cancelled" || status === "finally_cancelled" || status === "rejected_by_admin";
  return (
    <div className="space-y-4">
      <div className={cn(struck && "opacity-55")}>
        <p className={cn("font-display text-[2rem] leading-none font-semibold tabular", struck && "line-through decoration-2")}>
          {timeRange(shift)}
        </p>
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-[15px]">
          <Fact label="Точка">
            <span className="inline-flex items-center gap-1.5">
              <MapPin className="size-4 text-muted-foreground" />
              {shift.location}
            </span>
          </Fact>
          <Fact label="Объём">{shift.volume} шт.</Fact>
          {shift.training ? (
            <Fact label="Оплата">Учебная смена — не оплачивается</Fact>
          ) : (
            <>
              <Fact label="Ставка">
                {shift.rate} ₽ за шт.
                {shift.lazy && <span className="block text-xs text-wait">налог на лень</span>}
              </Fact>
              {!struck && shift.expected_pay != null && <Fact label="План заработка">{rub(shift.expected_pay)}</Fact>}
            </>
          )}
        </dl>
      </div>
      {status === "awaiting_approval" && (
        <Notice tone="wait">Администратор ещё не ответил на срочную заявку. Статус обновится сам.</Notice>
      )}
      {status === "cancelled" && shift.can_restore && (
        <Notice tone="muted">Ты взял выходной — смена отменена. Передумаешь до {shift.deadline?.slice(11, 16) ?? "22:00"} накануне — нажми «Передумал, выйду».</Notice>
      )}
      {status === "cancelled" && !shift.can_restore && (
        <Notice tone="cancel">Ты взял выходной — смена отменена. Вернуться на этот день можно только через администратора.</Notice>
      )}
      {status === "finally_cancelled" && (
        <Notice tone="cancel">
          {shift.penalty_amount != null ? `Штраф ${rub(shift.penalty_amount)}. ` : "Сумму штрафа назначит администратор. "}
          Изменить этот день можно только через администратора.
        </Notice>
      )}
      {status === "rejected_by_admin" && <Notice tone="muted">Администратор не подтвердил выход на этот день.</Notice>}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 font-semibold">{children}</dd>
    </div>
  );
}

const TONES = {
  wait: "bg-wait-soft",
  cancel: "bg-cancel-soft",
  muted: "bg-muted",
};

export function Notice({ tone, children }: { tone: keyof typeof TONES; children: React.ReactNode }) {
  return <div className={cn("rounded-2xl px-4 py-3 text-sm leading-relaxed", TONES[tone])}>{children}</div>;
}

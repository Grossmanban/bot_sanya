"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, MapPin } from "lucide-react";
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
import { api, ApiError, type Day, type Point, type Pricing, type Shift, type User } from "@/lib/api";
import { STATUS_META, WEEKDAYS, dayMonth, dayState, parseDay, rub, timeRange } from "@/lib/format";
import { readLocal, useAction, writeLocal } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { DayLanes, type Lane } from "../day-line";
import { initialForm, prefillFrom, ShiftForm, validateForm, type FormState } from "./shift-form";
import { ReportBlock } from "../report/report-block";

type Mode = "view" | "create" | "edit" | "restore" | "request";

export interface DetailProps {
  user: User;
  pricing: Pricing;
  locations: string[];
  points: Point[];
  onChanged: () => Promise<void> | void;
  onOpenProfile: () => void;
}

/** «00:00, в ночь на 24 октября» — the end of the free editing window. */
function untilText(iso: string) {
  return `00:00, в ночь на ${dayMonth(iso.slice(0, 10))}`;
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
  const [confirm, setConfirm] = useState<null | "cancel" | "final">(null);
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
  const formMode = mode !== "view";
  const blocked = !working && mode !== "edit" && mode !== "request";
  // 00:00 after booking has passed: the volume stays, only the time and the point change
  const volumeLocked = (mode === "edit" || mode === "request") && !!shift && !shift.volume_open;
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
      } else if (mode === "request" && shift) {
        if (await run("request", (k) => api.requestChange(shift.id, payload, k), "Запрос отправлен — ответ придёт в бот")) {
          setMode("view");
        }
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
    } catch (e) {
      if (e instanceof ApiError && e.code === "final_confirm_required") setConfirm("final");
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
    if (mode === "request") return "Отправить запрос";
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
          {mode === "restore" && (
            <Notice tone="wait">Вернуться можно один раз. Если потом снова отменишь, это будет окончательно и со штрафом.</Notice>
          )}
          {mode === "request" && (
            <Notice tone="wait">Сам ты уже менял эту смену. Второе изменение времени или точки подтверждает администратор или тренер сети — ответ придёт в бот.</Notice>
          )}
          {mode === "edit" && shift && !shift.volume_open && (
            <Notice tone="muted">Время или точку сам можно изменить один раз. Дальше — запросом администратору.</Notice>
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
            ownLocation={mode === "edit" || mode === "request" ? shift?.location : null}
            disabled={blocked}
            volumeLocked={volumeLocked}
          />
        </div>
      ) : (
        shift && <ShiftView shift={shift} />
      )}

      {!formMode && shift?.change_request && (
        <div className="row-in flex items-start gap-3 rounded-2xl bg-wait-soft p-4 text-sm">
          <div className="min-w-0 flex-1">
            <p className="font-semibold text-wait">Запрос на изменение ждёт решения</p>
            <p className="mt-0.5">
              {shift.change_request.start_time}–{shift.change_request.end_time}, {shift.change_request.location}. Ответ придёт в бот.
            </p>
          </div>
          <Button size="sm" variant="ghost" className="h-8 shrink-0 rounded-lg" disabled={!!busy}
                  onClick={() => act("withdraw", (k) => api.withdrawChange(shift.change_request!.id, k), "Запрос отозван")}>
            {busy === "withdraw" && <LoaderCircle className="animate-spin" />} Отозвать
          </Button>
        </div>
      )}
      {!formMode && shift?.volume_open && shift.volume_until && (
        <p className="text-sm text-muted-foreground">
          Объём, время и точку можно менять до {untilText(shift.volume_until)}. Потом объём фиксируется.
        </p>
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
            {(mode === "edit" || mode === "restore" || mode === "request") && (
              <Button variant="ghost" className="h-11 w-full rounded-2xl" onClick={() => setMode("view")}>
                Назад
              </Button>
            )}
          </>
        )}

        {!formMode && shift && (
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
            {shift.can_request_change && (
              <Button
                size="lg"
                variant="secondary"
                className="press h-12 w-full rounded-2xl text-[15px] font-semibold"
                onClick={() => {
                  setForm(initialForm(prefillFrom(shift)));
                  setMode("request");
                }}
              >
                Попросить изменить время или точку
              </Button>
            )}
            {(shift.can_cancel || shift.can_final_cancel) && (
              <Button
                size="lg"
                variant="ghost"
                disabled={!!busy}
                onClick={() => setConfirm(shift.can_final_cancel ? "final" : "cancel")}
                className="press h-12 w-full rounded-2xl text-[15px] font-semibold text-cancel hover:bg-cancel-soft hover:text-cancel"
              >
                {(busy === "cancel" || busy === "final") && <LoaderCircle className="animate-spin" />}
                Беру выходной
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
              Передумаешь — один раз можно вернуться в график на этот день.
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

      <AlertDialog open={confirm === "final"} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Выходной со штрафом?</AlertDialogTitle>
            <AlertDialogDescription>
              Эту смену ты уже отменял и восстанавливал, поэтому отмена будет окончательной: вернуться на этот день
              будет нельзя, и начислится штраф. Сумму назначит администратор.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Оставить смену</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              className="rounded-xl"
              onClick={() => shift && act("final", (k) => api.finalCancel(shift.id, k), "Выходной отмечен, смена отменена окончательно")}
            >
              Беру выходной со штрафом
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
      {status === "restored" && <Notice tone="wait">Смена восстановлена. Следующая отмена будет окончательной и со штрафом.</Notice>}
      {status === "cancelled" && shift.can_restore && (
        <Notice tone="muted">Ты взял выходной — смена отменена. Передумаешь — нажми «Передумал, выйду» (это можно один раз).</Notice>
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
      {(status === "active" || status === "restored") && !shift.can_edit && !shift.can_request_change && !shift.change_request
        && shift.edit_count > 0 && !shift.report?.started && !shift.report_state && (
        <p className="text-sm text-muted-foreground">Изменение уже использовано. Дальше через администратора.</p>
      )}
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

"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, MapPin, Minus, Plus } from "lucide-react";
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

type Mode = "view" | "create" | "edit" | "restore";

export interface DetailProps {
  user: User;
  pricing: Pricing;
  locations: string[];
  points: Point[];
  onChanged: () => Promise<void> | void;
  onOpenProfile: () => void;
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
  const blocked = !working && mode !== "edit";
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
        await run("edit", (k) => api.edit(shift.id, payload, k), "Смена изменена");
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
          />
        </div>
      ) : (
        shift && <ShiftView shift={shift} />
      )}

      {!formMode && shift?.can_set_fact && <FactBlock shift={shift} pricing={pricing} onChanged={onChanged} />}

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
                Изменить время, точку или объём
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
                Не выйду
              </Button>
            )}
          </>
        )}
      </div>

      <AlertDialog open={confirm === "cancel"} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Отменить выход?</AlertDialogTitle>
            <AlertDialogDescription>
              Администраторы получат уведомление. Потом можно один раз вернуться в график на этот день.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Оставить</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              className="rounded-xl"
              onClick={() => shift && act("cancel", (k) => api.cancel(shift.id, k), "Выход отменён")}
            >
              Отменить выход
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirm === "final"} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Отменить окончательно?</AlertDialogTitle>
            <AlertDialogDescription>
              Вернуться на этот день будет нельзя, и начислится штраф. Сумму назначит администратор.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Оставить смену</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              className="rounded-xl"
              onClick={() => shift && act("final", (k) => api.finalCancel(shift.id, k), "Смена отменена окончательно")}
            >
              Отменить со штрафом
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
          <Fact label="Ставка">
            {shift.rate} ₽ за шт.
            {shift.lazy && <span className="block text-xs text-wait">налог на лень</span>}
          </Fact>
          {!struck && shift.expected_pay != null && <Fact label="План заработка">{rub(shift.expected_pay)}</Fact>}
        </dl>
      </div>
      {status === "awaiting_approval" && (
        <Notice tone="wait">Администратор ещё не ответил на срочную заявку. Статус обновится сам.</Notice>
      )}
      {status === "restored" && <Notice tone="wait">Смена восстановлена. Следующая отмена будет окончательной и со штрафом.</Notice>}
      {status === "cancelled" && !shift.can_restore && (
        <Notice tone="cancel">Выход отменён. Вернуться на этот день можно только через администратора.</Notice>
      )}
      {status === "finally_cancelled" && (
        <Notice tone="cancel">
          {shift.penalty_amount != null ? `Штраф ${rub(shift.penalty_amount)}. ` : "Сумму штрафа назначит администратор. "}
          Изменить этот день можно только через администратора.
        </Notice>
      )}
      {status === "rejected_by_admin" && <Notice tone="muted">Администратор не подтвердил выход на этот день.</Notice>}
      {(status === "active" || status === "restored") && !shift.can_edit && shift.edit_count > 0 && !shift.can_set_fact && (
        <p className="text-sm text-muted-foreground">Изменение уже использовано. Дальше через администратора.</p>
      )}
    </div>
  );
}

function FactBlock({ shift, pricing, onChanged }: { shift: Shift; pricing: Pricing; onChanged: () => Promise<void> | void }) {
  const [editing, setEditing] = useState(shift.fact_volume == null);
  const [value, setValue] = useState(String(shift.fact_volume ?? shift.volume ?? ""));
  const { busy, run } = useAction();
  const n = Number(value) || 0;
  const rate = n > 0 && n < pricing.lazy_threshold ? pricing.lazy_rate : pricing.base_rate;

  async function save() {
    if (!/^\d+$/.test(value)) {
      toast.error("Факт — целое число");
      return;
    }
    try {
      await run("fact", (k) => api.setFact(shift.id, n, k), "Факт сохранён");
      setEditing(false);
      await onChanged();
    } catch {
      /* toast shown */
    }
  }

  if (!editing && shift.fact_volume != null) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-2xl bg-go-soft px-4 py-3">
        <div>
          <p className="text-xs text-muted-foreground">Сделано по факту</p>
          <p className="font-display text-xl font-semibold">
            {shift.fact_volume} шт.
            {shift.fact_pay != null && <span className="ml-2 text-base font-medium text-muted-foreground">{rub(shift.fact_pay)}</span>}
          </p>
        </div>
        <Button variant="ghost" className="rounded-xl" onClick={() => setEditing(true)}>
          Изменить
        </Button>
      </div>
    );
  }
  return (
    <div className="space-y-3 rounded-2xl bg-muted/60 p-4">
      <div>
        <p className="font-semibold">Сколько сделал по факту?</p>
        <p className="text-sm text-muted-foreground">План был {shift.volume} шт. Факт попадёт в отчёт и расчёт заработка.</p>
      </div>
      <div className="flex items-center gap-2">
        <button type="button" aria-label="Минус 1" onClick={() => setValue(String(Math.max(0, n - 1)))}
                className="press grid size-12 shrink-0 place-items-center rounded-2xl bg-card">
          <Minus className="size-5" />
        </button>
        <input inputMode="numeric" aria-label="Факт" value={value}
               onChange={(e) => setValue(e.target.value.replace(/\D/g, "").slice(0, 5))}
               className="h-12 w-full rounded-2xl border-0 bg-card text-center font-display text-xl font-semibold tabular outline-none focus:ring-2 focus:ring-ring/25" />
        <button type="button" aria-label="Плюс 1" onClick={() => setValue(String(n + 1))}
                className="press grid size-12 shrink-0 place-items-center rounded-2xl bg-card">
          <Plus className="size-5" />
        </button>
      </div>
      {n > 0 && <p className="text-sm text-muted-foreground">{n} × {rate} ₽ = {rub(n * rate)}</p>}
      <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy} onClick={save}>
        {busy && <LoaderCircle className="animate-spin" />} Сохранить факт
      </Button>
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

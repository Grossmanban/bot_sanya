"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, MapPin } from "lucide-react";
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
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from "@/components/ui/drawer";
import { api, ApiError, type Day, type Pricing, type Shift, type User } from "@/lib/api";
import { STATUS_META, dayState, longDate, rub, timeRange } from "@/lib/format";
import { readLocal, useAction, writeLocal } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { initialForm, prefillFrom, ShiftForm, validateForm, type FormState } from "./shift-form";

type Mode = "view" | "create" | "edit" | "restore";

export function DaySheet({
  day,
  user,
  pricing,
  locations,
  onClose,
  onChanged,
  onOpenProfile,
}: {
  day: Day | null;
  user: User;
  pricing: Pricing;
  locations: string[];
  onClose: () => void;
  onChanged: () => Promise<void> | void;
  onOpenProfile: () => void;
}) {
  const shift = day?.my_shift ?? null;
  const [mode, setMode] = useState<Mode>("view");
  const [form, setForm] = useState<FormState>(initialForm());
  const [confirm, setConfirm] = useState<null | "cancel" | "final">(null);
  const { busy, run } = useAction();

  // reset when another day is opened or the shift changes state
  const stateKey = `${day?.date}:${shift?.id ?? "none"}:${shift?.status ?? "none"}`;
  useEffect(() => {
    setMode(shift ? "view" : "create");
    setForm(initialForm(null, readLocal("last-location") ?? undefined));
    setConfirm(null);
  }, [stateKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!day) return <Drawer open={false} />;

  const working = user.work_status === "working";
  const meta = STATUS_META[dayState(shift)];

  async function submit() {
    const payload = validateForm(form);
    if (typeof payload === "string") {
      toast.error(payload);
      return;
    }
    writeLocal("last-location", payload.location);
    try {
      if (mode === "create") {
        const res = await run("create", (k) => api.create(day!.date, payload, k));
        if (res?.shift?.status === "awaiting_approval") toast.info("Заявка на сегодня отправлена админу");
        else toast.success("Записал! Админ уведомлён");
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

  const submitLabel = () => {
    const range = form.start_time && form.end_time ? ` ${form.start_time}–${form.end_time}` : "";
    if (mode === "edit") return "Сохранить изменения";
    if (mode === "restore") return `Выйду${range}`;
    return day.is_today ? `Попроситься на сегодня${range}` : `Выйду${range}`;
  };

  const formMode = mode !== "view";

  return (
    <>
      <Drawer open={!!day} onOpenChange={(o) => !o && onClose()} repositionInputs={false}>
        <DrawerContent className="mx-auto max-w-xl rounded-t-3xl">
          <DrawerHeader className="flex-row items-start justify-between gap-2 pb-2 text-left group-data-[vaul-drawer-direction=bottom]/drawer-content:text-left">
            <div>
              <DrawerTitle className="text-xl">{longDate(day.date).split(",")[0]}</DrawerTitle>
              <DrawerDescription>{longDate(day.date).split(", ")[1]}</DrawerDescription>
            </div>
            {shift && (
              <span className={cn("pt-1 text-sm font-semibold", meta.className)}>
                {meta.emoji} {meta.label}
              </span>
            )}
          </DrawerHeader>

          <div className="overflow-y-auto px-4 pb-6">
            {formMode ? (
              <div className="space-y-4">
                {mode === "restore" && (
                  <Notice tone="wait">
                    Восстановить смену можно один раз. Если потом снова отменишь — это будет окончательная отмена со штрафом.
                  </Notice>
                )}
                {mode === "create" && day.is_today && (
                  <Notice tone="wait">Запись на сегодня — срочная: заявка уйдёт администратору на одобрение.</Notice>
                )}
                {!working && mode !== "edit" ? (
                  <Notice tone="cancel">
                    Сначала включите статус «Работаю» в профиле.{" "}
                    <button className="font-semibold underline" onClick={onOpenProfile}>
                      Открыть профиль
                    </button>
                  </Notice>
                ) : null}
                <ShiftForm form={form} onChange={setForm} pricing={pricing} locations={locations}
                           disabled={!working && mode !== "edit"} />
              </div>
            ) : (
              shift && <ShiftView shift={shift} />
            )}

            <Colleagues day={day} />

            <div className="mt-5 space-y-2.5">
              {formMode && (
                <>
                  <Button size="lg" className="h-12 w-full text-base font-semibold" onClick={submit}
                          disabled={!!busy || (!working && mode !== "edit")}>
                    {busy && busy !== "off" ? <LoaderCircle className="animate-spin" /> : null}
                    {submitLabel()}
                  </Button>
                  {mode === "create" && (
                    <Button size="lg" variant="outline"
                            className="h-12 w-full border-cancel/30 bg-cancel-soft text-base font-semibold text-cancel hover:bg-cancel-soft/80 hover:text-cancel"
                            disabled={!!busy || !working}
                            onClick={() => act("off", (k) => api.preDeclareOff(day.date, k), "Отметил выходной")}>
                      {busy === "off" && <LoaderCircle className="animate-spin" />}
                      Не выхожу в этот день
                    </Button>
                  )}
                  {(mode === "edit" || mode === "restore") && (
                    <Button variant="ghost" className="w-full" onClick={() => setMode("view")}>
                      Назад
                    </Button>
                  )}
                </>
              )}

              {!formMode && shift && (
                <ShiftActions
                  shift={shift}
                  busy={busy}
                  onEdit={() => {
                    setForm(initialForm(prefillFrom(shift)));
                    setMode("edit");
                  }}
                  onCancel={() => setConfirm(shift.can_final_cancel ? "final" : "cancel")}
                  onRestore={() => {
                    setForm(initialForm(prefillFrom(shift)));
                    setMode("restore");
                  }}
                  onClearOff={() => act("clear", (k) => api.clearOff(shift.id, k), "Отметка снята")}
                />
              )}
            </div>
          </div>
        </DrawerContent>
      </Drawer>

      <AlertDialog open={confirm === "cancel"} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Отменить смену?</AlertDialogTitle>
            <AlertDialogDescription>
              Админы получат уведомление. После отмены ты сможешь один раз восстановить эту смену.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Нет, оставить</AlertDialogCancel>
            <AlertDialogAction variant="destructive"
                               onClick={() => shift && act("cancel", (k) => api.cancel(shift.id, k), "Смена отменена")}>
              Да, отменить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirm === "final"} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>⚠️ Окончательная отмена</AlertDialogTitle>
            <AlertDialogDescription>
              Это окончательная отмена. Восстановить смену больше не получится. Будет начислен штраф — его размер
              определит администратор.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Нет, оставить смену</AlertDialogCancel>
            <AlertDialogAction variant="destructive"
                               onClick={() => shift && act("final", (k) => api.finalCancel(shift.id, k), "Смена окончательно отменена")}>
              Да, отменить и принять штраф
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function ShiftView({ shift }: { shift: Shift }) {
  const status = shift.status;
  if (status === "pre_declared_off") {
    return <Notice tone="off">Вы отметили этот день как нерабочий.</Notice>;
  }
  const struck = status === "cancelled" || status === "finally_cancelled" || status === "rejected_by_admin";
  return (
    <div className="space-y-3">
      <dl className={cn("grid grid-cols-2 gap-2 rounded-2xl bg-muted/70 p-4 text-sm", struck && "opacity-60")}>
        <Item label="Время" value={timeRange(shift)} strong />
        <Item label="Точка" value={shift.location ?? "—"} strong />
        <Item label="Объём" value={`${shift.volume ?? "—"} шт.`} />
        <Item label="Ставка" value={shift.rate ? `${shift.rate} ₽/шт.${shift.lazy ? " (налог на лень)" : ""}` : "—"} />
        {!struck && shift.expected_pay != null && (
          <div className="col-span-2 mt-1 flex items-center justify-between border-t pt-2">
            <span className="text-muted-foreground">Планируемый заработок</span>
            <b className="text-base">{rub(shift.expected_pay)}</b>
          </div>
        )}
      </dl>
      {status === "awaiting_approval" && (
        <Notice tone="wait">Заявка на сегодняшнюю смену отправлена администратору. Ждите решения — статус обновится сам.</Notice>
      )}
      {status === "restored" && (
        <Notice tone="wait">Смена восстановлена. Следующая отмена будет окончательной — со штрафом.</Notice>
      )}
      {status === "cancelled" && !shift.can_restore && (
        <Notice tone="cancel">Смена отменена. Восстановить её можно только через администратора.</Notice>
      )}
      {status === "finally_cancelled" && (
        <Notice tone="cancel">
          🔒 Смена окончательно отменена.{" "}
          {shift.penalty_amount != null ? `Штраф: ${rub(shift.penalty_amount)}.` : "Сумму штрафа определит администратор."}{" "}
          Изменения — только через администратора.
        </Notice>
      )}
      {status === "rejected_by_admin" && <Notice tone="cancel">Администратор отклонил заявку на этот день.</Notice>}
      {(status === "active" || status === "restored") && !shift.can_edit && (
        <p className="text-xs text-muted-foreground">Изменить смену можно один раз — дальше через администратора.</p>
      )}
    </div>
  );
}

function ShiftActions({
  shift,
  busy,
  onEdit,
  onCancel,
  onRestore,
  onClearOff,
}: {
  shift: Shift;
  busy: string | null;
  onEdit: () => void;
  onCancel: () => void;
  onRestore: () => void;
  onClearOff: () => void;
}) {
  return (
    <>
      {shift.can_restore && (
        <Button size="lg" className="h-12 w-full text-base font-semibold" onClick={onRestore}>
          Передумал, выйду на эту смену
        </Button>
      )}
      {shift.can_clear_off && (
        <Button size="lg" className="h-12 w-full text-base font-semibold" onClick={onClearOff} disabled={!!busy}>
          {busy === "clear" && <LoaderCircle className="animate-spin" />}
          Передумал, выйду
        </Button>
      )}
      {shift.can_edit && (
        <Button size="lg" variant="outline" className="h-12 w-full text-base" onClick={onEdit}>
          Изменить
        </Button>
      )}
      {(shift.can_cancel || shift.can_final_cancel) && (
        <Button size="lg" variant="outline" onClick={onCancel} disabled={!!busy}
                className="h-12 w-full border-cancel/30 bg-cancel-soft text-base font-semibold text-cancel hover:bg-cancel-soft/80 hover:text-cancel">
          {(busy === "cancel" || busy === "final") && <LoaderCircle className="animate-spin" />}
          Не выхожу
        </Button>
      )}
    </>
  );
}

function Colleagues({ day }: { day: Day }) {
  if (!day.colleagues.length && !day.colleagues_off.length) return null;
  return (
    <div className="mt-5">
      <p className="mb-2 text-sm text-muted-foreground">Выходят</p>
      <ul className="space-y-2">
        {day.colleagues.map((c, i) => (
          <li key={i} className="rounded-xl border bg-card px-3 py-2">
            <div className="flex items-center justify-between">
              <span className="font-medium">{c.name}</span>
              <span className="font-semibold text-go">{timeRange(c)}</span>
            </div>
            <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-go-soft px-2 py-0.5 text-xs font-medium text-go">
              <MapPin className="size-3" /> {c.location}
            </span>
          </li>
        ))}
      </ul>
      {day.colleagues_off.length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          🚫 Не выходят: {day.colleagues_off.map((c) => c.name).join(", ")}
        </p>
      )}
    </div>
  );
}

function Item({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn(strong && "text-base font-semibold")}>{value}</dd>
    </div>
  );
}

const TONES = {
  wait: "border-wait/30 bg-wait/10",
  cancel: "border-cancel/30 bg-cancel-soft",
  off: "border-off/30 bg-off/10",
};

function Notice({ tone, children }: { tone: keyof typeof TONES; children: React.ReactNode }) {
  return <div className={cn("rounded-xl border px-3 py-2.5 text-sm", TONES[tone])}>{children}</div>;
}

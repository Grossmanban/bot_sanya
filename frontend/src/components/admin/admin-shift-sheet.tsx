"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, Trash2, X } from "lucide-react";
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
import { Drawer, DrawerClose, DrawerContent, DrawerDescription, DrawerTitle } from "@/components/ui/drawer";
import { api, type AdminAction, type ReportView, type Shift, type ShiftStatus } from "@/lib/api";
import { STATUS_META, longDate } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { useWide } from "@/lib/use-media";
import { useCan } from "@/lib/caps";
import { cn } from "@/lib/utils";
import { ReportDecision } from "./reports";

export interface EditableShift {
  id: number;
  date: string;
  status: ShiftStatus;
  start_time: string | null;
  end_time: string | null;
  volume: number | null;
  location: string | null;
  penalty_amount: number | null;
  fact_volume?: number | null;
  shortfall?: number;
  shortfall_status?: "fined" | "waived" | null;
  shortfall_penalty?: number | null;
  report?: ReportView | null;
  /** the employee's second change of the time or the point, waiting for a decision */
  change_request?: Shift["change_request"];
  name: string;
  username: string | null;
}

const PENALTY_PRESETS = [500, 1000, 2000];
const field = "h-12 w-full rounded-2xl border-0 bg-muted px-4 outline-none focus:ring-2 focus:ring-ring/25";

export function AdminShiftSheet({
  shift,
  onClose,
  onChanged,
}: {
  shift: EditableShift | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const wide = useWide();
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [volume, setVolume] = useState("");
  const [location, setLocation] = useState("");
  const [penalty, setPenalty] = useState("");
  const [reason, setReason] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { busy, run } = useAction();
  const canFine = useCan("fine_direct");

  useEffect(() => {
    if (!shift) return;
    setStart(shift.start_time ?? "");
    setEnd(shift.end_time ?? "");
    setVolume(shift.volume ? String(shift.volume) : "");
    setLocation(shift.location ?? "");
    setPenalty(shift.penalty_amount != null ? String(shift.penalty_amount) : "");
    setReason(shift.status === "finally_cancelled" ? "Окончательная отмена смены" : "");
  }, [shift]);

  const meta = shift ? STATUS_META[shift.status] : null;
  const hasTimes = shift?.status !== "pre_declared_off";
  const urgent = shift?.status === "awaiting_approval";
  const canForceCancel = !!shift && ["active", "restored"].includes(shift.status);
  const canForceRestore = !!shift && ["cancelled", "finally_cancelled", "rejected_by_admin"].includes(shift.status) && hasTimes;
  const isFinal = shift?.status === "finally_cancelled";
  const going = shift?.status === "active" || shift?.status === "restored";

  function needReason() {
    if (!reason.trim()) {
      toast.error("Укажи причину: она попадёт в журнал");
      return true;
    }
    return false;
  }

  async function save() {
    if (!shift || needReason()) return;
    const body: Parameters<typeof api.admin.editShift>[1] = { reason: reason.trim() };
    if (hasTimes) {
      if (start !== shift.start_time) body.start_time = start;
      if (end !== shift.end_time) body.end_time = end;
      if (location.trim() !== shift.location) body.location = location.trim();
      if (volume && Number(volume) !== shift.volume) body.volume = Number(volume);
    }
    if (isFinal && penalty !== "" && Number(penalty) !== shift.penalty_amount) body.penalty_amount = Number(penalty);
    if (Object.keys(body).length === 1) {
      toast.error("Ничего не изменилось");
      return;
    }
    try {
      await run("save", (k) => api.admin.editShift(shift.id, body, k), "Сохранено");
      onChanged();
    } catch {
      /* toast shown */
    }
  }

  async function action(name: AdminAction, success: string) {
    if (!shift) return;
    const isUrgent = name === "approve_urgent" || name === "reject_urgent";
    if (!isUrgent && needReason()) return;
    try {
      await run(name, (k) => api.admin.editShift(shift.id, { reason: reason.trim() || "Срочная заявка", action: name }, k), success);
      onChanged();
    } catch {
      /* toast shown */
    }
  }

  async function decideChange(approve: boolean) {
    if (!shift?.change_request) return;
    const id = shift.change_request.id;
    try {
      await run(approve ? "change-ok" : "change-no", (k) => api.admin.decideChange(id, approve, reason.trim() || null, k),
        approve ? "Изменение разрешено" : "Запрос отклонён");
      onChanged();
    } catch {
      /* toast shown */
    }
  }

  async function remove() {
    if (!shift) return;
    try {
      await run("delete", (k) => api.admin.deleteShift(shift.id, reason.trim(), k), "Запись удалена");
      onChanged();
    } catch {
      /* toast shown */
    }
  }

  const spin = (name: string) => busy === name && <LoaderCircle className="animate-spin" />;

  return (
    <>
      <Drawer open={!!shift} onOpenChange={(o) => !o && onClose()} direction={wide ? "right" : "bottom"} repositionInputs={false}>
        <DrawerContent
          className={cn(
            "border-0 bg-card",
            wide ? "w-[28rem] max-w-[28rem] sm:max-w-[28rem]" : "mx-auto max-h-[92dvh] max-w-2xl rounded-t-[1.75rem]",
          )}
        >
          {shift && meta && (
            <div className={cn("overflow-y-auto px-5", wide ? "h-full py-8" : "pb-safe pt-2")}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <DrawerTitle className="truncate font-display text-xl font-semibold">{shift.name}</DrawerTitle>
                  <DrawerDescription>
                    {longDate(shift.date)}
                    {shift.username ? `, @${shift.username}` : ""}
                  </DrawerDescription>
                </div>
                {wide && (
                  <DrawerClose className="grid size-9 place-items-center rounded-full bg-muted" aria-label="Закрыть">
                    <X className="size-4" />
                  </DrawerClose>
                )}
              </div>
              <span className={cn("mt-3 inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold", meta.soft, meta.text)}>
                <span className={cn("size-1.5 rounded-full", meta.dot)} />
                {meta.label}
              </span>

              <div className="mt-6 space-y-4">
                {urgent && (
                  <div className="grid grid-cols-2 gap-2">
                    <Button className="press h-12 rounded-2xl bg-go text-[15px] font-semibold text-white hover:bg-go/90" disabled={!!busy}
                            onClick={() => action("approve_urgent", "Выход разрешён")}>
                      {spin("approve_urgent")} Разрешить
                    </Button>
                    <Button variant="secondary" className="press h-12 rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                            onClick={() => action("reject_urgent", "Выход запрещён")}>
                      {spin("reject_urgent")} Запретить
                    </Button>
                  </div>
                )}

                {shift.change_request && (
                  <div className="space-y-3 rounded-2xl bg-wait-soft p-4">
                    <div className="text-sm">
                      <p className="font-semibold text-wait">Запрос на изменение</p>
                      <p className="mt-0.5">
                        {shift.start_time}–{shift.end_time}, {shift.location} → <b>{shift.change_request.start_time}–{shift.change_request.end_time}, {shift.change_request.location}</b>
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">Сам сотрудник уже менял смену; объём остаётся {shift.volume} шт.</p>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <Button className="press h-11 rounded-xl bg-go font-semibold text-white hover:bg-go/90" disabled={!!busy}
                              onClick={() => decideChange(true)}>
                        {spin("change-ok")} Разрешить
                      </Button>
                      <Button variant="secondary" className="press h-11 rounded-xl font-semibold" disabled={!!busy}
                              onClick={() => decideChange(false)}>
                        {spin("change-no")} Отклонить
                      </Button>
                    </div>
                  </div>
                )}

                {hasTimes && (
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="Начало"><input type="time" className={cn(field, "tabular")} value={start} onChange={(e) => setStart(e.target.value)} /></Field>
                    <Field label="Конец"><input type="time" className={cn(field, "tabular")} value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
                    <Field label="Точка"><input className={field} value={location} onChange={(e) => setLocation(e.target.value)} /></Field>
                    <Field label="Объём, шт.">
                      <input inputMode="numeric" className={cn(field, "tabular")} value={volume}
                             onChange={(e) => setVolume(e.target.value.replace(/\D/g, "").slice(0, 5))} />
                    </Field>
                  </div>
                )}

                {going && shift.report?.started && (
                  <div className="space-y-3 rounded-2xl bg-muted/50 p-4">
                    <p className="font-semibold">Отчёт</p>
                    <ReportDecision
                      subject={{
                        shift_id: shift.id,
                        date: shift.date,
                        volume: shift.volume ?? 0,
                        fact_volume: shift.fact_volume ?? null,
                        state: shift.report.state,
                        decided: shift.report.decided,
                        fine: shift.report.fine,
                        note: shift.report.note,
                        deadline: shift.report.deadline,
                        attempts: shift.report.last ? [shift.report.last] : [],
                        team: shift.report.team,
                        fine_block: shift.report.fine_block,
                        fine_pending: shift.report.fine_pending,
                      }}
                      onDone={onChanged}
                    />
                  </div>
                )}

                {isFinal && canFine && (
                  <div className="space-y-2 rounded-2xl bg-cancel-soft p-4">
                    <p className="text-sm font-semibold">Штраф за окончательную отмену</p>
                    <div className="flex gap-2">
                      {PENALTY_PRESETS.map((p) => (
                        <button key={p} onClick={() => setPenalty(String(p))}
                                className={cn("press rounded-full px-3.5 py-1.5 text-sm font-semibold", penalty === String(p) ? "bg-final text-white" : "bg-card")}>
                          {p} ₽
                        </button>
                      ))}
                    </div>
                    <input inputMode="numeric" className={cn(field, "bg-card")} placeholder="Своя сумма, ₽" value={penalty}
                           onChange={(e) => setPenalty(e.target.value.replace(/\D/g, "").slice(0, 7))} />
                  </div>
                )}

                <Field label="Причина изменения">
                  <textarea rows={2} className="w-full resize-none rounded-2xl border-0 bg-muted px-4 py-3 outline-none focus:ring-2 focus:ring-ring/25"
                            value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Например, договорились по телефону" />
                </Field>

                <div className="space-y-2.5 pt-1">
                  <Button className="press h-14 w-full rounded-2xl text-base font-semibold" onClick={save} disabled={!!busy}>
                    {spin("save")} Сохранить
                  </Button>
                  {canForceCancel && (
                    <Button variant="secondary" className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                            onClick={() => action("force_cancel", "Выход отменён без штрафа")}>
                      {spin("force_cancel")} Отменить без штрафа
                    </Button>
                  )}
                  {canForceRestore && (
                    <Button variant="secondary" className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                            onClick={() => action("force_restore", "Выход восстановлен")}>
                      {spin("force_restore")} Вернуть в график
                    </Button>
                  )}
                  <Button variant="ghost" className="h-12 w-full rounded-2xl text-[15px] font-semibold text-cancel hover:bg-cancel-soft hover:text-cancel"
                          disabled={!!busy} onClick={() => !needReason() && setConfirmDelete(true)}>
                    <Trash2 /> Удалить запись
                  </Button>
                  <p className="text-center text-xs text-muted-foreground">После правки сотрудник сам эту смену уже не изменит.</p>
                </div>
              </div>
            </div>
          )}
        </DrawerContent>
      </Drawer>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Удалить запись?</AlertDialogTitle>
            <AlertDialogDescription>
              Запись исчезнет из графика, но останется в журнале. У сотрудника день станет «Не отмечен».
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Оставить</AlertDialogCancel>
            <AlertDialogAction variant="destructive" className="rounded-xl" onClick={remove}>Удалить</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

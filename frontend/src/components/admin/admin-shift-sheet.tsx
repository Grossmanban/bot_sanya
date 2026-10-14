"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, Trash2 } from "lucide-react";
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
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from "@/components/ui/drawer";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, type AdminAction, type ShiftStatus } from "@/lib/api";
import { STATUS_META, longDate } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export interface EditableShift {
  id: number;
  date: string;
  status: ShiftStatus;
  start_time: string | null;
  end_time: string | null;
  volume: number | null;
  location: string | null;
  penalty_amount: number | null;
  name: string;
  username: string | null;
}

const PENALTY_PRESETS = [500, 1000, 2000];

export function AdminShiftSheet({
  shift,
  onClose,
  onChanged,
}: {
  shift: EditableShift | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [volume, setVolume] = useState("");
  const [location, setLocation] = useState("");
  const [penalty, setPenalty] = useState("");
  const [reason, setReason] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { busy, run } = useAction();

  useEffect(() => {
    if (!shift) return;
    setStart(shift.start_time ?? "");
    setEnd(shift.end_time ?? "");
    setVolume(shift.volume ? String(shift.volume) : "");
    setLocation(shift.location ?? "");
    setPenalty(shift.penalty_amount != null ? String(shift.penalty_amount) : "");
    setReason(shift.status === "finally_cancelled" ? "Окончательная отмена смены" : "");
  }, [shift]);

  if (!shift) return <Drawer open={false} />;

  const meta = STATUS_META[shift.status];
  const hasTimes = shift.status !== "pre_declared_off";
  const urgent = shift.status === "awaiting_approval";
  const canForceCancel = ["active", "restored"].includes(shift.status);
  const canForceRestore = ["cancelled", "finally_cancelled", "rejected_by_admin"].includes(shift.status) && hasTimes;
  const isFinal = shift.status === "finally_cancelled";

  function needReason() {
    if (!reason.trim()) {
      toast.error("Укажите причину — она попадёт в журнал");
      return true;
    }
    return false;
  }

  async function save() {
    if (needReason()) return;
    const body: Parameters<typeof api.admin.editShift>[1] = { reason: reason.trim() };
    if (hasTimes) {
      if (start !== shift!.start_time) body.start_time = start;
      if (end !== shift!.end_time) body.end_time = end;
      if (location.trim() !== shift!.location) body.location = location.trim();
      if (volume && Number(volume) !== shift!.volume) body.volume = Number(volume);
    }
    if (isFinal && penalty !== "" && Number(penalty) !== shift!.penalty_amount) body.penalty_amount = Number(penalty);
    if (Object.keys(body).length === 1) {
      toast.error("Нет изменений");
      return;
    }
    try {
      await run("save", (k) => api.admin.editShift(shift!.id, body, k), "Сохранено");
      onChanged();
    } catch {
      /* toast shown */
    }
  }

  async function action(name: AdminAction, success: string) {
    const isUrgent = name === "approve_urgent" || name === "reject_urgent";
    if (!isUrgent && needReason()) return;
    try {
      await run(name, (k) => api.admin.editShift(shift!.id, { reason: reason.trim() || "Срочная заявка", action: name }, k), success);
      onChanged();
    } catch {
      /* toast shown */
    }
  }

  async function remove() {
    try {
      await run("delete", (k) => api.admin.deleteShift(shift!.id, reason.trim(), k), "Запись удалена");
      onChanged();
    } catch {
      /* toast shown */
    }
  }

  return (
    <>
      <Drawer open onOpenChange={(o) => !o && onClose()} repositionInputs={false}>
        <DrawerContent className="mx-auto max-w-xl rounded-t-3xl">
          <DrawerHeader className="flex-row items-start justify-between gap-2 pb-2 text-left group-data-[vaul-drawer-direction=bottom]/drawer-content:text-left">
            <div>
              <DrawerTitle className="text-xl">{shift.name}</DrawerTitle>
              <DrawerDescription>
                {longDate(shift.date)}
                {shift.username ? ` · @${shift.username}` : ""}
              </DrawerDescription>
            </div>
            <span className={cn("pt-1 text-sm font-semibold", meta.className)}>
              {meta.emoji} {meta.label}
            </span>
          </DrawerHeader>

          <div className="space-y-4 overflow-y-auto px-4 pb-6">
            {hasTimes && (
              <div className="grid grid-cols-2 gap-3">
                <Field label="С"><Input type="time" className="h-11" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
                <Field label="До"><Input type="time" className="h-11" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
                <Field label="Точка"><Input className="h-11" value={location} onChange={(e) => setLocation(e.target.value)} /></Field>
                <Field label="Объём, шт.">
                  <Input inputMode="numeric" className="h-11" value={volume}
                         onChange={(e) => setVolume(e.target.value.replace(/\D/g, "").slice(0, 5))} />
                </Field>
              </div>
            )}

            {isFinal && (
              <div className="space-y-2 rounded-xl border border-final/30 bg-cancel-soft p-3">
                <Label>Штраф за окончательную отмену, ₽</Label>
                <div className="flex gap-1.5">
                  {PENALTY_PRESETS.map((p) => (
                    <button key={p} onClick={() => setPenalty(String(p))}
                            className={cn("rounded-full border px-3 py-1 text-xs font-semibold",
                              penalty === String(p) ? "border-final bg-final text-white" : "bg-card")}>
                      {p} ₽
                    </button>
                  ))}
                </div>
                <Input inputMode="numeric" className="h-11 bg-card" placeholder="Своя сумма" value={penalty}
                       onChange={(e) => setPenalty(e.target.value.replace(/\D/g, "").slice(0, 7))} />
              </div>
            )}

            <Field label="Причина (обязательно, пишется в журнал)">
              <Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Например: договорились по телефону" />
            </Field>

            <div className="space-y-2.5">
              <Button size="lg" className="h-12 w-full text-base font-semibold" onClick={save} disabled={!!busy}>
                {busy === "save" && <LoaderCircle className="animate-spin" />} Сохранить
              </Button>
              {urgent && (
                <div className="grid grid-cols-2 gap-2">
                  <Button size="lg" variant="outline" className="h-12 text-go" disabled={!!busy}
                          onClick={() => action("approve_urgent", "Срочная заявка одобрена")}>
                    {busy === "approve_urgent" && <LoaderCircle className="animate-spin" />} ✅ Разрешить
                  </Button>
                  <Button size="lg" variant="outline" className="h-12 text-cancel" disabled={!!busy}
                          onClick={() => action("reject_urgent", "Срочная заявка отклонена")}>
                    {busy === "reject_urgent" && <LoaderCircle className="animate-spin" />} ⛔ Запретить
                  </Button>
                </div>
              )}
              {canForceCancel && (
                <Button size="lg" variant="outline" className="h-12 w-full text-cancel" disabled={!!busy}
                        onClick={() => action("force_cancel", "Смена отменена без штрафа")}>
                  {busy === "force_cancel" && <LoaderCircle className="animate-spin" />} Отменить без штрафа
                </Button>
              )}
              {canForceRestore && (
                <Button size="lg" variant="outline" className="h-12 w-full text-go" disabled={!!busy}
                        onClick={() => action("force_restore", "Смена восстановлена")}>
                  {busy === "force_restore" && <LoaderCircle className="animate-spin" />} Восстановить смену
                </Button>
              )}
              <Button variant="ghost" className="w-full text-destructive" disabled={!!busy}
                      onClick={() => !needReason() && setConfirmDelete(true)}>
                <Trash2 /> Снять с дня (удалить запись)
              </Button>
              <p className="text-center text-xs text-muted-foreground">
                После ручной правки сотрудник больше не сможет сам изменить эту смену.
              </p>
            </div>
          </div>
        </DrawerContent>
      </Drawer>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить запись?</AlertDialogTitle>
            <AlertDialogDescription>
              Запись пропадёт из графика, но останется в журнале событий. День у сотрудника станет «Не отметился».
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={remove}>Удалить</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

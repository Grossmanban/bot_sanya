"use client";

import { useState } from "react";
import { Eye, EyeOff, LoaderCircle, Pencil, Trash2 } from "lucide-react";
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
import { api, type Lifecycle } from "@/lib/api";
import { useAction } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { PhotoPicker, PhotoThumb, VideoPicker, VideoThumb } from "./report/photo";

/** Shared by goals and events once they run (ТЗ №20, №22). */

export const fieldClass = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

export const CHANGE_LABELS: Record<string, string> = {
  starts_at: "старт", ends_at: "финиш", date_from: "начало", date_to: "конец", target: "цель",
  max_winners: "число победителей", max_attempts: "попытки", prize: "приз", prize_type: "тип приза",
};

/** The prize on the right of a card: a photo or a video that opens full screen, or a grey square with 🎁. */
export function PrizeThumb({ url, kind, className }: { url: string | null | undefined; kind?: "photo" | "video" | null; className?: string }) {
  if (url && kind === "video") return <VideoThumb src={url} label="приз" className={cn("size-16 rounded-2xl", className)} />;
  if (url) return <PhotoThumb src={url} label="приз" className={cn("size-16 rounded-2xl", className)} />;
  return (
    <span aria-hidden className={cn("grid size-16 shrink-0 place-items-center rounded-2xl bg-muted text-2xl", className)}>🎁</span>
  );
}

export function prizeText(item: Pick<Lifecycle, "prize" | "prize_type">) {
  return [item.prize_type, item.prize].filter(Boolean).join(" — ");
}

export interface PrizeState {
  type: string;
  prize: string;
  /** a new photo picked on the device (uploaded on save) */
  file: Blob | null;
  /** or a new video (events) */
  video: File | null;
  /** the current photo was removed */
  cleared: boolean;
}

export const prizeState = (item?: Partial<Lifecycle>): PrizeState => ({
  type: item?.prize_type ?? "", prize: item?.prize ?? "", file: null, video: null, cleared: false,
});

/** Uploads a new photo; returns the value for `prize_photo` (undefined — unchanged, null — removed). */
export async function prizePhotoValue(state: PrizeState): Promise<string | null | undefined> {
  if (state.file) return (await api.admin.uploadMedia(state.file)).file_id;
  return state.cleared ? null : undefined;
}

/** The same with a video: the fields to send, or {} when the prize's file didn't change. */
export async function prizeMediaValue(state: PrizeState): Promise<{ prize_photo?: string | null; prize_media_kind?: "photo" | "video" }> {
  if (state.video) return { prize_photo: (await api.admin.uploadVideo(state.video)).file_id, prize_media_kind: "video" };
  const photo = await prizePhotoValue(state);
  if (photo === undefined) return {};
  return photo ? { prize_photo: photo, prize_media_kind: "photo" } : { prize_photo: null };
}

export const prizeTouched = (state: PrizeState) => !!state.file || !!state.video || state.cleared;

/** Prize type: one of the list or the creator's own (answer 3), the prize itself and its photo (or a video). */
export function PrizeFields({ types, value, onChange, currentUrl, currentKind, allowVideo }: {
  types: string[];
  value: PrizeState;
  onChange: (v: PrizeState) => void;
  currentUrl?: string | null;
  currentKind?: "photo" | "video" | null;
  allowVideo?: boolean;
}) {
  const own = value.type !== "" && !types.includes(value.type);
  const [custom, setCustom] = useState(own);
  const set = (patch: Partial<PrizeState>) => onChange({ ...value, ...patch });
  const chip = (active: boolean) =>
    cn("press h-9 rounded-full px-3.5 text-sm font-medium", active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground");
  const showCurrent = currentUrl && !value.cleared && !value.file && !value.video;
  return (
    <div className="space-y-2.5">
      <p className="text-xs text-muted-foreground">Приз</p>
      <div className="flex flex-wrap gap-1.5">
        <button type="button" className={chip(!value.type && !custom)} onClick={() => { setCustom(false); set({ type: "" }); }}>Без типа</button>
        {types.map((t) => (
          <button type="button" key={t} className={chip(value.type === t && !custom)} onClick={() => { setCustom(false); set({ type: t }); }}>{t}</button>
        ))}
        <button type="button" className={chip(custom)} onClick={() => { setCustom(true); set({ type: own ? value.type : "" }); }}>Своё</button>
      </div>
      {custom && (
        <input className={fieldClass} placeholder="Свой тип, например «Ужин с командой»" value={value.type}
               onChange={(e) => set({ type: e.target.value.slice(0, 40) })} />
      )}
      <input className={fieldClass} placeholder="Что именно, например «Наушники»" value={value.prize}
             onChange={(e) => set({ prize: e.target.value.slice(0, 200) })} />
      {showCurrent ? (
        <div className="flex items-center gap-3">
          <PrizeThumb url={currentUrl} kind={currentKind} />
          <Button type="button" variant="ghost" size="sm" className="rounded-lg text-muted-foreground" onClick={() => set({ cleared: true })}>
            {currentKind === "video" ? "Убрать видео" : "Убрать фото"}
          </Button>
        </div>
      ) : (
        <>
          {!value.video && <PhotoPicker value={value.file} onChange={(file) => set({ file, video: null })} label="Фото приза" optional />}
          {allowVideo && !value.file && <VideoPicker value={value.video} onChange={(video) => set({ video, file: null })} label="Видео приза" />}
        </>
      )}
    </div>
  );
}

/** A small icon button for the card's toolbar. */
export function ToolButton({ label, onClick, busy, children, danger }: {
  label: string; onClick: () => void; busy?: boolean; children: React.ReactNode; danger?: boolean;
}) {
  return (
    <Button variant="ghost" size="icon" aria-label={label} title={label} disabled={busy} onClick={onClick}
            className={cn("size-9 shrink-0 rounded-xl text-muted-foreground", danger && "hover:text-cancel")}>
      {busy ? <LoaderCircle className="animate-spin" /> : children}
    </Button>
  );
}

export function EditButton({ onClick }: { onClick: () => void }) {
  return <ToolButton label="Редактировать" onClick={onClick}><Pencil /></ToolButton>;
}

/** The eye (ТЗ №22): open — employees see it, crossed — hidden. Hiding asks first. */
export function VisibilityButton({ item, noun, onToggle }: {
  item: Lifecycle;
  noun: "событие" | "цель";
  onToggle: (hidden: boolean, key: string) => Promise<unknown>;
}) {
  const { busy, run } = useAction();
  const [ask, setAsk] = useState(false);
  const hidden = item.visibility === "hidden" || item.in_feed === false;
  const toggle = async (hide: boolean) => {
    try {
      await run("eye", (k) => onToggle(hide, k), hide ? "Скрыто из ленты" : "Снова в ленте");
    } catch {
      /* toast shown */
    }
  };
  return (
    <>
      <ToolButton label={hidden ? "Показать сотрудникам" : "Скрыть из ленты"} busy={!!busy}
                  onClick={() => (hidden ? toggle(false) : setAsk(true))}>
        {hidden ? <EyeOff /> : <Eye />}
      </ToolButton>
      <AlertDialog open={ask} onOpenChange={setAsk}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Скрыть {noun === "цель" ? "цель" : "событие"}?</AlertDialogTitle>
            <AlertDialogDescription>
              {noun === "цель" ? "Эта цель будет убрана" : "Это событие будет убрано"} из ленты сотрудников. Итоги сохранятся, их можно
              посмотреть в «Архиве». Вернуть можно в любой момент.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Отмена</AlertDialogCancel>
            <AlertDialogAction className="rounded-xl" onClick={() => toggle(true)}>Скрыть</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export function DeleteButton({ noun, onDelete }: { noun: "событие" | "цель"; onDelete: (key: string) => Promise<unknown> }) {
  const { busy, run } = useAction();
  const [ask, setAsk] = useState(false);
  return (
    <>
      <ToolButton label="Удалить" danger busy={!!busy} onClick={() => setAsk(true)}><Trash2 /></ToolButton>
      <AlertDialog open={ask} onOpenChange={setAsk}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Удалить {noun}?</AlertDialogTitle>
            <AlertDialogDescription>
              {noun === "цель" ? "Цель пропадёт" : "Событие пропадёт"} у всех, в том числе из архива. Если нужно только убрать из ленты —
              нажмите на глаз.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Отмена</AlertDialogCancel>
            <AlertDialogAction className="rounded-xl bg-cancel text-white hover:bg-cancel/90"
                               onClick={async () => { try { await run("del", onDelete, noun === "цель" ? "Цель удалена" : "Событие удалено"); } catch { /* toast */ } }}>
              Удалить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/** «Это изменение повлияет на текущий прогресс. Уведомить участников?» — before saving a critical change. */
export function NotifyDialog({ fields, onChoose, onCancel }: {
  fields: string[] | null;
  onChoose: (notify: boolean) => void;
  onCancel: () => void;
}) {
  return (
    <AlertDialog open={!!fields} onOpenChange={(o) => !o && onCancel()}>
      <AlertDialogContent className="rounded-3xl">
        <AlertDialogHeader>
          <AlertDialogTitle className="font-display">Уведомить участников?</AlertDialogTitle>
          <AlertDialogDescription>
            Это изменение повлияет на текущий прогресс: меняется {fields?.map((f) => CHANGE_LABELS[f] ?? f).join(", ")}.
            Участники получат сообщение «Важное обновление» с тем, что изменилось.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="gap-2">
          <AlertDialogCancel className="rounded-xl">Отмена</AlertDialogCancel>
          <Button variant="secondary" className="rounded-xl" onClick={() => onChoose(false)}>Сохранить молча</Button>
          <AlertDialogAction className="rounded-xl" onClick={() => onChoose(true)}>Сохранить и уведомить</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** A reason with quick presets (annul a result, take a completion back). */
export function ReasonInput({ value, onChange, presets }: { value: string; onChange: (v: string) => void; presets: string[] }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {presets.map((p) => (
          <button type="button" key={p} onClick={() => onChange(p)}
                  className={cn("press h-8 rounded-full px-3 text-xs font-medium", value === p ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
            {p}
          </button>
        ))}
      </div>
      <input autoFocus className={fieldClass} placeholder="Причина — человек её увидит" value={value}
             onChange={(e) => onChange(e.target.value.slice(0, 300))} />
    </div>
  );
}

export const ANNUL_PRESETS = ["Нечестная работа", "Скриншот не подтверждает результат", "Условия изменились"];
